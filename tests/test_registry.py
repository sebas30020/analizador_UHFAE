import pytest

from metrics.registry import MetricContext, discover_metrics, get_metric, list_metrics, metric


def test_discover_finds_all_20_metrics():
    registry = discover_metrics()
    assert len(registry) == 20


def test_regimen_counts_match_fase0_inventory():
    puntual = list_metrics(regimen="puntual")
    grupo = list_metrics(regimen="grupo")
    assert len(puntual) == 17  # 15 tiempo + 2 frecuencia
    assert len(grupo) == 3     # tasa_pulsos, tasa_energia, tasa_rafagas


def test_dominio_counts():
    tiempo = list_metrics(dominio="tiempo")
    frecuencia = list_metrics(dominio="frecuencia")
    assert len(tiempo) == 18  # 15 puntuales + 3 grupo
    assert len(frecuencia) == 2


def test_get_unknown_metric_raises_keyerror():
    with pytest.raises(KeyError):
        get_metric("no_existe")


def test_duplicate_registration_raises():
    discover_metrics()  # asegura que el registro ya está poblado

    with pytest.raises(ValueError):
        @metric(id="rms", label="dup", regimen="puntual", dominio="tiempo", unit="", version=1)
        def _dup(ctx: MetricContext, **params: object):
            return None


def test_shannon_entropy_unit_is_dimensionless_not_bits():
    # Corrección de AUDITORIA_FORMULAS_PDF_vs_metricas.md §3.
    definition = get_metric("shannon_entropy")
    assert definition.unit == ""
