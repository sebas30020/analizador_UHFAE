"""Pruebas de ``data/merge.py`` y componentes asociados (Fusión de bases de datos desde la GUI)."""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import h5py
import numpy as np
import pytest
from dash.exceptions import PreventUpdate

from core.models import load_sensor_configs
from data.export import export_filtered
from data.ingest import ingest_experiment
from data.merge import MergeResult, merge_datasets
from data.readers.factory import open_reader
from data.readers.filtered_export_reader import FilteredExportReader
from data.readers.hdf5_reader import HDF5Reader
from metrics.registry import discover_metrics
from tests.conftest import EXPERIMENT_NAME
from ui.app import create_app
from ui.callbacks.helpers import (
    default_merge_filename,
    format_merge_done_message,
    format_merge_error_message,
    format_merge_starting_message,
)
import ui.callbacks.sensor_window_callbacks as swc
from ui.state import AppState, compute_t0


@pytest.fixture(autouse=True, scope="module")
def _ensure_registered():
    discover_metrics()


@pytest.fixture
def app_and_state(tmp_path, synthetic_hdf5, monkeypatch):
    state = AppState(cache_dir=tmp_path / "cache_merge_test", warmup_on_load=False)
    state.load_dataset(synthetic_hdf5)
    monkeypatch.setattr(swc, "get_state", lambda: state)
    dash_app = create_app()
    return dash_app, state


def _find_callback(dash_app, substr: str):
    """Extrae la función Python original de un callback en Dash buscando por subcadena en su clave."""
    for k, v in dash_app.callback_map.items():
        if substr in k:
            return v["callback"].__wrapped__
    raise KeyError(f"Callback con substring '{substr}' no encontrado. Disponibles: {list(dash_app.callback_map)}")


def _create_second_synthetic(original_path: Path, new_path: Path, time_shift_s: float = 1000.0) -> Path:
    """Copia el fixture sintético desplazando todos los timestamps para simular un segundo test independiente."""
    shutil.copyfile(original_path, new_path)
    with h5py.File(new_path, mode="r+") as f:
        exp = f[EXPERIMENT_NAME]
        exp.attrs["description"] = "segundo test sintetico"
        for key in exp.keys():
            if not key.startswith("chunk_"):
                continue
            chunk = exp[key]
            for sname in ("signals", "signals_ae"):
                if sname in chunk and "timestamps" in chunk[sname]:
                    ts = chunk[sname]["timestamps"][:]
                    chunk[sname]["timestamps"][:] = ts + time_shift_s
            for hname in ("humidity", "environmental"):
                if hname in chunk and "timestamps" in chunk[hname]:
                    ts = chunk[hname]["timestamps"][:]
                    chunk[hname]["timestamps"][:] = ts + time_shift_s
            if "events" in chunk and "timestamps" in chunk["events"]:
                ts = chunk["events"]["timestamps"][:]
                chunk["events"]["timestamps"][:] = ts + time_shift_s
    return new_path


def test_merge_chronological_order_and_signals_count(synthetic_hdf5, tmp_path):
    path1 = synthetic_hdf5
    path2 = _create_second_synthetic(synthetic_hdf5, tmp_path / "synthetic2.hdf5", time_shift_s=500.0)
    out_path = tmp_path / "merged.hdf5"

    with open_reader(path1) as r1, open_reader(path2) as r2:
        ingest1 = ingest_experiment(r1, EXPERIMENT_NAME, ["UHF", "AE"])
        ingest2 = ingest_experiment(r2, EXPERIMENT_NAME, ["UHF", "AE"])

    n1_total = sum(b.data.shape[0] for b in ingest1.sensors.values())
    n2_total = sum(b.data.shape[0] for b in ingest2.sensors.values())

    res = merge_datasets(out_path, path1, path2)

    assert res.destination == out_path
    assert res.n_signals_total == n1_total + n2_total

    with open_reader(out_path, partition="resultantes") as reader:
        merged_ingest = ingest_experiment(reader, reader.list_experiments()[0], ["UHF", "AE"])

    for s in ("UHF", "AE"):
        b1 = ingest1.sensors[s]
        b2 = ingest2.sensors[s]
        bm = merged_ingest.sensors[s]

        n1 = b1.data.shape[0]
        n2 = b2.data.shape[0]
        assert bm.data.shape[0] == n1 + n2

        # 1. min(ts_2) > max(ts_1)
        ts1_merged = bm.timestamps[:n1]
        ts2_merged = bm.timestamps[n1:]
        assert np.min(ts2_merged) > np.max(ts1_merged)

        # 2. Las distancias internas del archivo 2 se conservan
        assert np.allclose(np.diff(ts2_merged), np.diff(b2.timestamps))

        # 3. Formas de onda conservadas
        assert np.allclose(bm.data[:n1], b1.data)
        assert np.allclose(bm.data[n1:], b2.data)


