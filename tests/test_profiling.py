"""Instrumentación de tiempos por etapa (Fase 7, PROMPT §9.3)."""
import logging

import pytest

from utils.profiling import (
    ENV_VAR,
    LOGGER_NAME,
    STAGE_CARGA_DATASET,
    STAGE_FILTRO_METRICAS,
    STAGE_JOB_PREFIX,
    StageRecord,
    configure_from_config,
    get_records,
    is_enabled,
    measure_process_memory,
    profiled,
    record_timing,
    reset_records,
    set_enabled,
    stage,
    stage_job,
    summarize,
)


@pytest.fixture(autouse=True)
def instrumentacion_limpia():
    """Cada prueba arranca con el colector vacío y la instrumentación apagada, y deja
    todo como estaba: el estado vive en variables de módulo compartidas."""
    previo = is_enabled()
    reset_records()
    set_enabled(False)
    yield
    reset_records()
    set_enabled(previo)


def test_desactivado_no_registra_nada():
    with stage("etapa.cualquiera"):
        pass
    assert get_records() == []


def test_activado_registra_etapa_con_campos():
    set_enabled(True)
    with stage("ingesta.sensor", sensor="UHF", n_senales=12484):
        pass

    records = get_records("ingesta.sensor")
    assert len(records) == 1
    assert records[0].fields == {"sensor": "UHF", "n_senales": 12484}
    assert records[0].duration_s >= 0.0


def test_campos_anadidos_dentro_del_bloque_llegan_al_registro():
    set_enabled(True)
    with stage("cache.puntual") as ctx:
        ctx["cache"] = "hit"
    assert get_records()[0].fields == {"cache": "hit"}


def test_etapa_que_falla_se_registra_con_el_tipo_de_error_y_relanza():
    set_enabled(True)
    with pytest.raises(ValueError):
        with stage("etapa.rota"):
            raise ValueError("boom")

    records = get_records("etapa.rota")
    assert len(records) == 1
    assert records[0].fields["error"] == "ValueError"


def test_decorador_profiled_mide_la_funcion_entera():
    set_enabled(True)

    @profiled("metricas.computo", metrica="rms")
    def calcular():
        return 42

    assert calcular() == 42
    assert get_records("metricas.computo")[0].fields == {"metrica": "rms"}


def test_summarize_agrega_por_nombre_de_etapa():
    set_enabled(True)
    for _ in range(3):
        with stage("render.grafica1"):
            pass
    with stage("render.grafica2"):
        pass

    resumen = summarize()
    assert resumen["render.grafica1"]["n"] == 3.0
    assert resumen["render.grafica2"]["n"] == 1.0
    assert resumen["render.grafica1"]["min_ms"] <= resumen["render.grafica1"]["max_ms"]


def test_format_line_es_parseable_como_clave_valor():
    record = StageRecord(name="cache.puntual", duration_s=0.0123, fields={"sensor": "AE", "cache": "miss"})
    campos = dict(part.split("=", 1) for part in record.format_line().split(" "))
    assert campos["etapa"] == "cache.puntual"
    assert campos["sensor"] == "AE"
    assert campos["cache"] == "miss"
    assert float(campos["duracion_ms"]) == pytest.approx(12.3, abs=0.1)


def test_etapa_activa_emite_una_linea_de_log_por_etapa(caplog):
    set_enabled(True)
    with caplog.at_level(logging.INFO, logger=LOGGER_NAME):
        with stage("ingesta.experimento", experimento="Test"):
            pass

    lineas = [r.message for r in caplog.records if r.name == LOGGER_NAME]
    assert len(lineas) == 1
    assert "etapa=ingesta.experimento" in lineas[0]
    assert "experimento=Test" in lineas[0]


# --- Resolución de la configuración (archivo < variable de entorno < force) ----------


def _escribir_config(tmp_path, contenido: str):
    path = tmp_path / "sensors.yaml"
    path.write_text(contenido, encoding="utf-8")
    return path


