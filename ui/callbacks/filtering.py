"""Lógica pura de resolución de selección → índices a excluir (Fase 6, PROMPT §7).

Sin dependencia de Dash, testeable con diccionarios/arrays sintéticos que imitan la
forma de ``selectedData`` de Plotly -- mismo espíritu que ``ui/callbacks/helpers.py``.
Los ``@app.callback`` en ``ui/callbacks/sensor_window_callbacks.py`` son envoltorios
delgados sobre estas funciones.
"""
from __future__ import annotations

from typing import Any

import numpy as np

from core.grouping import Group
from ui.callbacks.helpers import resolve_map_point_signal_index
from ui.components.time_axis import elapsed_minutes_to_unix_seconds
from viz.decimation import ENTRIES_PER_SEGMENT


# La envolvente es la PRIMERA traza de la gráfica #1 (``ui/components/graph_timeseries.py``);
# después van temperatura y humedad sobre el eje secundario, los eventos, y por último la
# traza ancla del lazo. Filtrar por ``curveNumber`` evita que un lazo que roce cualquiera
# de esas -- ninguna es una señal -- acabe excluyendo señales por una correspondencia de
# índices que no significa nada.
TIMESERIES_ENVELOPE_CURVE = 0


def intersect_lasso_segments(
    t: np.ndarray,
    y_min: np.ndarray,
    y_max: np.ndarray,
    poly_x: list[float] | np.ndarray,
    poly_y: list[float] | np.ndarray,
) -> np.ndarray:
    """Máscara booleana 1D de señales cuyos segmentos verticales [y_min, y_max] en t
    intersectan el polígono cerrado (poly_x, poly_y).

    Un segmento vertical [y_min, y_max] en t_i se selecciona si:
    1. Su extremo superior (t_i, y_max) está dentro o sobre el borde del polígono (ray casting).
    2. Su extremo inferior (t_i, y_min) está dentro o sobre el borde del polígono (ray casting).
    3. Alguna arista del polígono cruza o toca el segmento vertical en y_cross in [y_min, y_max].
    4. Alguna arista vertical del polígono en x = t_i se solapa con [y_min, y_max].
    """
    px = np.asarray(poly_x, dtype=np.float64)
    py = np.asarray(poly_y, dtype=np.float64)
    n_signals = t.shape[0]
    if n_signals == 0 or px.shape[0] < 3 or py.shape[0] < 3:
        return np.zeros(n_signals, dtype=bool)

    if px[0] != px[-1] or py[0] != py[-1]:
        px = np.append(px, px[0])
        py = np.append(py, py[0])

    p_xmin, p_xmax = float(np.min(px)), float(np.max(px))
    p_ymin, p_ymax = float(np.min(py)), float(np.max(py))

    # 1. Filtro rápido de Bounding Box
    cand_mask = (t >= p_xmin) & (t <= p_xmax) & (y_min <= p_ymax) & (y_max >= p_ymin)
    if not np.any(cand_mask):
        return np.zeros(n_signals, dtype=bool)

    cand_indices = np.where(cand_mask)[0]
    tc = t[cand_indices, None]       # Shape (C, 1)
    ymc = y_min[cand_indices, None]  # Shape (C, 1)
    yMc = y_max[cand_indices, None]  # Shape (C, 1)

    # 2. Vértices y aristas del polígono
    x1, y1 = px[:-1][None, :], py[:-1][None, :]  # Shape (1, K)
    x2, y2 = px[1:][None, :], py[1:][None, :]    # Shape (1, K)

    dx = x2 - x1
    is_vert = (dx == 0.0)
    safe_dx = np.where(is_vert, 1.0, dx)

    # 3. Ray casting hacia +Y (half-open [x1, x2) o [x2, x1))
    crosses_x = ((x1 <= tc) & (tc < x2)) | ((x2 <= tc) & (tc < x1))
    y_cross = y1 + (tc - x1) * (y2 - y1) / safe_dx

    # Test punto-en-polígono para extremos
    in_poly_ymin = (np.sum(crosses_x & (y_cross > ymc), axis=1) % 2 == 1)
    in_poly_ymax = (np.sum(crosses_x & (y_cross > yMc), axis=1) % 2 == 1)

    # 4. Intersección de aristas no verticales con el cuerpo del segmento
    x_min_edge = np.minimum(x1, x2)
    x_max_edge = np.maximum(x1, x2)
    on_x_span = (x_min_edge <= tc) & (tc <= x_max_edge) & (~is_vert)
    edge_hits = np.any(on_x_span & (y_cross >= ymc) & (y_cross <= yMc), axis=1)

    # 5. Solape con aristas verticales
    ey_min = np.minimum(y1, y2)
    ey_max = np.maximum(y1, y2)
    vert_hits = np.any(
        is_vert & (tc == x1) & (np.maximum(ymc, ey_min) <= np.minimum(yMc, ey_max)),
        axis=1,
    )

    hits = in_poly_ymin | in_poly_ymax | edge_hits | vert_hits
    result_mask = np.zeros(n_signals, dtype=bool)
    result_mask[cand_indices[hits]] = True
    return result_mask


