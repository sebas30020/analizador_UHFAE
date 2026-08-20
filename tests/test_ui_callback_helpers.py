import numpy as np

from ui.callbacks.helpers import (
    AUTOPLAY_DEFAULT_SPEED_HZ,
    AUTOPLAY_MIN_INTERVAL_MS,
    AUTOPLAY_SPEED_OPTIONS_HZ,
    AUTOPLAY_TRIGGER_ID,
    autoplay_interval_ms,
    clamp_index,
    decode_metric_option,
    encode_metric_option,
    parse_compare_indices,
    parse_route,
    resolve_map_axis_status,
    resolve_map_click_signal_index,
    resolve_nav_index,
)


def test_parse_compare_indices_basic():
    assert parse_compare_indices("450,451,452", n_total=1000) == [450, 451, 452]


def test_parse_compare_indices_ignores_out_of_range_and_junk():
    assert parse_compare_indices("450, abc, -1, 999999, 452", n_total=1000) == [450, 452]


def test_parse_compare_indices_excludes_main_index_and_duplicates():
    assert parse_compare_indices("450,450,10", n_total=1000, exclude=450) == [10]


def test_parse_compare_indices_empty_input():
    assert parse_compare_indices("", n_total=1000) == []
    assert parse_compare_indices(None, n_total=1000) == []


def test_clamp_index_bounds():
    assert clamp_index(-5, 100) == 0
    assert clamp_index(500, 100) == 99
    assert clamp_index(50, 100) == 50
    assert clamp_index(50, 0) == 0


def test_resolve_nav_index_prev_next_relative_to_current_state_not_input_field():
    # Si el usuario dejó el campo numérico a medio escribir, +-1 debe partir del
    # índice conocido por el estado, no de lo que haya (posiblemente inválido) en el campo.
    assert resolve_nav_index("btn-next", nav_index_value=None, current_index=10, n_total=100) == 11
    assert resolve_nav_index("btn-prev", nav_index_value=999, current_index=10, n_total=100) == 9


def test_resolve_nav_index_direct_typing():
    assert resolve_nav_index("nav-index", nav_index_value=42, current_index=10, n_total=100) == 42


def test_resolve_nav_index_clamps_at_edges():
    assert resolve_nav_index("btn-prev", nav_index_value=None, current_index=0, n_total=100) == 0
    assert resolve_nav_index("btn-next", nav_index_value=None, current_index=99, n_total=100) == 99


def test_resolve_nav_index_click_on_graph_uses_target():
    assert resolve_nav_index("graph-timeseries", None, current_index=10, n_total=100, click_target_index=77) == 77
    assert resolve_nav_index("graph-metric", None, current_index=10, n_total=100, click_target_index=33) == 33


def test_resolve_nav_index_click_on_map_uses_target():
    # Click en el mapa 2D o 3D (#4/#5): mismo trato que #1/#3, pero el target ya viene
    # exacto (customdata), no de una búsqueda por timestamp -- ver
    # resolve_map_click_signal_index más abajo.
    assert resolve_nav_index("graph-map", None, current_index=10, n_total=100, click_target_index=55) == 55


def test_resolve_nav_index_initial_load_keeps_current():
    assert resolve_nav_index(None, None, current_index=5, n_total=100) == 5


# --- parse_route (Fase 5: ventanas gemelas por sensor) ---

def test_parse_route_root_defaults_to_uhf_sensor():
    assert parse_route(None) == {"page": "sensor", "sensor": "UHF"}
    assert parse_route("/") == {"page": "sensor", "sensor": "UHF"}


def test_parse_route_sensor_page():
    assert parse_route("/sensor/UHF") == {"page": "sensor", "sensor": "UHF"}
    assert parse_route("/sensor/AE") == {"page": "sensor", "sensor": "AE"}


def test_parse_route_unknown_sensor_falls_back_to_default():
    assert parse_route("/sensor/BOGUS") == {"page": "sensor", "sensor": "UHF"}