def test_configuracion_desde_archivo(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert configure_from_config(_escribir_config(tmp_path, "profiling:\n  enabled: true\n")) is True
    assert configure_from_config(_escribir_config(tmp_path, "profiling:\n  enabled: false\n")) is False


def test_sin_seccion_profiling_queda_desactivado(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert configure_from_config(_escribir_config(tmp_path, "sensors: {}\n")) is False


def test_archivo_inexistente_queda_desactivado(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert configure_from_config(tmp_path / "no_existe.yaml") is False


def test_variable_de_entorno_tiene_prioridad_sobre_el_archivo(tmp_path, monkeypatch):
    config = _escribir_config(tmp_path, "profiling:\n  enabled: false\n")
    monkeypatch.setenv(ENV_VAR, "1")
    assert configure_from_config(config) is True
    monkeypatch.setenv(ENV_VAR, "0")
    assert configure_from_config(config) is False


def test_force_tiene_prioridad_sobre_todo(tmp_path, monkeypatch):
    config = _escribir_config(tmp_path, "profiling:\n  enabled: true\n")
    monkeypatch.setenv(ENV_VAR, "1")
    assert configure_from_config(config, force=False) is False


# --- Pruebas de medición de memoria y nuevas etapas R0 -----------------------


def test_measure_process_memory_returns_valid_counters():
    mem = measure_process_memory()
    assert isinstance(mem, dict)
    assert "rss_bytes" in mem
    assert "peak_rss_bytes" in mem
    assert isinstance(mem["rss_bytes"], int)
    assert isinstance(mem["peak_rss_bytes"], int)
    # En Windows, ambos contadores deben ser > 0 y el pico >= actual.
    assert mem["rss_bytes"] > 0
    assert mem["peak_rss_bytes"] >= mem["rss_bytes"]


def test_stage_with_track_memory_records_rss_metrics():
    set_enabled(True)
    with stage(STAGE_CARGA_DATASET, track_memory=True, dataset_id="test_ds"):
        # Asignar un buffer para forzar actividad
        _ = [0] * 100_000

    records = get_records(STAGE_CARGA_DATASET)
    assert len(records) == 1
    rec = records[0]
    assert rec.fields["dataset_id"] == "test_ds"
    assert "rss_bytes" in rec.fields
    assert "peak_rss_bytes" in rec.fields
    assert "rss_delta_bytes" in rec.fields
    assert rec.fields["rss_bytes"] > 0
    assert rec.fields["peak_rss_bytes"] >= rec.fields["rss_bytes"]


def test_profiled_with_track_memory():
    set_enabled(True)

    @profiled(stage_job("load_dataset"), track_memory=True, job_id="job-123")
    def ejecutar_trabajo():
        return 99

    assert ejecutar_trabajo() == 99
    records = get_records(stage_job("load_dataset"))
    assert len(records) == 1
    assert records[0].fields["job_id"] == "job-123"
    assert "rss_bytes" in records[0].fields
    assert "peak_rss_bytes" in records[0].fields


def test_record_timing_explicit_addition():
    set_enabled(True)
    record = record_timing(
        STAGE_FILTRO_METRICAS,
        0.042,
        {"sensor": "UHF", "n_condiciones": 2, "n_metricas_distintas": 1},
    )
    assert record.name == STAGE_FILTRO_METRICAS
    assert record.duration_s == 0.042
    assert record.duration_ms == pytest.approx(42.0)
    assert record.fields["sensor"] == "UHF"

    collected = get_records(STAGE_FILTRO_METRICAS)
    assert len(collected) == 1
    assert collected[0] == record


def test_stage_constants_and_helpers():
    assert STAGE_CARGA_DATASET == "carga.dataset"
    assert STAGE_FILTRO_METRICAS == "filtro.metricas"
    assert STAGE_JOB_PREFIX == "job."
    assert stage_job("warmup") == "job.warmup"
    assert stage_job("metric_calc") == "job.metric_calc"
    assert stage_job("export") == "job.export"
    assert stage_job("merge") == "job.merge"


# --- Pruebas del generador de datasets sintéticos (R0 / Etapa 0) -------------


def test_generate_synthetic_dataset_chunked_output_and_canonical_schema(tmp_path):
    from benchmarks.generate_synthetic import generate_synthetic_dataset
    from data.storage import CanonicalStore

    out_file = tmp_path / "synthetic_test.hdf5"
    res_path = generate_synthetic_dataset(
        output_path=out_file,
        n_signals=120,
        sensors=("UHF", "AE"),
        chunk_size=25,
        seed=123,
    )
    assert res_path == out_file
    assert out_file.exists()

    with CanonicalStore(out_file) as store:
        assert store.dataset_id.startswith("synthetic:")
        assert store.normalization_version == "v1_divide_by_vrange"
        sensors = store.available_sensors()
        assert "UHF" in sensors
        assert "AE" in sensors
        assert store.n_signals("UHF") == 120
        assert store.n_signals("AE") == 120

        # Timestamps
        ts_uhf = store.get_all_timestamps("UHF")
        assert ts_uhf.shape == (120,)
        assert (ts_uhf[1:] >= ts_uhf[:-1]).all()

        # Min/max
        mm_uhf = store.get_all_minmax("UHF")
        assert mm_uhf.shape == (120, 2)
        assert (mm_uhf[:, 0] <= mm_uhf[:, 1]).all()

        # Fila individual O(1)
        row_sig, t, trig, vr = store.get_signal_row("UHF", 10)
        assert row_sig.shape == (3000,)
        assert vr == 0.5

        # Bloque contiguo O(K)
        block_ae = store.get_block("AE", 10, 20)
        assert block_ae.data.shape == (10, 10000)
        assert block_ae.timestamps.shape == (10,)

        # Series auxiliares
        env = store.get_environmental()
        assert env.timestamps.shape[0] > 0
        assert env.temperature.shape == env.timestamps.shape

        ev = store.get_events()
        assert ev.timestamps.shape[0] > 0
        assert ev.event_type.shape == ev.timestamps.shape


def test_generate_benchmark_suite(tmp_path):
    from benchmarks.generate_synthetic import generate_benchmark_suite

    suite = generate_benchmark_suite(
        output_dir=tmp_path,
        sizes=(30,),
        sensors=("UHF",),
        chunk_size=15,
        seed=42,
    )
    assert "synthetic_UHF_0k" in suite
    target = suite["synthetic_UHF_0k"]
    assert target.exists()
    assert target.stat().st_size > 0


def test_generate_synthetic_invalid_sensor(tmp_path):
    from benchmarks.generate_synthetic import generate_synthetic_dataset

    with pytest.raises(ValueError, match="Sensor desconocido"):
        generate_synthetic_dataset(
            output_path=tmp_path / "err.hdf5",
            n_signals=10,
            sensors="SENSOR_INVENTADO",  # type: ignore[arg-type]
        )