def intersect_range_segments(
    t: np.ndarray,
    y_min: np.ndarray,
    y_max: np.ndarray,
    range_x: list[float] | tuple[float, ...],
    range_y: list[float] | tuple[float, ...],
) -> np.ndarray:
    """Máscara booleana 1D para selección rectangular (Box Select)."""
    n_signals = t.shape[0]
    if n_signals == 0:
        return np.zeros(n_signals, dtype=bool)

    rx0, rx1 = min(range_x[0], range_x[1]), max(range_x[0], range_x[1])
    ry0, ry1 = min(range_y[0], range_y[1]), max(range_y[0], range_y[1])

    return (t >= rx0) & (t <= rx1) & (y_min <= ry1) & (y_max >= ry0)


def _resolve_points_fallback(
    points: list[dict[str, Any]],
    active_signal_indices: np.ndarray,
) -> np.ndarray:
    """Fallback legacy: mapea lista de puntos vía pointNumber // ENTRIES_PER_SEGMENT."""
    if not points or active_signal_indices.shape[0] == 0:
        return np.array([], dtype=np.int64)
    seen: set[int] = set()
    result: list[int] = []
    for point in points:
        if point.get("curveNumber") != TIMESERIES_ENVELOPE_CURVE:
            continue
        position = point.get("pointNumber")
        if position is None:
            position = point.get("pointIndex")
        if position is None:
            continue
        segment = int(position) // ENTRIES_PER_SEGMENT
        if segment < 0 or segment >= active_signal_indices.shape[0]:
            continue
        idx = int(active_signal_indices[segment])
        if idx not in seen:
            seen.add(idx)
            result.append(idx)
    return np.array(result, dtype=np.int64)


def resolve_timeseries_selection_indices(
    selected_data: dict[str, Any] | list[dict[str, Any]] | None,
    active_signal_indices: np.ndarray,
    timestamps_minutes: np.ndarray | None = None,
    minmax: np.ndarray | None = None,
    is_decimated: bool = False,
) -> np.ndarray:
    """Índices globales de señal seleccionados con lazo/caja en la gráfica #1 (Fase 2 / R1).

    Resuelve en el servidor de forma vectorizada en NumPy:
    1. Si `selected_data` contiene `lassoPoints`, calcula la intersección exacta de cada
       segmento vertical [y_min, y_max] con el polígono (incluyendo extremos dentro y
       cortes a través del cuerpo del segmento).
    2. Si contiene `range`, calcula la intersección con el rectángulo en [rx0, rx1] x [ry0, ry1].
    3. Si no hay coordenadas geométricas o faltan arrays temporales/minmax, cae en el
       fallback de compatibilidad por lista de `points` (``pointNumber // ENTRIES_PER_SEGMENT``).
       Si la envolvente viene diezmada (``is_decimated=True``), el fallback por puntos se
       desactiva estrictamente para evitar indexaciones erróneas sobre bins de píxel.

    Retorna un array 1D de np.ndarray (dtype=int64) con los índices globales de señal.
    """
    if selected_data is None or active_signal_indices.shape[0] == 0:
        return np.array([], dtype=np.int64)

    if isinstance(selected_data, dict):
        has_coords = (
            timestamps_minutes is not None
            and minmax is not None
            and timestamps_minutes.shape[0] == active_signal_indices.shape[0]
            and minmax.shape[0] == active_signal_indices.shape[0]
        )

        lasso = selected_data.get("lassoPoints")
        if isinstance(lasso, dict) and has_coords:
            lx = lasso.get("x") or lasso.get("xaxis")
            ly = lasso.get("y") or lasso.get("yaxis")
            if (
                lx is not None
                and ly is not None
                and len(lx) >= 3
                and len(ly) >= 3
                and timestamps_minutes is not None
                and minmax is not None
            ):
                mask = intersect_lasso_segments(
                    timestamps_minutes, minmax[:, 0], minmax[:, 1], lx, ly
                )
                return active_signal_indices[mask].astype(np.int64)

        rng = selected_data.get("range")
        if isinstance(rng, dict) and has_coords:
            rx = rng.get("x") or rng.get("xaxis")
            ry = rng.get("y") or rng.get("yaxis")
            if (
                rx is not None
                and ry is not None
                and len(rx) == 2
                and len(ry) == 2
                and timestamps_minutes is not None
                and minmax is not None
            ):
                mask = intersect_range_segments(
                    timestamps_minutes, minmax[:, 0], minmax[:, 1], rx, ry
                )
                return active_signal_indices[mask].astype(np.int64)

        points = selected_data.get("points")
        if isinstance(points, list):
            if is_decimated:
                return np.array([], dtype=np.int64)
            return _resolve_points_fallback(points, active_signal_indices)
        return np.array([], dtype=np.int64)

    if isinstance(selected_data, list):
        if is_decimated:
            return np.array([], dtype=np.int64)
        return _resolve_points_fallback(selected_data, active_signal_indices)

    return np.array([], dtype=np.int64)


