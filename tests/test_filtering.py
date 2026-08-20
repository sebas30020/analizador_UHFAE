import numpy as np

from core.grouping import Group
from ui.callbacks.filtering import (
    format_filter_status,
    indices_in_time_range,
    nearest_group_index,
    resolve_group_selection_indices,
    resolve_map_selection_signal_indices,
    resolve_puntual_selection_indices,
    resolve_timeseries_selection_range,
)


# --- resolve_timeseries_selection_range (gráfica #1) -------------------------------

def test_resolve_timeseries_selection_box_select_uses_range():
    selected = {"points": [], "range": {"x": [3.0, 1.0], "y": [-1, 1]}}
    assert resolve_timeseries_selection_range(selected) == (1.0, 3.0)


def test_resolve_timeseries_selection_lasso_uses_bounding_box_of_lassopoints():
    selected = {"points": [], "lassoPoints": {"x": [1.0, 2.5, 1.8], "y": [0, 1, 2]}}
    assert resolve_timeseries_selection_range(selected) == (1.0, 2.5)


def test_resolve_timeseries_selection_empty_or_none_returns_none():
    assert resolve_timeseries_selection_range(None) is None
    assert resolve_timeseries_selection_range({}) is None
    assert resolve_timeseries_selection_range({"points": []}) is None


def test_resolve_timeseries_selection_prefers_range_over_lassopoints_when_both_present():
    # No debería ocurrir en la práctica (son mutuamente excluyentes en selectedData de
    # Plotly), pero fija el comportamiento: range.x tiene prioridad si ambos existen.
    selected = {"range": {"x": [1.0, 2.0]}, "lassoPoints": {"x": [10.0, 20.0]}}
    assert resolve_timeseries_selection_range(selected) == (1.0, 2.0)


# --- indices_in_time_range -----------------------------------------------------------

def test_indices_in_time_range_inclusive_bounds():
    timestamps = np.array([0.5, 1.0, 1.5, 3.0, 4.0])
    result = indices_in_time_range(timestamps, 1.0, 3.0)
    assert list(result) == [1, 2, 3]


def test_indices_in_time_range_no_match_returns_empty():
    timestamps = np.array([0.5, 1.0])
    result = indices_in_time_range(timestamps, 10.0, 20.0)
    assert result.shape[0] == 0


# --- nearest_group_index --------------------------------------------------------------

def test_nearest_group_index_picks_closest_center():
    centers = np.array([30.0, 450.0, 900.0])
    assert nearest_group_index(centers, 449.9999) == 1
    assert nearest_group_index(centers, 31.0) == 0
    assert nearest_group_index(centers, 899.0) == 2


def test_nearest_group_index_clamps_at_edges():
    centers = np.array([30.0, 450.0])
    assert nearest_group_index(centers, -100.0) == 0
    assert nearest_group_index(centers, 10_000.0) == 1


# --- resolve_group_selection_indices --------------------------------------------------

def _group(start_idx: int, end_idx: int, center_timestamp: float) -> Group:
    return Group(index=0, start_idx=start_idx, end_idx=end_idx, T_w=60.0,
                 center_timestamp=center_timestamp, is_partial=False)


def test_resolve_group_selection_indices_expands_to_full_group_range():
    # Grupo intermedio (start_idx=3..6) fue omitido por el motor (sin señales
    # válidas+activas) -- el punto seleccionado corresponde al grupo siguiente (6..9),
    # no debe caer erróneamente en el omitido.
    groups = [_group(0, 3, 30.0), _group(6, 9, 450.0)]
    selected_points = [{"x": 450.0 / 60.0}]  # x en minutos transcurridos, t0=0
    result = resolve_group_selection_indices(selected_points, groups, t0=0.0)
    assert list(result) == [6, 7, 8]


