"""Lógica pura de resolución de selección → índices a excluir (Fase 6, PROMPT §7).

Sin dependencia de Dash, testeable con diccionarios/arrays sintéticos que imitan la
forma de ``selectedData`` de Plotly -- mismo espíritu que ``ui/callbacks/helpers.py``.
Los ``@app.callback`` en ``ui/callbacks/sensor_window_callbacks.py`` son envoltorios
delgados sobre estas funciones.
"""
from __future__ import annotations

import numpy as np

from core.grouping import Group
from ui.callbacks.helpers import resolve_map_point_signal_index
from ui.components.time_axis import elapsed_minutes_to_unix_seconds
from viz.decimation import ENTRIES_PER_SEGMENT


# La envolvente es la PRIMERA traza de la gráfica #1 (``ui/components/graph_timeseries.py``);
# las de temperatura y humedad van después, sobre el eje secundario. Filtrar por
# ``curveNumber`` evita que un lazo que roce las series ambientales -- que no son señales --
# acabe excluyendo señales por una correspondencia de índices que no significa nada.
TIMESERIES_ENVELOPE_CURVE = 0


def resolve_timeseries_selection_indices(
    selected_points: list[dict] | None, active_signal_indices: np.ndarray
) -> list[int]:
    """Índices globales de señal encerrados con lazo/caja en la gráfica #1.

    Resuelve **señal a señal**, respetando también la amplitud: cada señal se dibuja
    como un segmento vertical de tres entradas (mínimo, máximo, separador ``NaN``, ver
    :func:`viz.decimation.build_vertical_segments`), así que la señal del punto
    ``pointNumber`` es ``pointNumber // ENTRIES_PER_SEGMENT``, y de ahí a índice global
    a través de ``active_signal_indices`` (las señales que esa figura realmente dibujó,
    ``valid_mask & active_mask``, en el mismo orden).

    Sustituye a la resolución por rango de tiempo que existía hasta la rama de los mapas.
    Aquella colapsaba cualquier selección a ``[min(x), max(x)]`` y excluía **todas** las
    señales de esa franja temporal, ignorando por completo lo que el lazo encerraba en
    vertical: con un lazo alto y estrecho el resultado era el correcto por casualidad,
    pero con uno ancho excluía señales que el usuario nunca encerró. Que la gráfica #1
    dibuje segmentos en vez de puntos sueltos no impide resolver por señal -- solo exige
    dividir por el número de entradas que aporta cada una.

    Un lazo que cruce el centro de un segmento sin contener ninguno de sus dos extremos
    no selecciona esa señal: Plotly solo conoce los vértices que dibuja. Es coherente con
    lo que se ve en pantalla (los extremos llevan marcador, ``mode="lines+markers"``) y
    es preferible a la alternativa anterior, que excluía de más sin avisar.
    """
    if not selected_points:
        return []
    seen: set[int] = set()
    result: list[int] = []
    for point in selected_points:
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
    return result


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
