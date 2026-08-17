"""Orquestador del motor de métricas (PROMPT maestro §4, §9.1).

Responsabilidades:
- Excluir siempre las señales inválidas (``valid_mask=False``) de todo cálculo (§11).
- Normalizar antes de calcular (§2.4) -- ninguna métrica ve señal cruda.
- Paralelizar el cálculo puntual sobre bloques de filas cuando ``n_workers > 1`` (§9.1).
- Calcular el espectro una sola vez por bloque y compartirlo entre métricas espectrales
  de la misma pasada (§4.3) -- nunca dentro de una métrica individual.
- Régimen "grupo": dos mecanismos (AUDITORIA_FORMULAS_PDF_vs_metricas.md §2) --
  reducción genérica (mediana por defecto) de cualquier métrica puntual, y las 3
  métricas de grupo intrínsecas (tasa_pulsos, tasa_energia, tasa_rafagas) con ``T_w``
  siempre explícito, provisto por ``core/grouping.py`` (§4).

Decisión de alcance (documentada, no un descuido): régimen "grupo" opera **siempre**
sobre el subconjunto de señales válidas dentro de cada grupo -- una señal con metadatos
faltantes no se usa para ninguna métrica, ni puntual ni de grupo (PROMPT §11 no reserva
una excepción para el conteo de grupo). Grupos sin ninguna señal válida se omiten, igual
que una ventana vacía (§4.2).
"""
from __future__ import annotations

import numpy as np
from joblib import Parallel, delayed

from core.grouping import Group
from core.models import SensorConfig, SignalBlock
from core.normalization import normalize
from metrics.registry import MetricContext, get_metric
from metrics.spectral import compute_spectrum


def _effective_mask(valid_mask: np.ndarray, extra_mask: np.ndarray | None) -> np.ndarray:
    """Combina ``valid_mask`` (metadato inválido, ver core/normalization.py) con una
    máscara de exclusión adicional opcional (filtrado interactivo del usuario, Fase 6,
    PROMPT §7) -- ``extra_mask=None`` reproduce el comportamiento sin filtro byte a byte.
    Único punto de combinación para que las dos variables de la rama
    ``requires_global_timestamps`` de :func:`compute_group_reduction` (que deben
    construirse con la MISMA máscara o quedan desalineadas entre sí) nunca diverjan.
    """
    return valid_mask if extra_mask is None else (valid_mask & extra_mask)


def _split_row_ranges(n_rows: int, n_workers: int, block_size: int | None = None) -> list[tuple[int, int]]:
    """Divide ``[0, n_rows)`` en rangos contiguos.

    Con ``block_size`` (bloqueo por memoria, ver ``SensorConfig.block_n_signals``):
    rangos de a lo sumo ``block_size`` filas, en orden -- su cantidad no depende de
    ``n_workers``. Sin ``block_size`` (comportamiento histórico): ``n_workers`` rangos
    de tamaño ~igual.
    """
    if block_size is not None and block_size > 0:
        return [(start, min(start + block_size, n_rows)) for start in range(0, n_rows, block_size)]
    n_workers = max(1, min(n_workers, n_rows)) if n_rows > 0 else 1
    edges = np.linspace(0, n_rows, n_workers + 1, dtype=np.int64)
    return [(int(edges[i]), int(edges[i + 1])) for i in range(n_workers) if edges[i] < edges[i + 1]]


def _compute_puntual_chunk(
    definition_id: str,
    data_chunk: np.ndarray,
    vrange_chunk: np.ndarray,
    requires_spectrum: bool,
    fs_hz: float,
    freq_limit_hz: float,
    params: dict,
) -> np.ndarray:
    # Reimporta el registro dentro del worker: en el backend "loky" de joblib cada
    # proceso hijo arranca en frío y necesita repoblar el registro de métricas. Normaliza
    # y calcula el espectro AQUÍ (no en el proceso padre) para que solo cruce el pickle
    # el bloque crudo -- del tamaño acotado por SensorConfig.block_n_signals -- y nunca
    # la matriz normalizada completa del sensor.
    from metrics.registry import discover_metrics
    from metrics.spectral import compute_spectrum

    discover_metrics()
    definition = get_metric(definition_id)
    normalized = normalize(data_chunk, vrange_chunk)
    spectrum = compute_spectrum(normalized, fs_hz, freq_limit_hz) if requires_spectrum else None
    ctx = MetricContext(signal_matrix=normalized, spectrum=spectrum, fs_hz=fs_hz)
    return np.asarray(definition.compute(ctx, **params))


