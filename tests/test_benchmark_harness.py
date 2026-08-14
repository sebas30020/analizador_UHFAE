"""Arnés de benchmarks (Fase 7): agregación, veredicto y formato del reporte.

Se prueba con funciones sintéticas de duración conocida -- la corrida real contra el
`.hdf5` (``benchmarks/run_benchmarks.py``) tarda minutos y no cabe en la suite.
"""
import json
import time

import pytest

from benchmarks.harness import (
    BenchmarkResult,
    format_markdown_table,
    measure,
    measure_once,
    to_json_dict,
)


def test_measure_descarta_el_calentamiento():
    llamadas = {"n": 0}

    def contar():
        llamadas["n"] += 1

    measure(contar, repeats=3, warmup=2)
    assert llamadas["n"] == 5  # 2 de calentamiento + 3 medidas


def test_measure_devuelve_mediana_min_max_coherentes():
    def dormir_poco():
        time.sleep(0.002)

    mediana, mn, mx = measure(dormir_poco, repeats=3, warmup=0)
    assert mn <= mediana <= mx
    assert mn >= 1.0  # al menos ~2 ms, con holgura para la resolución del reloj


def test_measure_exige_al_menos_una_repeticion():
    with pytest.raises(ValueError):
        measure(lambda: None, repeats=0)


def test_measure_once_devuelve_el_valor_de_la_funcion():
    ms, valor = measure_once(lambda: "resultado")
    assert valor == "resultado"
    assert ms >= 0.0


def test_veredicto_cumple_y_no_cumple():
    ok = BenchmarkResult("Lectura desde caché", "UHF", mediana_ms=12.0, min_ms=10.0, max_ms=15.0,
                         repeticiones=5, objetivo_ms=200.0)
    mal = BenchmarkResult("Cambio de señal", "AE", mediana_ms=310.0, min_ms=300.0, max_ms=330.0,
                          repeticiones=5, objetivo_ms=250.0)
    assert ok.cumple is True and ok.veredicto == "CUMPLE"
    assert mal.cumple is False and mal.veredicto == "NO CUMPLE"


def test_operacion_sin_objetivo_no_tiene_veredicto():
    # El PROMPT §9.2 pide "reportar" (sin umbral) para ingesta y métrica puntual: eso no
    # es un resultado sin medir, es un número que se publica tal cual.
    r = BenchmarkResult("Ingesta", "AE", mediana_ms=31_000.0, min_ms=31_000.0, max_ms=31_000.0,
                        repeticiones=1, objetivo_ms=None)
    assert r.cumple is None
    assert r.veredicto == "—"


def test_tabla_markdown_tiene_una_fila_por_resultado_y_separa_sensores():
    resultados = [
        BenchmarkResult("Render gráfica #1", "UHF", 146.0, 140.0, 160.0, 5, objetivo_ms=2000.0),
        BenchmarkResult("Render gráfica #1", "AE", 76.0, 70.0, 90.0, 5, objetivo_ms=2000.0),
    ]
    tabla = format_markdown_table(resultados)
    filas = [line for line in tabla.splitlines() if line.startswith("| Render")]
    assert len(filas) == 2
    assert "| UHF |" in filas[0] and "| AE |" in filas[1]
    assert "CUMPLE" in filas[0]


def test_reporte_json_es_serializable_y_conserva_el_veredicto():
    resultados = [BenchmarkResult("Filtro + propagación", "UHF", 45.0, 40.0, 50.0, 5, objetivo_ms=200.0,
                                  notas={"n_excluidas": 624})]
    payload = to_json_dict(resultados, {"dataset": "med_5_ago_3.hdf5"})
    recargado = json.loads(json.dumps(payload, ensure_ascii=False))
    assert recargado["metadata"]["dataset"] == "med_5_ago_3.hdf5"
    assert recargado["resultados"][0]["cumple"] is True
    assert recargado["resultados"][0]["notas"]["n_excluidas"] == 624
