"""Piezas puras del arnés de benchmarks: medición repetida y formato del reporte.

Separado de ``run_benchmarks.py`` (que necesita un dataset real y tarda minutos) para
que la lógica de agregación y de veredicto se pueda probar en la suite normal con
funciones sintéticas de duración conocida.
"""
from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class BenchmarkResult:
    """Una operación medida, con su objetivo del §9.2 si lo tiene.

    ``objetivo_ms=None`` significa "reportar, sin umbral" -- el PROMPT pide eso
    explícitamente para la ingesta y para el cálculo de una métrica puntual, así que un
    resultado sin objetivo **no** es un resultado sin veredicto: es un número que se
    publica tal cual.
    """

    operacion: str
    sensor: str
    mediana_ms: float
    min_ms: float
    max_ms: float
    repeticiones: int
    objetivo_ms: float | None = None
    notas: dict[str, Any] = field(default_factory=dict)

    @property
    def cumple(self) -> bool | None:
        if self.objetivo_ms is None:
            return None
        return self.mediana_ms <= self.objetivo_ms

    @property
    def veredicto(self) -> str:
        if self.cumple is None:
            return "—"
        return "CUMPLE" if self.cumple else "NO CUMPLE"


def measure(fn: Callable[[], Any], repeats: int = 5, warmup: int = 1) -> tuple[float, float, float]:
    """Ejecuta ``fn`` ``warmup + repeats`` veces y retorna ``(mediana, min, max)`` en ms
    sobre las ``repeats`` últimas.

    Las corridas de calentamiento se descartan porque la primera llamada paga costos que
    no se repiten y que no forman parte de la operación que se quiere medir (importar
    submódulos perezosos de plotly, primer ``discover_metrics()``, primeras páginas de
    memoria del array). Medir sin descartarlas reportaría un tiempo que el usuario solo
    ve una vez por sesión.
    """
    if repeats < 1:
        raise ValueError("repeats debe ser >= 1")

    for _ in range(max(0, warmup)):
        fn()

    samples_ms: list[float] = []
    for _ in range(repeats):
        started = time.perf_counter()
        fn()
        samples_ms.append((time.perf_counter() - started) * 1000.0)

    return statistics.median(samples_ms), min(samples_ms), max(samples_ms)


def measure_once(fn: Callable[[], Any]) -> tuple[float, Any]:
    """Mide una sola ejecución (para operaciones caras e irrepetibles como la ingesta en
    frío) y retorna ``(ms, valor_devuelto)``."""
    started = time.perf_counter()
    value = fn()
    return (time.perf_counter() - started) * 1000.0, value


def format_markdown_table(results: list[BenchmarkResult]) -> str:
    """Tabla markdown con una fila por (operación, sensor), en el orden dado."""
    header = (
        "| Operación | Sensor | Mediana | Min | Max | Objetivo (§9.2) | Veredicto |\n"
        "|---|---|---:|---:|---:|---:|---|"
    )
    rows = []
    for r in results:
        objetivo = "reportar" if r.objetivo_ms is None else f"< {_ms(r.objetivo_ms)}"
        rows.append(
            f"| {r.operacion} | {r.sensor} | {_ms(r.mediana_ms)} | {_ms(r.min_ms)} | "
            f"{_ms(r.max_ms)} | {objetivo} | {r.veredicto} |"
        )
    return "\n".join([header, *rows])


def _ms(value: float) -> str:
    """Formato legible: ms por debajo de 1 s, segundos por encima."""
    if value >= 1000.0:
        return f"{value / 1000.0:.2f} s"
    if value >= 10.0:
        return f"{value:.0f} ms"
    return f"{value:.1f} ms"


def to_json_dict(results: list[BenchmarkResult], metadata: dict[str, Any]) -> dict[str, Any]:
    """Estructura serializable del reporte completo (para comparar corridas entre sí)."""
    return {
        "metadata": metadata,
        "resultados": [
            {
                "operacion": r.operacion,
                "sensor": r.sensor,
                "mediana_ms": round(r.mediana_ms, 3),
                "min_ms": round(r.min_ms, 3),
                "max_ms": round(r.max_ms, 3),
                "repeticiones": r.repeticiones,
                "objetivo_ms": r.objetivo_ms,
                "cumple": r.cumple,
                "notas": r.notas,
            }
            for r in results
        ],
    }
