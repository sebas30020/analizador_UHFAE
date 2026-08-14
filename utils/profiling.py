"""Instrumentación de tiempos por etapa (PROMPT maestro §9.3: "no se optimiza lo que
no se mide").

Dos formas de usarlo, ambas con el mismo destino:

>>> with stage("ingesta.sensor", sensor="UHF", n_senales=12484):
...     block = ingest_sensor(...)

>>> @profiled("metricas.puntual")
... def compute_puntual(...): ...

Cada etapa terminada produce un :class:`StageRecord` que va a dos sitios:

- Un **log estructurado** (``logging`` estándar, logger ``analizador.profiling``), una
  línea por etapa con los campos en formato ``clave=valor`` -- legible por humanos y
  parseable con ``split()``, sin arrastrar una dependencia de logging estructurado.
- Un **colector en memoria** consultable con :func:`get_records`, que es lo que usan los
  benchmarks (``benchmarks/run_benchmarks.py``) y las pruebas para afirmar sobre tiempos
  sin tener que leer texto de log.

**Desactivado por defecto** (``config/sensors.yaml`` -> ``profiling.enabled: false``).
Con la instrumentación apagada, ``stage()`` no llama ni a ``perf_counter``: entra y sale
por la rama corta, de modo que instrumentar una función que se llama mucho no le cuesta
nada medible al usuario final. La variable de entorno ``ANALIZADOR_PROFILING`` (``1``/
``0``) tiene prioridad sobre el archivo, para activarlo en una ejecución suelta sin
tocar la configuración del proyecto:

    ANALIZADOR_PROFILING=1 python scripts/run_dev_server.py
"""
from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Iterator, TypeVar

import yaml

LOGGER_NAME = "analizador.profiling"
ENV_VAR = "ANALIZADOR_PROFILING"

_logger = logging.getLogger(LOGGER_NAME)


@dataclass(frozen=True)
class StageRecord:
    """Una etapa medida. ``fields`` son los metadatos que le pasó el llamador (sensor,
    número de señales, hit/miss de caché...), no una estructura fija: cada etapa reporta
    lo que hace falta para interpretar su propio tiempo."""

    name: str
    duration_s: float
    fields: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> float:
        return self.duration_s * 1000.0

    def format_line(self) -> str:
        """``etapa=X duracion_ms=Y clave=valor...`` -- una línea por etapa."""
        parts = [f"etapa={self.name}", f"duracion_ms={self.duration_ms:.1f}"]
        parts += [f"{k}={v}" for k, v in self.fields.items()]
        return " ".join(parts)


class _Collector:
    """Colector en memoria, seguro entre hilos (el precalentamiento de caché corre en un
    hilo daemon, ``cache/warmup.py``)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: list[StageRecord] = []

    def add(self, record: StageRecord) -> None:
        with self._lock:
            self._records.append(record)

    def snapshot(self) -> list[StageRecord]:
        with self._lock:
            return list(self._records)

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


_COLLECTOR = _Collector()
_ENABLED = False
_ENABLED_LOCK = threading.Lock()


def is_enabled() -> bool:
    with _ENABLED_LOCK:
        return _ENABLED


def set_enabled(enabled: bool) -> None:
    """Activa/desactiva la instrumentación en caliente (lo usan las pruebas y los
    benchmarks; la aplicación normal la resuelve una vez con :func:`configure_from_config`)."""
    global _ENABLED
    with _ENABLED_LOCK:
        _ENABLED = bool(enabled)


def configure_from_config(config_path: str | Path, force: bool | None = None) -> bool:
    """Resuelve el estado desde ``config/sensors.yaml`` (sección ``profiling``), con la
    variable de entorno :data:`ENV_VAR` por encima del archivo y ``force`` por encima de
    todo. Retorna el estado resultante.

    Un archivo sin sección ``profiling`` equivale a desactivado -- la instrumentación
    nunca se enciende sola por omisión.
    """
    if force is not None:
        set_enabled(force)
        return is_enabled()

    env_value = os.environ.get(ENV_VAR)
    if env_value is not None:
        set_enabled(env_value.strip() not in ("", "0", "false", "False"))
        return is_enabled()

    enabled = False
    level = "INFO"
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        section = raw.get("profiling") or {}
        enabled = bool(section.get("enabled", False))
        level = str(section.get("level", "INFO"))
    except FileNotFoundError:
        enabled = False

    set_enabled(enabled)
    if enabled:
        _logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    return is_enabled()


@contextmanager
def stage(name: str, **fields: Any) -> Iterator[dict[str, Any]]:
    """Mide el bloque y lo registra como etapa ``name``.

    Cede el diccionario de campos, así el cuerpo puede añadir metadatos que solo se
    conocen al terminar (``ctx["n_grupos"] = len(groups)``). Si el bloque lanza una
    excepción, la etapa igual se registra, con ``error=<Tipo>`` -- una etapa que falla a
    los 30 s es justo la que hay que ver en el log.
    """
    if not is_enabled():
        yield fields
        return

    started = time.perf_counter()
    try:
        yield fields
    except BaseException as exc:  # noqa: BLE001 -- se re-lanza intacta
        fields["error"] = type(exc).__name__
        raise
    finally:
        record = StageRecord(name=name, duration_s=time.perf_counter() - started, fields=dict(fields))
        _COLLECTOR.add(record)
        _logger.info(record.format_line())


F = TypeVar("F", bound=Callable[..., Any])


def profiled(name: str, **fields: Any) -> Callable[[F], F]:
    """Versión decorador de :func:`stage`, para cuando la función entera es la etapa."""

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with stage(name, **fields):
                return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def get_records(name: str | None = None) -> list[StageRecord]:
    """Etapas medidas hasta ahora, en orden de finalización. ``name`` filtra por etapa."""
    records = _COLLECTOR.snapshot()
    if name is not None:
        records = [r for r in records if r.name == name]
    return records


def reset_records() -> None:
    _COLLECTOR.clear()


def summarize(records: list[StageRecord] | None = None) -> dict[str, dict[str, float]]:
    """Agrega por nombre de etapa: ``{etapa: {n, total_ms, media_ms, min_ms, max_ms}}``.

    Sin percentiles a propósito: con el puñado de repeticiones que hace un benchmark
    local, un p95 sobre 5 muestras es ruido con nombre de estadístico. Para comparar
    corridas se usa la mediana que calcula ``benchmarks/run_benchmarks.py`` sobre sus
    propias repeticiones.
    """
    records = _COLLECTOR.snapshot() if records is None else records
    grouped: dict[str, list[float]] = {}
    for record in records:
        grouped.setdefault(record.name, []).append(record.duration_ms)

    return {
        name: {
            "n": float(len(durations)),
            "total_ms": float(sum(durations)),
            "media_ms": float(sum(durations) / len(durations)),
            "min_ms": float(min(durations)),
            "max_ms": float(max(durations)),
        }
        for name, durations in sorted(grouped.items())
    }
