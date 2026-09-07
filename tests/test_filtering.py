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
    assert list(
        resolve_timeseries_selection_indices(
            [_envelope_point(0), _envelope_point(3)], _ACTIVE_SIGNALS
        )
    ) == [4, 9]


def test_timeseries_selection_min_and_max_of_same_signal_yield_one_index():
    # Un lazo que encierra el segmento completo captura sus dos extremos (3k y 3k+1):
    # es UNA señal, no dos.
    assert list(
        resolve_timeseries_selection_indices(
            [_envelope_point(3), _envelope_point(4)], _ACTIVE_SIGNALS
        )
    ) == [9]


def test_timeseries_selection_only_max_endpoint_still_selects_the_signal():
    # Lazo alto que solo alcanza los máximos: la señal igual queda seleccionada.
    assert list(
        resolve_timeseries_selection_indices([_envelope_point(7)], _ACTIVE_SIGNALS)
    ) == [11]


def test_timeseries_selection_ignores_environmental_traces():
    # Temperatura/humedad son curveNumber 1 y 2 y NO son señales -- un lazo que las roce
    # no debe excluir señales por una correspondencia de índices sin significado.
    points = [
        {"curveNumber": 1, "pointNumber": 0},
        {"curveNumber": 2, "pointNumber": 1},
        _envelope_point(0),
    ]
    assert list(resolve_timeseries_selection_indices(points, _ACTIVE_SIGNALS)) == [4]


def test_timeseries_selection_out_of_range_position_is_skipped():
    assert list(
        resolve_timeseries_selection_indices([_envelope_point(999)], _ACTIVE_SIGNALS)
    ) == []


def test_timeseries_selection_empty_returns_empty():
    assert list(resolve_timeseries_selection_indices([], _ACTIVE_SIGNALS)) == []
    assert list(resolve_timeseries_selection_indices(None, _ACTIVE_SIGNALS)) == []


def test_timeseries_selection_respects_amplitude_not_just_time_span():
    # Regresión del bug reportado: un lazo ancho en tiempo pero que solo encierra
    # algunos segmentos NO debe excluir todo lo que cae en esa franja temporal. Aquí el
    # usuario encerró solo la señal 9, aunque 4 y 11 estén dentro del mismo rango de X.
    assert list(
        resolve_timeseries_selection_indices([_envelope_point(3)], _ACTIVE_SIGNALS)
    ) == [9]


# --- Pruebas de selección en servidor (Fase 2 / R1) -----------------------------------

def test_timeseries_selection_lasso_points_endpoints_inside():
    # Señales activas globales [100, 200, 300]
    active = np.array([100, 200, 300])
    t = np.array([1.0, 2.0, 3.0])  # minutos
    minmax = np.array([
        [-1.0, 1.0],   # Señal 100: extremo sup en (1.0, 1.0)
        [-5.0, 0.0],   # Señal 200: extremo inf en (2.0, -5.0)
        [-0.5, 0.5],   # Señal 300: en t=3.0 fuera del lazo
    ])
    # Lazo triangular que encierra (1.0, 1.0) y (2.0, -5.0)
    lasso_payload = {
        "lassoPoints": {
            "x": [0.5, 2.5, 1.5, 0.5],
            "y": [2.0, -6.0, 2.0, 2.0],
        }
    }
    result = resolve_timeseries_selection_indices(
        lasso_payload, active, timestamps_minutes=t, minmax=minmax
    )
    assert isinstance(result, np.ndarray)
    assert list(result) == [100, 200]


def test_timeseries_selection_lasso_cuts_through_segment_middle():
    # Señal cuyo segmento [-10.0, 10.0] en t=1.0 atraviesa el lazo por el centro.
    # Sus extremos (1.0, 10.0) y (1.0, -10.0) quedan FUERA del polígono, pero el
    # cuerpo del segmento cruza las aristas del lazo.
    active = np.array([50, 60])
    t = np.array([1.0, 5.0])
    minmax = np.array([
        [-10.0, 10.0],  # Corta por el medio
        [-1.0, 1.0],    # Fuera en t=5.0
    ])
    # Polígono rectangular estrecho en Y: X in [0.5, 1.5], Y in [-1.0, 1.0]
    lasso_payload = {
        "lassoPoints": {
            "x": [0.5, 1.5, 1.5, 0.5, 0.5],
            "y": [-1.0, -1.0, 1.0, 1.0, -1.0],
        }
    }
    result = resolve_timeseries_selection_indices(
        lasso_payload, active, timestamps_minutes=t, minmax=minmax
    )
    assert list(result) == [50]


def test_timeseries_selection_lasso_rejects_outside_signals():
    active = np.array([1, 2, 3, 4, 5])
    t = np.array([0.0, 2.0, 2.0, 2.0, 10.0])
    minmax = np.array([
        [-1.0, 1.0],   # Señal 1: fuera a la izquierda (t=0.0)
        [5.0, 6.0],    # Señal 2: arriba del polígono
        [-6.0, -5.0],  # Señal 3: abajo del polígono
        [-0.5, 0.5],   # Señal 4: dentro del polígono en t=2.0
        [-1.0, 1.0],   # Señal 5: fuera a la derecha (t=10.0)
    ])
    # Diamante centrado en (2.0, 0.0) de radio 1 en X e Y
    lasso_payload = {
        "lassoPoints": {
            "x": [1.0, 2.0, 3.0, 2.0, 1.0],
            "y": [0.0, 1.0, 0.0, -1.0, 0.0],
        }
    }
    result = resolve_timeseries_selection_indices(
        lasso_payload, active, timestamps_minutes=t, minmax=minmax
    )
    assert list(result) == [4]


