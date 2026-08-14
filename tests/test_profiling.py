"""Instrumentación de tiempos por etapa (Fase 7, PROMPT §9.3)."""
import logging

import pytest

from utils.profiling import (
    ENV_VAR,
    LOGGER_NAME,
    StageRecord,
    configure_from_config,
    get_records,
    is_enabled,
    profiled,
    reset_records,
    set_enabled,
    stage,
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