def test_parse_route_malformed_paths_fall_back_to_default():
    assert parse_route("/sensor") == {"page": "sensor", "sensor": "UHF"}
    assert parse_route("/sensor/UHF/window") == {"page": "sensor", "sensor": "UHF"}
    assert parse_route("/algo/random") == {"page": "sensor", "sensor": "UHF"}
    assert parse_route("") == {"page": "sensor", "sensor": "UHF"}


# --- codificación de opciones del selector múltiple de métricas ---

def test_encode_decode_metric_option_roundtrip():
    encoded = encode_metric_option("grupo_reduccion", "kurtosis")
    assert encoded == "grupo_reduccion:kurtosis"
    assert decode_metric_option(encoded) == ("grupo_reduccion", "kurtosis")


def test_encode_decode_distinguishes_regimen_for_same_metric_id():
    a = encode_metric_option("puntual", "kurtosis")
    b = encode_metric_option("grupo_reduccion", "kurtosis")
    assert a != b
    assert decode_metric_option(a)[0] != decode_metric_option(b)[0]


# --- Auto-play y navegación consciente del filtrado (rama auto-play) ---
#
# Las señales 2, 3 y 6 están excluidas por el usuario: la navegación paso a paso debe
# saltárselas, y el auto-play además cerrar el bucle del último activo al primero.

_ACTIVOS = np.array([0, 1, 4, 5, 7])  # de n_total=8; excluidas: 2, 3, 6


def test_next_skips_filtered_signals():
    assert resolve_nav_index("btn-next", None, current_index=1, n_total=8, active_indices=_ACTIVOS) == 4
    assert resolve_nav_index("btn-next", None, current_index=5, n_total=8, active_indices=_ACTIVOS) == 7


def test_prev_skips_filtered_signals():
    assert resolve_nav_index("btn-prev", None, current_index=4, n_total=8, active_indices=_ACTIVOS) == 1
    assert resolve_nav_index("btn-prev", None, current_index=7, n_total=8, active_indices=_ACTIVOS) == 5


def test_next_and_prev_stop_at_the_edges_of_the_active_set():
    # Los botones manuales no dan la vuelta: se quedan en el extremo, como siempre.
    assert resolve_nav_index("btn-next", None, current_index=7, n_total=8, active_indices=_ACTIVOS) == 7
    assert resolve_nav_index("btn-prev", None, current_index=0, n_total=8, active_indices=_ACTIVOS) == 0


def test_autoplay_advances_like_next():
    assert resolve_nav_index(AUTOPLAY_TRIGGER_ID, None, current_index=1, n_total=8, active_indices=_ACTIVOS) == 4


def test_autoplay_wraps_from_last_active_to_first():
    assert resolve_nav_index(AUTOPLAY_TRIGGER_ID, None, current_index=7, n_total=8, active_indices=_ACTIVOS) == 0


def test_navigation_from_an_excluded_signal_lands_on_an_active_one():
    # El usuario puede estar viendo una señal y filtrarla después: avanzar desde ahí
    # debe llevar al siguiente activo, no al índice+1 (que podría seguir excluido).
    assert resolve_nav_index("btn-next", None, current_index=2, n_total=8, active_indices=_ACTIVOS) == 4
    assert resolve_nav_index("btn-prev", None, current_index=3, n_total=8, active_indices=_ACTIVOS) == 1


def test_typed_index_ignores_the_filter_mask():
    # Teclear un índice es pedir esa señal concreta, aunque esté excluida del análisis.
    assert resolve_nav_index("nav-index", 3, current_index=0, n_total=8, active_indices=_ACTIVOS) == 3


def test_without_mask_behaviour_is_the_previous_one():
    # No-regresión: sin información de filtrado, el paso es el ±1 acotado de siempre.
    assert resolve_nav_index("btn-next", None, current_index=10, n_total=100) == 11
    assert resolve_nav_index("btn-prev", None, current_index=10, n_total=100) == 9
    assert resolve_nav_index("btn-next", None, current_index=99, n_total=100) == 99


