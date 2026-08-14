import numpy as np
import pytest

from core.models import EnvironmentalSeries, EventSeries, SignalBlock
from ui.state import AppState, compute_t0


def _block(timestamps) -> SignalBlock:
    timestamps = np.asarray(timestamps, dtype=np.float64)
    n = timestamps.shape[0]
    data = np.zeros((n, 1), dtype=np.float32)
    return SignalBlock(
        data=data,
        timestamps=timestamps,
        trigger=np.zeros(n, dtype=np.float64),
        vrange=np.ones(n, dtype=np.float64),
        valid_mask=np.ones(n, dtype=bool),
        minmax=np.zeros((n, 2), dtype=np.float32),
    )


def _empty_environmental() -> EnvironmentalSeries:
    return EnvironmentalSeries(timestamps=np.array([]), temperature=np.array([]), humidity=np.array([]))


def _empty_events() -> EventSeries:
    return EventSeries(timestamps=np.array([]), event_type=np.array([], dtype="<U10"))


def test_compute_t0_picks_earliest_across_sensors_env_events():
    blocks = {"UHF": _block([105.0, 106.0]), "AE": _block([50.0, 60.0])}
    environmental = EnvironmentalSeries(
        timestamps=np.array([40.0]), temperature=np.array([20.0]), humidity=np.array([50.0])
    )
    events = EventSeries(timestamps=np.array([45.0]), event_type=np.array(["SHOT"]))
    assert compute_t0(blocks, environmental, events) == 40.0


def test_compute_t0_ignores_empty_sensor_blocks():
    blocks = {"UHF": _block([]), "AE": _block([50.0, 60.0])}
    assert compute_t0(blocks, _empty_environmental(), _empty_events()) == 50.0


def test_compute_t0_all_empty_returns_zero():
    blocks = {"UHF": _block([]), "AE": _block([])}
    assert compute_t0(blocks, _empty_environmental(), _empty_events()) == 0.0


def test_compute_t0_uses_raw_timestamps_not_valid_mask():
    # La señal más temprana (10.0) tiene metadato inválido -- t0 debe igual considerarla,
    # no solo las señales con valid_mask=True (ver docstring de compute_t0).
    block = _block([10.0, 20.0])
    block.valid_mask[:] = [False, True]
    blocks = {"UHF": block}
    assert compute_t0(blocks, _empty_environmental(), _empty_events()) == 10.0


# --- AppState: máscara de filtrado, deshacer/rehacer/restablecer (Fase 6, PROMPT §7.2) ---
# UHF en synthetic_hdf5 (tests/conftest.py) tiene 4 señales cronológicas (t=1,2,105,106).


@pytest.fixture
def app_state(tmp_path) -> AppState:
    return AppState(cache_dir=tmp_path / "cache")


def test_load_dataset_initializes_active_mask_all_true(app_state, synthetic_hdf5):
    app_state.load_dataset(synthetic_hdf5)
    mask = app_state.get_active_mask("UHF")
    assert mask.shape[0] == 4
    assert mask.all()


def test_apply_filter_excludes_indices_and_bumps_filter_version(app_state, synthetic_hdf5):
    app_state.load_dataset(synthetic_hdf5)
    v0 = app_state.filter_version
    v1 = app_state.apply_filter("UHF", np.array([1, 3]))
    assert v1 == v0 + 1
    assert list(app_state.get_active_mask("UHF")) == [True, False, True, False]


def test_apply_filter_pushes_undo_and_clears_redo(app_state, synthetic_hdf5):
    app_state.load_dataset(synthetic_hdf5)
    app_state.apply_filter("UHF", np.array([0]))
    app_state.apply_filter("UHF", np.array([1]))
    assert app_state.can_undo("UHF")

    app_state.undo_filter("UHF")
    assert app_state.can_redo("UHF")

    # Una nueva operación de filtrado invalida el "rehacer" pendiente -- nueva rama de historia.
    app_state.apply_filter("UHF", np.array([2]))
    assert not app_state.can_redo("UHF")


def test_undo_redo_round_trip_restores_exact_mask(app_state, synthetic_hdf5):
    app_state.load_dataset(synthetic_hdf5)
    app_state.apply_filter("UHF", np.array([1]))
    after_filter = app_state.get_active_mask("UHF")

    app_state.undo_filter("UHF")
    assert app_state.get_active_mask("UHF").all()

    app_state.redo_filter("UHF")
    assert np.array_equal(app_state.get_active_mask("UHF"), after_filter)


def test_undo_with_empty_stack_is_noop(app_state, synthetic_hdf5):
    app_state.load_dataset(synthetic_hdf5)
    v0 = app_state.filter_version
    v1 = app_state.undo_filter("UHF")
    assert v1 == v0
    assert app_state.get_active_mask("UHF").all()


def test_redo_with_empty_stack_is_noop(app_state, synthetic_hdf5):
    app_state.load_dataset(synthetic_hdf5)
    v0 = app_state.filter_version
    v1 = app_state.redo_filter("UHF")
    assert v1 == v0


def test_reset_filters_hard_resets_and_clears_both_stacks(app_state, synthetic_hdf5):
    app_state.load_dataset(synthetic_hdf5)
    app_state.apply_filter("UHF", np.array([0]))
    app_state.undo_filter("UHF")  # deja algo en la pila de rehacer
    assert app_state.can_redo("UHF")

    app_state.reset_filters("UHF")
    assert app_state.get_active_mask("UHF").all()
    assert not app_state.can_undo("UHF")
    assert not app_state.can_redo("UHF")


def test_reset_filters_scoped_to_one_sensor_only(app_state, synthetic_hdf5):
    app_state.load_dataset(synthetic_hdf5)
    app_state.apply_filter("UHF", np.array([0]))
    app_state.apply_filter("AE", np.array([0]))

    app_state.reset_filters("UHF")
    assert app_state.get_active_mask("UHF").all()
    assert not app_state.get_active_mask("AE").all()  # AE no se ve afectado


def test_load_dataset_resets_mask_and_stacks_for_new_dataset(app_state, synthetic_hdf5):
    app_state.load_dataset(synthetic_hdf5)
    app_state.apply_filter("UHF", np.array([0]))

    app_state.load_dataset(synthetic_hdf5)  # dataset "nuevo" (mismo archivo, otra carga)
    assert app_state.get_active_mask("UHF").all()
    assert not app_state.can_undo("UHF")


def test_get_filter_counts_reports_active_total_ops(app_state, synthetic_hdf5):
    app_state.load_dataset(synthetic_hdf5)
    app_state.apply_filter("UHF", np.array([0]))
    app_state.apply_filter("UHF", np.array([1, 2]))

    active, total, n_ops = app_state.get_filter_counts("UHF")
    assert total == 4
    assert active == 1
    assert n_ops == 2