def test_timeseries_selection_lasso_concave_c_shape():
    # Polígono cóncavo en forma de 'C'
    # Hueco en X in [1.5, 2.5], Y in [-0.5, 0.5]
    active = np.array([10, 20, 30])
    t = np.array([1.0, 2.0, 3.0])
    minmax = np.array([
        [-1.0, 1.0],   # Señal 10 en t=1.0: dentro de la barra izquierda de la C
        [-0.2, 0.2],   # Señal 20 en t=2.0: en el hueco de la C (no debe seleccionarse)
        [-1.0, 1.0],   # Señal 30 en t=3.0: fuera a la derecha
    ])
    c_poly_x = [0.5, 2.5, 2.5, 1.5, 1.5, 2.5, 2.5, 0.5, 0.5]
    c_poly_y = [2.0, 2.0, 1.0, 1.0, -1.0, -1.0, -2.0, -2.0, 2.0]
    lasso_payload = {
        "lassoPoints": {
            "x": c_poly_x,
            "y": c_poly_y,
        }
    }
    result = resolve_timeseries_selection_indices(
        lasso_payload, active, timestamps_minutes=t, minmax=minmax
    )
    assert list(result) == [10]


def test_timeseries_selection_range_box_selection():
    active = np.array([10, 20, 30, 40])
    t = np.array([1.0, 2.0, 3.0, 5.0])
    minmax = np.array([
        [-0.5, 0.5],    # Señal 10 en t=1.0: fuera en X
        [-1.0, 1.0],    # Señal 20 en t=2.0: completamente dentro de la caja
        [-10.0, 10.0],  # Señal 30 en t=3.0: atraviesa la caja verticalmente
        [-0.5, 0.5],    # Señal 40 en t=5.0: fuera en X
    ])
    range_payload = {
        "range": {
            "x": [1.5, 3.5],
            "y": [-2.0, 2.0],
        }
    }
    result = resolve_timeseries_selection_indices(
        range_payload, active, timestamps_minutes=t, minmax=minmax
    )
    assert list(result) == [20, 30]


def test_timeseries_selection_fallback_to_points_dict():
    # Payload con diccionario que solo contiene "points"
    active = np.array([5, 15, 25])
    payload = {
        "points": [
            {"curveNumber": 0, "pointNumber": 3},  # Señal 1 (global 15)
        ]
    }
    result = resolve_timeseries_selection_indices(payload, active)
    assert list(result) == [15]


def test_timeseries_selection_degenerate_and_empty_payloads():
    active = np.array([1, 2, 3])
    t = np.array([1.0, 2.0, 3.0])
    minmax = np.array([[-1.0, 1.0], [-1.0, 1.0], [-1.0, 1.0]])

    assert list(resolve_timeseries_selection_indices(None, active)) == []
    assert list(resolve_timeseries_selection_indices({}, active)) == []
    assert list(resolve_timeseries_selection_indices({"points": []}, active)) == []
    assert list(
        resolve_timeseries_selection_indices(
            {"lassoPoints": {"x": [1.0, 2.0], "y": [1.0, 2.0]}},  # < 3 puntos
            active,
            timestamps_minutes=t,
            minmax=minmax,
        )
    ) == []
    assert list(
        resolve_timeseries_selection_indices(
            {"range": {"x": [1.0], "y": [2.0]}},  # len != 2
            active,
            timestamps_minutes=t,
            minmax=minmax,
        )
    ) == []
    assert list(
        resolve_timeseries_selection_indices(
            {"lassoPoints": {"x": [1.0, 2.0, 3.0], "y": [1.0, 2.0, 3.0]}},
            np.array([], dtype=int),
        )
    ) == []


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


def test_timeseries_selection_decimated_points_fallback_disabled():
    # When is_decimated=True, points fallback must be strictly disabled
    points = [{"curveNumber": 0, "pointNumber": 0}, {"curveNumber": 0, "pointNumber": 3}]
    result = resolve_timeseries_selection_indices(points, _ACTIVE_SIGNALS, is_decimated=True)
    assert len(result) == 0

    dict_payload = {"points": points}
    result_dict = resolve_timeseries_selection_indices(dict_payload, _ACTIVE_SIGNALS, is_decimated=True)
    assert len(result_dict) == 0


def test_timeseries_selection_decimated_geometric_selection_enabled():
    # Server-side geometry (lasso & range) must work accurately even when is_decimated=True
    active = np.array([10, 20, 30, 40])
    t = np.array([1.0, 2.0, 3.0, 5.0])
    minmax = np.array([
        [-0.5, 0.5],    # 10 at t=1.0: outside
        [-1.0, 1.0],    # 20 at t=2.0: inside
        [-10.0, 10.0],  # 30 at t=3.0: intersects box
        [-0.5, 0.5],    # 40 at t=5.0: outside
    ])
    range_payload = {
        "range": {
            "x": [1.5, 3.5],
            "y": [-2.0, 2.0],
        }
    }
    result = resolve_timeseries_selection_indices(
        range_payload, active, timestamps_minutes=t, minmax=minmax, is_decimated=True
    )
    assert list(result) == [20, 30]

    lasso_payload = {
        "lassoPoints": {
            "x": [1.5, 2.5, 2.5, 1.5, 1.5],
            "y": [-1.5, -1.5, 1.5, 1.5, -1.5],
        }
    }
    result_lasso = resolve_timeseries_selection_indices(
        lasso_payload, active, timestamps_minutes=t, minmax=minmax, is_decimated=True
    )
    assert list(result_lasso) == [20]