def test_with_every_signal_filtered_out_navigation_degrades_gracefully():
    vacio = np.array([], dtype=np.int64)
    assert resolve_nav_index("btn-next", None, current_index=5, n_total=8, active_indices=vacio) == 6
    assert resolve_nav_index(AUTOPLAY_TRIGGER_ID, None, current_index=7, n_total=8, active_indices=vacio) == 7


def test_single_active_signal_keeps_autoplay_in_place():
    # El callback traduce "no me moví" en PreventUpdate para no repintar en vano.
    uno = np.array([3])
    assert resolve_nav_index(AUTOPLAY_TRIGGER_ID, None, current_index=3, n_total=8, active_indices=uno) == 3


# --- velocidad de auto-play ---

def test_autoplay_interval_translates_speed_to_period():
    assert autoplay_interval_ms(1.0) == 1000
    assert autoplay_interval_ms(2.0) == 500
    assert autoplay_interval_ms(0.5) == 2000


def test_autoplay_interval_never_goes_below_the_safe_floor():
    # Ninguna velocidad -- ni una inyectada fuera del selector -- puede pedir cuadros
    # más rápido de lo que el navegador los dibuja.
    assert autoplay_interval_ms(1000.0) == AUTOPLAY_MIN_INTERVAL_MS
    for speed in AUTOPLAY_SPEED_OPTIONS_HZ:
        assert autoplay_interval_ms(speed) >= AUTOPLAY_MIN_INTERVAL_MS


def test_autoplay_interval_falls_back_on_empty_or_invalid_speed():
    assert autoplay_interval_ms(None) == autoplay_interval_ms(AUTOPLAY_DEFAULT_SPEED_HZ)
    assert autoplay_interval_ms(0) == autoplay_interval_ms(AUTOPLAY_DEFAULT_SPEED_HZ)
    assert autoplay_interval_ms(-3.0) == autoplay_interval_ms(AUTOPLAY_DEFAULT_SPEED_HZ)


# --- estado de los ejes de los mapas de separación (#4/#5) ---

def test_resolve_map_axis_status_missing_axis_is_not_assigned():
    assert resolve_map_axis_status(["rms", None]) == (False, False)
    assert resolve_map_axis_status([None, None, None]) == (False, False)


def test_resolve_map_axis_status_all_assigned_no_duplicate():
    assert resolve_map_axis_status(["rms", "kurtosis"]) == (True, False)
    assert resolve_map_axis_status(["rms", "kurtosis", "vpp"]) == (True, False)


def test_resolve_map_axis_status_all_assigned_with_duplicate():
    assert resolve_map_axis_status(["rms", "rms"]) == (True, True)
    assert resolve_map_axis_status(["rms", "kurtosis", "rms"]) == (True, True)


# --- resolve_map_click_signal_index (click en #4/#5 -> Gráfica #2) ---

def test_resolve_map_click_signal_index_reads_customdata():
    click_data = {"points": [{"x": 1.2, "y": 3.4, "customdata": 4821}]}
    assert resolve_map_click_signal_index(click_data) == 4821


def test_resolve_map_click_signal_index_handles_customdata_as_single_element_list():
    # Plotly serializa customdata escalar como lista de un elemento en algunos casos.
    click_data = {"points": [{"customdata": [4821]}]}
    assert resolve_map_click_signal_index(click_data) == 4821


def test_resolve_map_click_signal_index_no_points_returns_none():
    assert resolve_map_click_signal_index({"points": []}) is None
    assert resolve_map_click_signal_index(None) is None
    assert resolve_map_click_signal_index({}) is None


def test_resolve_map_click_signal_index_missing_customdata_returns_none():
    # La traza de resaltado no trae customdata -- clicar sobre la señal ya seleccionada
    # no debe hacer nada, no es un error.
    assert resolve_map_click_signal_index({"points": [{"x": 1.0, "y": 2.0}]}) is None