def compute_puntual(
    block: SignalBlock,
    sensor_config: SensorConfig,
    metric_id: str,
    params: dict | None = None,
    n_workers: int = 1,
    extra_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Calcula una métrica de régimen "puntual" sobre todas las señales válidas de
    ``block``. Retorna ``(timestamps, values)`` alineados 1 a 1, ordenados
    cronológicamente, solo para señales válidas.

    ``extra_mask`` (Fase 6, PROMPT §7): máscara de exclusión adicional del filtrado
    interactivo del usuario, combinada con ``valid_mask`` vía :func:`_effective_mask`.
    ``None`` (default) reproduce el comportamiento sin filtro.

    ``n_workers``: **medido en datos reales, no supuesto.** Con ``n_workers=1`` (serial,
    default), RMS sobre las 20 574 señales AE de ``med_5_ago_3.hdf5`` tarda ~3.4 s. Con
    ``n_workers=8`` tardó ~39 s -- **más lento**, no más rápido. El costo de arrancar
    procesos ``loky`` en Windows (spawn, sin fork) y reimportar numpy/scipy/h5py y
    redescubrir el registro de métricas en cada proceso hijo domina por completo sobre
    el trabajo vectorizado real, que ya es barato. Con los tamaños de dataset actuales
    del proyecto, ``n_workers>1`` es contraproducente; se deja implementado y probado
    (``test_compute_puntual_parallel_matches_serial``) porque el PROMPT exige que el
    nivel de paralelismo sea configurable, pero el valor por defecto debe seguir siendo
    serial hasta medir un caso (dataset mucho mayor, o métrica mucho más costosa como
    CWT) donde el paralelismo compense su propio costo de arranque -- ver
    FASE2_ENTREGA.md, riesgos de rendimiento.
    """
    definition = get_metric(metric_id)
    if definition.regimen != "puntual":
        raise ValueError(f"'{metric_id}' es régimen '{definition.regimen}', no 'puntual'")
    params = dict(params or {})

    mask = _effective_mask(block.valid_mask, extra_mask)
    valid_idx = np.where(mask)[0]
    if valid_idx.size == 0:
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)

    valid_timestamps = block.timestamps[valid_idx]

    if definition.requires_global_timestamps:
        # No usa signal_matrix -- ctx.timestamps ya está filtrado a señales válidas,
        # así Δt_i siempre mide contra el pulso válido inmediatamente anterior.
        ctx = MetricContext(timestamps=valid_timestamps, fs_hz=sensor_config.fs_hz)
        values = np.asarray(definition.compute(ctx, **params))
        return valid_timestamps, values

    # Fancy indexing con enteros SIEMPRE copia, incluso cuando no excluye nada (caso
    # normal: todas las señales válidas). normalize() no muta su entrada, así que aquí
    # es seguro pasarle block.data directamente y ahorrarse esa copia completa.
    data_subset = block.data if mask.all() else block.data[valid_idx]
    valid_vrange = block.vrange[valid_idx]

    # Cálculo por bloques de tamaño sensor_config.block_n_signals: el pico de memoria de
    # esta función deja de escalar con el número total de señales y pasa a depender solo
    # del tamaño de bloque (~target_block_bytes en config/sensors.yaml). Cada bloque se
    # normaliza y, si la métrica lo requiere, se le calcula el espectro por separado --
    # nunca se materializa la matriz normalizada (ni la FFT) del sensor completo.
    n_rows = data_subset.shape[0]
    row_ranges = _split_row_ranges(n_rows, n_workers, block_size=sensor_config.block_n_signals)

    if n_workers <= 1 or len(row_ranges) <= 1:
        chunks = []
        for start, stop in row_ranges:
            block_normalized = normalize(data_subset[start:stop], valid_vrange[start:stop])
            spectrum = (
                compute_spectrum(block_normalized, sensor_config.fs_hz, sensor_config.freq_limit_hz)
                if definition.requires_spectrum
                else None
            )
            ctx = MetricContext(signal_matrix=block_normalized, spectrum=spectrum, fs_hz=sensor_config.fs_hz)
            chunks.append(np.asarray(definition.compute(ctx, **params)))
    else:
        chunks = Parallel(n_jobs=min(n_workers, len(row_ranges)))(
            delayed(_compute_puntual_chunk)(
                metric_id,
                data_subset[start:stop],
                valid_vrange[start:stop],
                definition.requires_spectrum,
                sensor_config.fs_hz,
                sensor_config.freq_limit_hz,
                params,
            )
            for start, stop in row_ranges
        )

    values = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float64)
    return valid_timestamps, values


def _reduce(values: np.ndarray, reducer: str, percentile_q: float) -> float:
    finite = values[~np.isnan(values)] if np.issubdtype(values.dtype, np.floating) else values
    if finite.size == 0:
        return float("nan")
    if reducer == "median":
        return float(np.median(finite))
    if reducer == "mean":
        return float(np.mean(finite))
    if reducer == "percentile":
        return float(np.percentile(finite, percentile_q))
    raise ValueError(f"Reductor de grupo desconocido: '{reducer}'")


def compute_group_reduction(
    block: SignalBlock,
    sensor_config: SensorConfig,
    metric_id: str,
    groups: list[Group],
    reducer: str = "median",
    percentile_q: float = 75.0,
    params: dict | None = None,
    extra_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Régimen "grupo" vía reducción genérica de una métrica puntual existente
    (AUDITORIA_FORMULAS_PDF_vs_metricas.md §2 -- cubre las Ecuaciones 12, 14, 21, 28,
    31, 33 del PDF sin necesitar un plugin nuevo por variable).

    Por defecto usa la mediana, como especifica el PDF para todas esas ecuaciones.
    Retorna ``(center_timestamps, values, is_partial)``, uno por grupo con al menos
    una señal válida (los grupos sin señales válidas se omiten, igual que ventana vacía).

    ``extra_mask`` (Fase 6, PROMPT §7): ver :func:`compute_puntual`. Se combina con
    ``valid_mask`` vía :func:`_effective_mask` en TODOS los puntos de enmascarado de
    esta función -- incluida la rama ``requires_global_timestamps``, donde debe usarse
    la misma máscara tanto para la llamada interna a :func:`compute_puntual` como para
    recortar sus resultados por grupo, o ambas quedan desalineadas entre sí.
    """
    definition = get_metric(metric_id)
    if definition.regimen != "puntual":
        raise ValueError(f"compute_group_reduction requiere una métrica 'puntual', '{metric_id}' es '{definition.regimen}'")
    params = dict(params or {})
    mask = _effective_mask(block.valid_mask, extra_mask)

    out_t: list[float] = []
    out_v: list[float] = []
    out_partial: list[bool] = []

    if definition.requires_global_timestamps:
        # Δt_i (y derivadas como log Δt) se definen sobre la secuencia GLOBAL de pulsos
        # válidos (Ec. 11), no reinician en cada ventana: el primer pulso de un grupo
        # sigue midiendo su Δt contra el último pulso válido del grupo anterior. Se
        # calcula una sola vez sobre todo el sensor y se recorta por grupo usando los
        # índices originales -- recomputar "en frío" dentro de cada grupo (como para el
        # resto de métricas puntuales) resetearía Δt=0 al inicio de cada ventana, un
        # error real detectado al construir la prueba contra el ejemplo del PDF (Ec. 12).
        global_valid_idx = np.where(mask)[0]
        _, global_values = compute_puntual(block, sensor_config, metric_id, params=params, extra_mask=extra_mask)
        for g in groups:
            in_group = (global_valid_idx >= g.start_idx) & (global_valid_idx < g.end_idx)
            point_values = global_values[in_group]
            if point_values.size == 0:
                continue
            reduced = _reduce(point_values, reducer, percentile_q)
            if np.isnan(reduced):
                continue
            out_t.append(g.center_timestamp)
            out_v.append(reduced)
            out_partial.append(g.is_partial)
        return np.array(out_t), np.array(out_v), np.array(out_partial, dtype=bool)

    for g in groups:
        idx = np.arange(g.start_idx, g.end_idx)
        group_mask = mask[idx]
        if not group_mask.any():
            continue

        # Igual que en compute_puntual: si ninguna señal del grupo está excluida, el
        # grupo es un rango contiguo -- usar slicing (vista) en vez de fancy indexing
        # evita una copia completa del bloque del grupo.
        if group_mask.all():
            group_data, group_vrange = block.data[g.start_idx:g.end_idx], block.vrange[g.start_idx:g.end_idx]
        else:
            valid_idx = idx[group_mask]
            group_data, group_vrange = block.data[valid_idx], block.vrange[valid_idx]

        sub_normalized = normalize(group_data, group_vrange)
        spectrum = (
            compute_spectrum(sub_normalized, sensor_config.fs_hz, sensor_config.freq_limit_hz)
            if definition.requires_spectrum
            else None
        )
        ctx = MetricContext(signal_matrix=sub_normalized, spectrum=spectrum, fs_hz=sensor_config.fs_hz)

        point_values = np.asarray(definition.compute(ctx, **params), dtype=np.float64)
        reduced = _reduce(point_values, reducer, percentile_q)
        if np.isnan(reduced):
            continue

        out_t.append(g.center_timestamp)
        out_v.append(reduced)
        out_partial.append(g.is_partial)

    return np.array(out_t), np.array(out_v), np.array(out_partial, dtype=bool)


def compute_group_intrinsic(
    block: SignalBlock,
    sensor_config: SensorConfig,
    metric_id: str,
    groups: list[Group],
    params: dict | None = None,
    extra_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Régimen "grupo" intrínseco: tasa_pulsos, tasa_energia, tasa_rafagas (Ec. 15, 10,
    47). ``T_w`` siempre viene del propio ``Group`` (nunca inferido de los timestamps
    observados, ver AUDITORIA_FORMULAS_PDF_vs_metricas.md §4).

    ``extra_mask`` (Fase 6, PROMPT §7): ver :func:`compute_puntual`.
    """
    definition = get_metric(metric_id)
    if definition.regimen != "grupo":
        raise ValueError(f"compute_group_intrinsic requiere una métrica 'grupo', '{metric_id}' es '{definition.regimen}'")
    params = dict(params or {})
    mask = _effective_mask(block.valid_mask, extra_mask)

    out_t: list[float] = []
    out_v: list[float] = []
    out_partial: list[bool] = []

    for g in groups:
        if g.T_w <= 0:
            continue  # ventana degenerada (p. ej. grupo por_cantidad de una sola señal) -> se omite

        idx = np.arange(g.start_idx, g.end_idx)
        valid_idx = idx[mask[idx]]
        if valid_idx.size == 0:
            continue

        sub_timestamps = block.timestamps[valid_idx]
        # Normalización perezosa (Fase 7): ``tasa_pulsos`` y ``tasa_rafagas`` se calculan
        # solo con los timestamps del grupo; materializar su matriz normalizada era el
        # trabajo dominante de esta función y no lo leía nadie. ``tasa_energia`` sí la
        # pide, y la paga exactamente igual que antes al tocar ``ctx.signal_matrix``. El
        # slicing/fancy-indexing de ``block.data`` debe quedar DENTRO del cuerpo de la
        # función (no en un default de argumento) para que siga sin ejecutarse jamás
        # cuando nadie llama a ``_materializar``.
        group_all_valid = valid_idx.size == idx.size

        def _materializar(
            vi: np.ndarray = valid_idx,
            all_valid: bool = group_all_valid,
            start: int = g.start_idx,
            end: int = g.end_idx,
        ) -> np.ndarray:
            # Igual que en compute_puntual/compute_group_reduction: slicing en vez de
            # fancy indexing cuando el grupo no excluye ninguna señal (evita una copia
            # completa del bloque del grupo).
            if all_valid:
                return normalize(block.data[start:end], block.vrange[start:end])
            return normalize(block.data[vi], block.vrange[vi])

        ctx = MetricContext(
            signal_matrix_factory=_materializar,
            timestamps=sub_timestamps,
            fs_hz=sensor_config.fs_hz,
            T_w=g.T_w,
        )
        value = float(definition.compute(ctx, **params))

        out_t.append(g.center_timestamp)
        out_v.append(value)
        out_partial.append(g.is_partial)

    return np.array(out_t), np.array(out_v), np.array(out_partial, dtype=bool)
