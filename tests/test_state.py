import gc
import threading
import time
import weakref

import numpy as np
import pytest

from cache.warmup import WarmupSpec
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
    # Sin precalentamiento (Fase 7): estas pruebas son sobre el estado de filtrado, y un
    # hilo de warmup escribiendo en el caché de ``tmp_path`` mientras pytest lo borra
    # convertiría un fallo de limpieza en un fallo de prueba intermitente.
    return AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)


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


# --- Carga de un origen Keysight (rama lectura_keysight) -----------------------------
# synthetic_keysight_h5 (tests/conftest.py) tiene 12 señales UHF_KS, sin AE/eventos/ambiental.


def test_load_dataset_keysight_only_populates_uhf_ks(app_state, synthetic_keysight_h5):
    dataset = app_state.load_dataset(synthetic_keysight_h5)
    assert set(dataset.blocks.keys()) == {"UHF_KS"}
    assert dataset.blocks["UHF_KS"].data.shape[0] == 12
    assert dataset.environmental.timestamps.shape[0] == 0
    assert dataset.events.timestamps.shape[0] == 0


def test_load_dataset_keysight_resets_mask_and_index_for_uhf_ks(app_state, synthetic_keysight_h5):
    app_state.load_dataset(synthetic_keysight_h5)
    mask = app_state.get_active_mask("UHF_KS")
    assert mask.shape[0] == 12
    assert mask.all()
    assert app_state.get_active_index("UHF_KS") == 0


def test_load_dataset_keysight_applies_effective_sensor_config(app_state, synthetic_keysight_h5):
    dataset = app_state.load_dataset(synthetic_keysight_h5)
    cfg = dataset.sensor_configs["UHF_KS"]
    # fs_hz/n_samples nominales del YAML (2e10, 20000) quedan sobreescritos por los
    # atributos reales del fixture (XInc=5e-11 -> 2e10 Hz coincide; NumPoints=4).
    assert cfg.fs_hz == pytest.approx(1.0 / 5e-11)
    assert cfg.n_samples == 4


def test_load_dataset_keysight_does_not_disturb_other_sensor_configs(app_state, synthetic_keysight_h5):
    dataset = app_state.load_dataset(synthetic_keysight_h5)
    # UHF/AE siguen con su perfil nominal del YAML -- el override solo tocó UHF_KS.
    assert dataset.sensor_configs["UHF"].fs_hz == 3.0e9
    assert dataset.sensor_configs["AE"].fs_hz == 1.0e5


def test_load_hdf5_after_keysight_still_populates_uhf_and_ae(app_state, synthetic_keysight_h5, synthetic_hdf5):
    """No-regresión explícita: cargar un dataset Keysight y luego el formato antiguo en
    la misma sesión debe dejar el estado exactamente como si solo se hubiera cargado el
    segundo (§3, §7.9 del plan -- el camino de med_5_ago_3.hdf5 no cambia)."""
    app_state.load_dataset(synthetic_keysight_h5)
    dataset = app_state.load_dataset(synthetic_hdf5)
    assert set(dataset.blocks.keys()) == {"UHF", "AE"}
    assert app_state.get_active_mask("UHF_KS").shape[0] == 0


# --- Cancelación del precalentamiento al cambiar de dataset ------------------------
# Ver archivos_md/CONTINUAR_DIAGNOSTICO_RENDIMIENTO.md §7: el hilo de warmup del dataset
# anterior seguía vivo tras cargar otro archivo, reteniendo su matriz completa.


def test_load_dataset_cancels_the_previous_warmup(tmp_path, synthetic_hdf5):
    state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=True)

    state.load_dataset(synthetic_hdf5)
    primera = state._warmup_cancel
    assert primera is not None and not primera.is_set()

    state.load_dataset(synthetic_hdf5)

    assert primera.is_set(), "el warmup anterior debe quedar cancelado al cargar otro dataset"
    segunda = state._warmup_cancel
    assert segunda is not None and segunda is not primera and not segunda.is_set()

    for hilo in threading.enumerate():
        if hilo.name == "cache-warmup":
            hilo.join(timeout=30)


def test_load_dataset_releases_the_previous_dataset_matrix(tmp_path, monkeypatch, synthetic_hdf5):
    """El síntoma que importa: la matriz del dataset anterior debe liberarse sin esperar
    a que termine su precalentamiento.

    El warmup se alarga a propósito (una spec lenta, muchas veces) porque con las 9 specs
    reales sobre el fixture sintético el hilo termina en milisegundos y la prueba pasaría
    igual con el bug puesto. Lo que se afirma es que la matriz se libera **mientras el
    warmup todavía tendría trabajo pendiente**.
    """
    liberado = threading.Event()

    def _lento(*args, **kwargs):
        # Cede el GIL en cada spec: sin cancelación, el hilo seguiría vivo -- y
        # reteniendo la matriz -- durante todo el bucle.
        liberado.wait(timeout=10.0)
        return np.array([]), np.array([])

    monkeypatch.setattr("cache.warmup.get_or_compute_puntual", _lento)
    monkeypatch.setattr(
        "ui.state.default_warmup_specs",
        lambda sensor, **kw: [WarmupSpec(sensor=sensor, metric_id="rms", regimen="puntual")] * 20,
    )

    state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=True)
    primero = state.load_dataset(synthetic_hdf5)
    matriz = weakref.ref(primero.blocks["UHF"].data)
    del primero

    state.load_dataset(synthetic_hdf5)  # debe cancelar el warmup anterior

    try:
        for _ in range(150):  # el hilo termina la spec en curso antes de salir
            gc.collect()
            if matriz() is None:
                break
            time.sleep(0.1)
        assert matriz() is None, "la matriz del dataset anterior sigue retenida por el warmup"
    finally:
        liberado.set()
        for hilo in threading.enumerate():
            if hilo.name == "cache-warmup":
                hilo.join(timeout=30)