def test_merge_t0_and_cadence(synthetic_hdf5, tmp_path):
    path1 = synthetic_hdf5
    path2 = _create_second_synthetic(synthetic_hdf5, tmp_path / "synthetic2.hdf5", time_shift_s=200.0)
    out_path = tmp_path / "merged_t0.hdf5"

    with open_reader(path1) as r1:
        ingest1 = ingest_experiment(r1, EXPERIMENT_NAME, ["UHF", "AE"])
    t0_orig = compute_t0(ingest1.sensors, ingest1.environmental, ingest1.events)

    merge_datasets(out_path, path1, path2)

    with open_reader(out_path) as reader:
        merged_ingest = ingest_experiment(reader, reader.list_experiments()[0], ["UHF", "AE"])
    t0_merged = compute_t0(merged_ingest.sensors, merged_ingest.environmental, merged_ingest.events)

    assert t0_merged == t0_orig


def test_merge_environmental_and_events_shifted_by_same_delta(synthetic_hdf5, tmp_path):
    path1 = synthetic_hdf5
    path2 = _create_second_synthetic(synthetic_hdf5, tmp_path / "synthetic2.hdf5", time_shift_s=300.0)
    out_path = tmp_path / "merged_aux.hdf5"

    with open_reader(path1) as r1, open_reader(path2) as r2:
        ingest1 = ingest_experiment(r1, EXPERIMENT_NAME, ["UHF", "AE"])
        ingest2 = ingest_experiment(r2, EXPERIMENT_NAME, ["UHF", "AE"])

    res = merge_datasets(out_path, path1, path2)
    delta = res.delta_s

    with open_reader(out_path) as reader:
        exp = reader.list_experiments()[0]
        env = reader.get_environmental(exp)
        events = reader.get_events(exp)

    # Cantidades sumadas
    assert env.timestamps.shape[0] == ingest1.environmental.timestamps.shape[0] + ingest2.environmental.timestamps.shape[0]
    assert events.timestamps.shape[0] == ingest1.events.timestamps.shape[0] + ingest2.events.timestamps.shape[0]

    # Comprobar que los timestamps de la segunda serie están efectivamente desfasados por delta
    env2_orig_shifted = ingest2.environmental.timestamps + delta
    for t in env2_orig_shifted:
        assert np.any(np.isclose(env.timestamps, t))

    events2_orig_shifted = ingest2.events.timestamps + delta
    for t in events2_orig_shifted:
        assert np.any(np.isclose(events.timestamps, t))


def test_merge_initial_dead_time_and_seam_overlap(synthetic_hdf5, tmp_path):
    path1 = synthetic_hdf5
    path2 = tmp_path / "synthetic_deadtime.hdf5"
    _create_second_synthetic(synthetic_hdf5, path2, time_shift_s=200.0)

    # Ajustar un ambiental en path2 que empiece antes de su primera señal
    with h5py.File(path2, mode="r+") as f:
        exp = f[EXPERIMENT_NAME]
        c1 = exp["chunk_000001"]
        c1["humidity"]["timestamps"][:] = [0.0]  # señales empiezan en t=1.0+200=201.0, pero ambiental en 0.0+200=200.0 vs t_fin_1=106.0 -> solapa 1.0 s después del shift
        c1["humidity"]["timestamps"][:] = [-50.0]  # ambiental anterior a la primera señal del archivo 2

    out_path = tmp_path / "merged_overlap.hdf5"
    res = merge_datasets(out_path, path1, path2)

    assert res.seam_overlap_s > 0.0
    with h5py.File(out_path, mode="r") as f:
        assert f.attrs["merge_seam_overlap_s"] == res.seam_overlap_s


