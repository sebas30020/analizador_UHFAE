import numpy as np

from core.grouping import Group
from ui.callbacks.filtering import (
    format_filter_status,
    nearest_group_index,
    resolve_group_selection_indices,
    resolve_map_selection_signal_indices,
    resolve_puntual_selection_indices,
    resolve_timeseries_selection_indices,
)


# --- resolve_timeseries_selection_indices (gráfica #1) ------------------------------
#
# Cada señal ocupa 3 entradas de la traza (mínimo, máximo, separador NaN), así que la
# señal del punto ``pointNumber`` es ``pointNumber // 3``. Las señales dibujadas son las
# de ``valid_mask & active_mask``, en ese orden -- aquí, las globales 4, 9 y 11.

_ACTIVE_SIGNALS = np.array([4, 9, 11])


def _envelope_point(position: int) -> dict:
    return {"curveNumber": 0, "pointNumber": position, "x": 1.0, "y": 0.5}


def test_timeseries_selection_maps_segment_position_to_signal():
    # Posiciones 0 (mínimo de la señal 4) y 3 (mínimo de la señal 9).
    assert resolve_timeseries_selection_indices(
        [_envelope_point(0), _envelope_point(3)], _ACTIVE_SIGNALS
    ) == [4, 9]


def test_timeseries_selection_min_and_max_of_same_signal_yield_one_index():
    # Un lazo que encierra el segmento completo captura sus dos extremos (3k y 3k+1):
    # es UNA señal, no dos.
    assert resolve_timeseries_selection_indices(
        [_envelope_point(3), _envelope_point(4)], _ACTIVE_SIGNALS
    ) == [9]


def test_timeseries_selection_only_max_endpoint_still_selects_the_signal():
    # Lazo alto que solo alcanza los máximos: la señal igual queda seleccionada.
    assert resolve_timeseries_selection_indices([_envelope_point(7)], _ACTIVE_SIGNALS) == [11]


def test_timeseries_selection_ignores_environmental_traces():
    # Temperatura/humedad son curveNumber 1 y 2 y NO son señales -- un lazo que las roce
    # no debe excluir señales por una correspondencia de índices sin significado.
    points = [
        {"curveNumber": 1, "pointNumber": 0},
        {"curveNumber": 2, "pointNumber": 1},
        _envelope_point(0),
    ]
    assert resolve_timeseries_selection_indices(points, _ACTIVE_SIGNALS) == [4]


def test_timeseries_selection_out_of_range_position_is_skipped():
    assert resolve_timeseries_selection_indices([_envelope_point(999)], _ACTIVE_SIGNALS) == []


def test_timeseries_selection_empty_returns_empty():
    assert resolve_timeseries_selection_indices([], _ACTIVE_SIGNALS) == []
    assert resolve_timeseries_selection_indices(None, _ACTIVE_SIGNALS) == []


def test_timeseries_selection_respects_amplitude_not_just_time_span():
    # Regresión del bug reportado: un lazo ancho en tiempo pero que solo encierra
    # algunos segmentos NO debe excluir todo lo que cae en esa franja temporal. Aquí el
    # usuario encerró solo la señal 9, aunque 4 y 11 estén dentro del mismo rango de X.
    assert resolve_timeseries_selection_indices([_envelope_point(3)], _ACTIVE_SIGNALS) == [9]


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
