"""Lógica pura de resolución de selección → índices a excluir (Fase 6, PROMPT §7).

Sin dependencia de Dash, testeable con diccionarios/arrays sintéticos que imitan la
forma de ``selectedData`` de Plotly -- mismo espíritu que ``ui/callbacks/helpers.py``.
Los ``@app.callback`` en ``ui/callbacks/sensor_window_callbacks.py`` son envoltorios
delgados sobre estas funciones.
"""
from __future__ import annotations

import numpy as np

from core.grouping import Group
from ui.components.time_axis import elapsed_minutes_to_unix_seconds


def resolve_timeseries_selection_range(selected_data: dict | None) -> tuple[float, float] | None:
    """Extrae el rango de X (minutos transcurridos) de una selección en la gráfica #1.

    La gráfica #1 se resuelve SIEMPRE por rango de tiempo, nunca por punto individual:
    aunque desde la Fase 7 la envolvente se dibuja sin diezmar (1 segmento = 1 señal
    real), cada señal aporta TRES entradas a la traza (min, max y el separador ``None``
    de ``build_vertical_segments``), así que el índice de punto de Plotly tampoco es el
    índice de señal. Selección rectangular trae ``range.x``; lazo trae ``lassoPoints.x``
    (los vértices del polígono, sin bounding-box precalculado) -- se usa su min/max.
    """
    if not selected_data:
        return None
    x_range = (selected_data.get("range") or {}).get("x")
    if x_range:
        return float(min(x_range)), float(max(x_range))
    lasso_x = (selected_data.get("lassoPoints") or {}).get("x")
    if lasso_x:
        return float(min(lasso_x)), float(max(lasso_x))
    return None


def indices_in_time_range(timestamps: np.ndarray, x0: float, x1: float) -> np.ndarray:
    """Índices crudos (posición en ``block.timestamps``) cuyo timestamp cae en ``[x0, x1]``."""
    return np.where((timestamps >= x0) & (timestamps <= x1))[0]


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