def test_merge_reader_compatibility_and_partitions(synthetic_hdf5, tmp_path):
    path1 = synthetic_hdf5
    path2 = _create_second_synthetic(synthetic_hdf5, tmp_path / "synthetic2.hdf5", time_shift_s=100.0)
    out_path = tmp_path / "merged_partitions.hdf5"

    merge_datasets(out_path, path1, path2)

    # Debe ser reconocido como FilteredExportReader
    with open_reader(out_path) as r:
        assert isinstance(r, FilteredExportReader)
        assert r.available_sensors() == ["UHF", "AE"]

    # 1. resultantes
    with open_reader(out_path, partition="resultantes") as r:
        res = ingest_experiment(r, r.list_experiments()[0], ["UHF", "AE"])
        assert sum(b.data.shape[0] for b in res.sensors.values()) > 0

    # 2. filtradas (no debe fallar, debe devolver 0 señales)
    with open_reader(out_path, partition="filtradas") as r:
        res = ingest_experiment(r, r.list_experiments()[0], ["UHF", "AE"])
        assert sum(b.data.shape[0] for b in res.sensors.values()) == 0

    # 3. ambas (debe equivaler a resultantes)
    with open_reader(out_path, partition="ambas") as r:
        res = ingest_experiment(r, r.list_experiments()[0], ["UHF", "AE"])
        assert sum(b.data.shape[0] for b in res.sensors.values()) > 0

    # Comprobar directamente en HDF5 que filtradas/<sensor> existe con 0 filas
    with h5py.File(out_path, mode="r") as f:
        assert "filtradas" in f
        for s in ("UHF", "AE"):
            assert s in f["filtradas"]
            assert f["filtradas"][s]["data"].shape[0] == 0


def test_merge_provenance_and_auditability(synthetic_hdf5, tmp_path):
    path1 = synthetic_hdf5
    path2 = _create_second_synthetic(synthetic_hdf5, tmp_path / "synthetic2.hdf5", time_shift_s=150.0)
    out_path = tmp_path / "merged_provenance.hdf5"

    res = merge_datasets(out_path, path1, path2)

    with h5py.File(out_path, mode="r") as f:
        assert f.attrs["schema_version"] == 2
        assert "Fusión:" in str(f.attrs["experiment"])
        assert len(f.attrs["merge_sources"]) == 2
        assert list(f.attrs["merge_time_offsets_s"]) == [0.0, res.delta_s]
        assert len(f.attrs["merge_signal_counts"]) == 2

        for s in ("UHF", "AE"):
            grp = f["resultantes"][s]
            sfi = grp["source_file_index"][:]
            st = grp["source_timestamp"][:]
            si = grp["source_index"][:]

            n1 = res.sensor_counts[s] // 2  # ya que path2 es copia con igual nº de señales
            assert np.all(sfi[:n1] == 0)
            assert np.all(sfi[n1:] == 1)
            assert len(st) == len(sfi)
            assert len(si) == len(sfi)


def test_merge_order_matters(synthetic_hdf5, tmp_path):
    path1 = synthetic_hdf5
    path2 = _create_second_synthetic(synthetic_hdf5, tmp_path / "synthetic2.hdf5", time_shift_s=1000.0)

    out_1_2 = tmp_path / "merged_1_2.hdf5"
    out_2_1 = tmp_path / "merged_2_1.hdf5"

    res_1_2 = merge_datasets(out_1_2, path1, path2)
    res_2_1 = merge_datasets(out_2_1, path2, path1)

    assert res_1_2.delta_s != res_2_1.delta_s

    with open_reader(out_1_2) as r1, open_reader(out_2_1) as r2:
        ing1 = ingest_experiment(r1, r1.list_experiments()[0], ["UHF"])
        ing2 = ingest_experiment(r2, r2.list_experiments()[0], ["UHF"])
        # Los timestamps del tramo fusionado serán distintos según el orden
        assert not np.array_equal(ing1.sensors["UHF"].timestamps, ing2.sensors["UHF"].timestamps)