def nearest_group_index(center_timestamps: np.ndarray, timestamp: float) -> int:
    """Índice (en ``center_timestamps``) del grupo cuyo centro está más cerca de
    ``timestamp``. Mismo patrón *searchsorted + comparar vecino* que
    ``AppState.nearest_index_for_timestamp``. Precondición: ``center_timestamps`` no
    vacío y ordenado ascendente (garantizado por construcción, ver
    ``core/grouping.py``: los centros de un mismo ``resolve_groups`` son estrictamente
    monótonos)."""
    idx = int(np.searchsorted(center_timestamps, timestamp))
    idx = min(idx, center_timestamps.shape[0] - 1)
    if idx > 0 and abs(center_timestamps[idx - 1] - timestamp) <= abs(center_timestamps[idx] - timestamp):
        idx -= 1
    return idx


def resolve_group_selection_indices(selected_points: list[dict], groups: list[Group], t0: float) -> np.ndarray:
    """Expande cada punto seleccionado de una gráfica de métrica de grupo a TODAS las
    señales crudas del grupo al que pertenece (PROMPT §7.2: "se excluyen todas las
    señales del grupo"). Empareja por coincidencia MÁS CERCANA de ``center_timestamp``
    (no exacta): el ``x`` mostrado ya pasó por la conversión a minutos transcurridos
    (``ui/components/time_axis.py``), con pérdida de precisión de punto flotante en el
    redondeo -- inofensivo en la práctica porque ese ruido (~1e-7 s) es muchos órdenes de
    magnitud menor que cualquier ``grouping-value`` realista (segundos o más).
    """
    if not selected_points or not groups:
        return np.array([], dtype=np.int64)

    center_timestamps = np.array([g.center_timestamp for g in groups])
    exclude: set[int] = set()
    for point in selected_points:
        seconds = elapsed_minutes_to_unix_seconds(float(point["x"]), t0)
        g = groups[nearest_group_index(center_timestamps, seconds)]
        exclude.update(range(g.start_idx, g.end_idx))
    return np.array(sorted(exclude), dtype=np.int64)


def format_filter_status(active: int, total: int, n_ops: int) -> str:
    """Texto del indicador permanente de filtrado (PROMPT §7.2: "número de señales
    activas/totales, y número de filtros aplicados")."""
    return f"{active}/{total} señales activas · {n_ops} filtro(s) aplicado(s)"


def resolve_map_selection_signal_indices(
    selected_points: list[dict] | None, signal_indices: np.ndarray
) -> list[int]:
    """Índices globales de señal seleccionados con lazo/caja en el mapa 2D (#4,
    ``archivos_md/prompt-mapas2d3d.md`` §5.4).

    Cada punto se resuelve por posición contra ``signal_indices`` del ``MapDataset`` con
    el que se dibujó el mapa (:func:`ui.callbacks.helpers.resolve_map_point_signal_index`),
    no por ``customdata`` ni por cercanía de timestamp como #1/#3: un punto del mapa ya
    ES una señal. El porqué de no usar ``customdata`` está en
    ``ui/components/graph_map_common.py``.

    Los puntos de la traza de resaltado que caigan dentro del lazo se descartan sin
    perder nada: esa misma señal ya viene en la traza de puntos.
    """
    if not selected_points:
        return []
    seen: set[int] = set()
    result: list[int] = []
    for point in selected_points:
        idx = resolve_map_point_signal_index(point, signal_indices)
        if idx is not None and idx not in seen:
            seen.add(idx)
            result.append(idx)
    return result


def resolve_puntual_selection_indices(selected_points: list[dict], t0: float, nearest_index_fn) -> list[int]:
    """Por cada punto seleccionado en una gráfica de métrica puntual, resuelve el índice
    de señal cruda más cercano vía ``nearest_index_fn`` (el llamador inyecta
    ``AppState.nearest_index_for_timestamp`` ligado al sensor correcto -- así esta
    función es pura/testeable con un stub). No hay ambigüedad de "grupo omitido" en
    régimen puntual (1 punto graficado = 1 señal real siempre), así que no hace falta
    más que la búsqueda de vecino más cercano que ya usa el click-para-navegar.
    """
    if not selected_points:
        return []
    seen: set[int] = set()
    result: list[int] = []
    for point in selected_points:
        seconds = elapsed_minutes_to_unix_seconds(float(point["x"]), t0)
        idx = nearest_index_fn(seconds)
        if idx not in seen:
            seen.add(idx)
            result.append(idx)
    return result