def test_resolve_group_selection_indices_unions_multiple_points_without_duplicates():
    groups = [_group(0, 2, 30.0), _group(2, 5, 90.0)]
    selected_points = [{"x": 30.0 / 60.0}, {"x": 90.0 / 60.0}, {"x": 30.0 / 60.0}]  # el primero repetido
    result = resolve_group_selection_indices(selected_points, groups, t0=0.0)
    assert list(result) == [0, 1, 2, 3, 4]


def test_resolve_group_selection_indices_empty_selection_or_groups():
    groups = [_group(0, 2, 30.0)]
    assert resolve_group_selection_indices([], groups, t0=0.0).shape[0] == 0
    assert resolve_group_selection_indices([{"x": 1.0}], [], t0=0.0).shape[0] == 0


# --- resolve_puntual_selection_indices -------------------------------------------------

def test_resolve_puntual_selection_indices_uses_injected_nearest_fn():
    calls = []

    def fake_nearest(seconds: float) -> int:
        calls.append(seconds)
        return int(seconds // 60)  # 1 índice por minuto, solo para el test

    selected_points = [{"x": 1.0}, {"x": 2.0}]
    result = resolve_puntual_selection_indices(selected_points, t0=0.0, nearest_index_fn=fake_nearest)
    assert result == [1, 2]
    assert calls == [60.0, 120.0]


def test_resolve_puntual_selection_indices_dedupes_preserving_first_occurrence_order():
    def fake_nearest(seconds: float) -> int:
        return int(seconds // 60)

    selected_points = [{"x": 2.0}, {"x": 1.0}, {"x": 2.0}]
    result = resolve_puntual_selection_indices(selected_points, t0=0.0, nearest_index_fn=fake_nearest)
    assert result == [2, 1]


def test_resolve_puntual_selection_indices_empty_selection():
    assert resolve_puntual_selection_indices([], t0=0.0, nearest_index_fn=lambda s: 0) == []


# --- resolve_map_selection_signal_indices (lazo/caja en el mapa 2D, #4) ----------------
#
# Igual que el click: por curveNumber+pointNumber, nunca por customdata (ver
# ui/components/graph_map_common.py). Payloads con la forma real del navegador.

_MAP_SIGNAL_INDICES = np.array([40, 12, 7, 5, 9])


def test_resolve_map_selection_signal_indices_maps_positions_to_signal_indices():
    selected_points = [
        {"curveNumber": 0, "pointNumber": 0, "x": 0.1, "y": 0.2},
        {"curveNumber": 0, "pointNumber": 1, "x": 0.3, "y": 0.4},
    ]
    assert resolve_map_selection_signal_indices(selected_points, _MAP_SIGNAL_INDICES) == [40, 12]


def test_resolve_map_selection_signal_indices_dedupes_preserving_first_occurrence_order():
    selected_points = [
        {"curveNumber": 0, "pointNumber": 3},
        {"curveNumber": 0, "pointNumber": 4},
        {"curveNumber": 0, "pointNumber": 3},
    ]
    assert resolve_map_selection_signal_indices(selected_points, _MAP_SIGNAL_INDICES) == [5, 9]


def test_resolve_map_selection_signal_indices_skips_highlight_trace_points():
    # Puntos de la traza de resaltado (curveNumber=1) que caen dentro del lazo -- se
    # descartan sin perder la señal, que ya viene en la traza de puntos.
    selected_points = [
        {"curveNumber": 1, "pointNumber": 0},
        {"curveNumber": 0, "pointNumber": 2},
    ]
    assert resolve_map_selection_signal_indices(selected_points, _MAP_SIGNAL_INDICES) == [7]


def test_resolve_map_selection_signal_indices_empty_selection():
    assert resolve_map_selection_signal_indices([], _MAP_SIGNAL_INDICES) == []
    assert resolve_map_selection_signal_indices(None, _MAP_SIGNAL_INDICES) == []


# --- format_filter_status ---------------------------------------------------------------

def test_format_filter_status():
    assert format_filter_status(8, 10, 2) == "8/10 señales activas · 2 filtro(s) aplicado(s)"