def test_merge_validations_and_rejections(synthetic_hdf5, tmp_path):
    # 1. Misma ruta
    with pytest.raises(ValueError, match="distintas"):
        merge_datasets(tmp_path / "out.hdf5", synthetic_hdf5, synthetic_hdf5)

    # 2. Archivo inexistente
    with pytest.raises(FileNotFoundError):
        merge_datasets(tmp_path / "out.hdf5", synthetic_hdf5, tmp_path / "no_existe.hdf5")

    # 3. Conjuntos de sensores distintos
    # Crear un export que solo tenga UHF
    with HDF5Reader(synthetic_hdf5) as r:
        ing = ingest_experiment(r, EXPERIMENT_NAME, ["UHF"])
    configs = load_sensor_configs("config/sensors.yaml")
    active_masks = {"UHF": np.ones(ing.sensors["UHF"].data.shape[0], dtype=bool)}
    uhf_only_path = tmp_path / "uhf_only.hdf5"
    export_filtered(
        uhf_only_path, ing.dataset_id, ing.experiment, ing.normalization.version,
        ing.sensors, configs, active_masks, ing.environmental, ing.events,
    )

    with pytest.raises(ValueError, match="sensores no coinciden"):
        merge_datasets(tmp_path / "out.hdf5", synthetic_hdf5, uhf_only_path)


def test_merge_helpers(tmp_path):
    p1 = Path("/tmp/data1.hdf5")
    p2 = Path("/tmp/data2.hdf5")
    now = datetime(2026, 8, 30, 12, 0, 0)

    filename = default_merge_filename(p1, p2, now)
    assert filename == "data1_data2_fusionado_20260830_120000.hdf5"

    msg_start = format_merge_starting_message(Path("dest.hdf5"))
    assert "Fusionando a dest.hdf5" in msg_start

    msg_done = format_merge_done_message(Path("dest.hdf5"), 100, 12.5)
    assert "Fusionado: dest.hdf5 (100 señales) [solape ambiental en costura: 12.5 s]" == msg_done

    msg_done_no_overlap = format_merge_done_message(Path("dest.hdf5"), 100, 0.0)
    assert "Fusionado: dest.hdf5 (100 señales)" == msg_done_no_overlap

    msg_err = format_merge_error_message(ValueError("Error prueba"))
    assert "Error al fusionar: Error prueba" in msg_err


def test_merge_ui_callbacks(app_and_state, monkeypatch, tmp_path):
    dash_app, state = app_and_state

    # 1. Probar _on_select_merge_1
    fn_select_1 = _find_callback(dash_app, "merge-path-label-1")
    monkeypatch.setattr(swc, "_open_file_dialog", lambda: str(tmp_path / "file1.hdf5"))
    lbl1, path1 = fn_select_1(1)
    assert lbl1 == "Archivo 1: file1.hdf5"
    assert path1 == str(tmp_path / "file1.hdf5")

    # Cancelar diálogo -> PreventUpdate
    monkeypatch.setattr(swc, "_open_file_dialog", lambda: None)
    with pytest.raises(PreventUpdate):
        fn_select_1(2)

    # 2. Probar _on_select_merge_2
    fn_select_2 = _find_callback(dash_app, "merge-path-label-2")
    monkeypatch.setattr(swc, "_open_file_dialog", lambda: str(tmp_path / "file2.hdf5"))
    lbl2, path2 = fn_select_2(1)
    assert lbl2 == "Archivo 2: file2.hdf5"
    assert path2 == str(tmp_path / "file2.hdf5")

    # 3. Probar _on_merge_databases
    fn_merge = _find_callback(dash_app, "merge-status")

    # Sin archivos seleccionados
    monkeypatch.setattr(swc, "ctx", SimpleNamespace(triggered_id="btn-merge-databases"))
    msg, disabled = fn_merge(1, None, None, None, "resultantes")
    assert "Seleccione ambos archivos" in msg
    assert disabled is True

    # Archivos iguales
    msg, disabled = fn_merge(1, None, str(tmp_path / "file1.hdf5"), str(tmp_path / "file1.hdf5"), "resultantes")
    assert "deben ser distintos" in msg
    assert disabled is True

    # Disparo válido
    dest_path = str(tmp_path / "merged_out.hdf5")
    monkeypatch.setattr(swc, "_open_save_file_dialog", lambda name: dest_path)
    launched = []
    monkeypatch.setattr(swc, "_launch_merge_thread", lambda st, p1, p2, d, part: launched.append((p1, p2, d, part)))

    msg, disabled = fn_merge(1, None, str(tmp_path / "file1.hdf5"), str(tmp_path / "file2.hdf5"), "resultantes")
    assert "Fusionando a merged_out.hdf5" in msg
    assert disabled is False  # sondeo habilitado
    assert len(launched) == 1
    assert launched[0] == (str(tmp_path / "file1.hdf5"), str(tmp_path / "file2.hdf5"), dest_path, "resultantes")
