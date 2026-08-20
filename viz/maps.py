"""Datos de los mapas de separación #4 (2D) y #5 (3D) —
``archivos_md/prompt-mapas2d3d.md``, ``archivos_md/PLAN_MAPAS_2D_3D.md``.

**No reimplementa el cálculo de métricas.** Cada eje se obtiene vía
``cache.service.get_or_compute_puntual`` — el mismo camino que ya usan las gráficas
tipo #3 en régimen puntual — así que un punto del mapa usa exactamente el mismo valor
que el usuario ya ve graficado en el tiempo para esa métrica.

Solo régimen **puntual**: es el único que produce un escalar por señal ("un punto = una
señal", requisito crítico del prompt). Los regímenes de grupo producen un valor por
ventana de agrupamiento y quedan fuera de los selectores de eje (decisión D3 del plan).

Alineación entre ejes: ``get_or_compute_puntual`` siempre consulta/persiste el caché
enmascarando solo por ``valid_mask`` y aplica ``active_mask`` como recorte posterior
puro (``cache/service.py``) — el orden resultante es siempre
``np.where(block.valid_mask & active_mask)[0]`` ascendente. Dos métricas puntuales
distintas con el mismo ``active_mask`` devuelven arrays alineados índice a índice sin
buscar por timestamp; ese mismo orden es el que reproduce ``_signal_indices`` aquí.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cache.backend import CacheBackend
from cache.service import get_or_compute_puntual
from core.models import SensorConfig, SignalBlock
from metrics.registry import get_metric


@dataclass(frozen=True)
class MapDataset:
    """Datos ya resueltos para un mapa de separación (2 o 3 ejes).

    ``signal_indices``: índice global de señal (posición en ``block.data``) de cada
    punto, en el mismo orden que ``coords`` — es lo que necesitan el tooltip, el click
    hacia la Gráfica #2 y el lazo hacia el filtrado, sin volver a buscar por timestamp.

    ``coords``: ``nombre_de_eje -> (K,)`` valores de esa métrica, ya sin NaN/inf.

    ``omitted_counts``: ``nombre_de_eje -> nº de señales`` cuyo valor no era finito para
    esa métrica específica (antes de combinar ejes) — insumo del aviso "N señales sin
    valor para <métrica>" que exige el prompt.

    ``axis_labels``: ``nombre_de_eje -> "Label (unidad)"`` para el título de los ejes de
    la figura.
    """

    signal_indices: np.ndarray
    coords: dict[str, np.ndarray]
    omitted_counts: dict[str, int]
    axis_labels: dict[str, str]


def _signal_indices(block: SignalBlock, active_mask: np.ndarray | None) -> np.ndarray:
    """Índices globales de señal en el orden exacto que produce
    ``get_or_compute_puntual`` con la misma ``active_mask`` — ver docstring del módulo."""
    mask = block.valid_mask if active_mask is None else (block.valid_mask & active_mask)
    return np.where(mask)[0]


def build_map_dataset(
    cache: CacheBackend,
    block: SignalBlock,
    sensor_config: SensorConfig,
    dataset_id: str,
    metric_ids: dict[str, str],
    active_mask: np.ndarray | None = None,
) -> MapDataset:
    """Construye los datos de un mapa a partir de las métricas ya asignadas a sus ejes.

    ``metric_ids``: ``nombre_de_eje -> metric_id`` (p. ej. ``{"x": "rms", "y":
    "kurtosis"}`` para el mapa 2D, o con ``"z"`` además para el 3D). Cada ``metric_id``
    debe ser de régimen puntual — es responsabilidad del llamador (el catálogo de la UI,
    decisión D3) ofrecer solo esas; aquí se valida por seguridad, no se filtra en
    silencio.

    Una señal se omite del mapa si **cualquiera** de los ejes seleccionados no tiene
    valor finito para ella (NaN/inf) — un punto con una coordenada indefinida no se
    puede ubicar. ``omitted_counts`` reporta, por eje, cuántas señales carecían de valor
    finito para esa métrica en particular, antes de combinar ejes.
    """
    if not metric_ids:
        raise ValueError("build_map_dataset requiere al menos un eje en 'metric_ids'")

    for axis_name, metric_id in metric_ids.items():
        definition = get_metric(metric_id)
        if definition.regimen != "puntual":
            raise ValueError(
                f"Eje '{axis_name}': '{metric_id}' es régimen '{definition.regimen}', "
                "los mapas de separación solo aceptan métricas de régimen 'puntual'"
            )

    all_signal_indices = _signal_indices(block, active_mask)

    raw_values: dict[str, np.ndarray] = {}
    axis_labels: dict[str, str] = {}
    omitted_counts: dict[str, int] = {}
    for axis_name, metric_id in metric_ids.items():
        definition = get_metric(metric_id)
        values = get_or_compute_puntual(
            cache, block, sensor_config, dataset_id, metric_id, active_mask=active_mask
        )[1]
        raw_values[axis_name] = values
        axis_labels[axis_name] = f"{definition.label} ({definition.unit})" if definition.unit else definition.label
        omitted_counts[axis_name] = int(np.count_nonzero(~np.isfinite(values)))

    n = all_signal_indices.shape[0]
    keep = np.ones(n, dtype=bool)
    for values in raw_values.values():
        keep &= np.isfinite(values)

    signal_indices = all_signal_indices[keep]
    coords = {axis_name: values[keep] for axis_name, values in raw_values.items()}

    return MapDataset(
        signal_indices=signal_indices,
        coords=coords,
        omitted_counts=omitted_counts,
        axis_labels=axis_labels,
    )


def resolve_map_highlight_coords(dataset: MapDataset, selected_signal_index: int | None) -> dict[str, list]:
    """Coordenadas de la traza de resaltado del mapa (§5.3 del prompt: la señal
    seleccionada aparece resaltada en #4 y #5 simultáneamente): una fila si
    ``selected_signal_index`` está presente en ``dataset.signal_indices`` (sin
    filtrar/omitir), vacía si no -- sin selección, señal excluida por el filtrado, o con
    NaN/inf en algún eje de este mapa.

    Mismas claves que ``dataset.coords`` (``{"x","y"}`` o ``{"x","y","z"}``), para que el
    llamador arme la traza de resaltado sin distinguir 2D de 3D. Reutilizada tanto por
    la construcción inicial de la figura (``ui/components/graph_map_2d.py``,
    ``graph_map_3d.py``) como por el parche ligero de navegación
    (``ui/callbacks/sensor_window_callbacks.py::_on_refresh_map_highlight``) -- una sola
    implementación de "dónde está la señal seleccionada dentro de este mapa".
    """
    if selected_signal_index is not None:
        pos = np.where(dataset.signal_indices == selected_signal_index)[0]
        if pos.size > 0:
            i = int(pos[0])
            return {axis: [float(values[i])] for axis, values in dataset.coords.items()}
    return {axis: [] for axis in dataset.coords}
