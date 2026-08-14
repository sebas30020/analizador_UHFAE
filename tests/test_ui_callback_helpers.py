from ui.callbacks.helpers import (
    clamp_index,
    decode_metric_option,
    encode_metric_option,
    parse_compare_indices,
    parse_route,
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
