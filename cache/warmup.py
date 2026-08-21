"""Precalentamiento en segundo plano (PROMPT maestro §8.2): al cargar un dataset,
calcular de forma asíncrona las métricas más usadas sin bloquear la interfaz.

``warm_cache`` es síncrona y reutilizable (fácil de probar); ``start_background_warmup``
la lanza en un hilo daemon y es lo que llamaría la UI (Fase 4+) al abrir un dataset.

Nota de concurrencia: cada llamada a ``start_background_warmup`` abre su **propia**
instancia de :class:`~cache.backend.SqliteHdf5CacheBackend` dentro del hilo, en vez de
recibir una ya abierta -- los objetos ``sqlite3.Connection`` no son seguros de compartir
entre hilos por defecto, y abrir uno nuevo por hilo evita ese problema de raíz sin
necesitar ``check_same_thread=False`` ni locks explícitos.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from cache.backend import SqliteHdf5CacheBackend
from cache.service import get_or_compute_group_intrinsic, get_or_compute_group_reduction, get_or_compute_puntual
from core.grouping import GroupingMode
from core.models import SensorConfig, SensorName, SignalBlock

Regimen = str  # "puntual" | "grupo_reduccion" | "grupo_intrinseca"

_logger = logging.getLogger("analizador.warmup")


@dataclass(frozen=True)
class WarmupSpec:
    sensor: SensorName
    metric_id: str
    regimen: Regimen
    params: dict | None = None
    grouping_mode: GroupingMode | None = None
    grouping_value: float | None = None
    reducer: str = "median"
    percentile_q: float = 75.0


def default_warmup_specs(sensor: SensorName, grouping_mode: GroupingMode = "by_time", grouping_value: float = 60.0) -> list[WarmupSpec]:
    """Selección razonable de métricas "más usadas" por defecto: las puntuales más
    comunes de cada dominio, más las 3 tasas de grupo. Ajustable por el llamador --
    esto es solo un valor por defecto, no una lista cerrada.
    """
    puntuales = ["rms", "vpp", "crest_factor", "kurtosis", "energia_relativa", "feq"]
    specs = [WarmupSpec(sensor=sensor, metric_id=m, regimen="puntual") for m in puntuales]
    specs += [
        WarmupSpec(
            sensor=sensor, metric_id=m, regimen="grupo_intrinseca",
            grouping_mode=grouping_mode, grouping_value=grouping_value,
        )
        for m in ("tasa_pulsos", "tasa_energia", "tasa_rafagas")
    ]
    return specs


def warm_cache(
    cache: SqliteHdf5CacheBackend,
    blocks: dict[SensorName, SignalBlock],
    sensor_configs: dict[SensorName, SensorConfig],
    dataset_id: str,
    specs: list[WarmupSpec],
    cancelled: threading.Event | None = None,
) -> list[str]:
    """Ejecuta el precalentamiento síncronamente sobre ``cache``. Retorna los
    ``metric_id`` efectivamente calculados/verificados (en orden).

    ``cancelled``: si se señala, el bucle corta **entre** especificaciones y retorna lo
    que alcanzó a calcular. La granularidad es deliberadamente la spec, no algo más fino:
    una métrica puntual es una sola llamada vectorizada de numpy sobre la matriz entera y
    no hay dónde interrumpirla. Lo que importa es que el corte ocurra antes de empezar la
    siguiente, que es donde está el trabajo caro (medido: `kurtosis` sobre las 20 574
    señales AE de med_5_ago_3 tarda ~26 s ella sola).
    """
    done: list[str] = []
    for spec in specs:
        if cancelled is not None and cancelled.is_set():
            _logger.info(
                "etapa=cache.warmup evento=cancelado dataset_id=%s calculadas=%d de=%d",
                dataset_id, len(done), len(specs),
            )
            break
        if spec.sensor not in blocks:
            continue
        block = blocks[spec.sensor]
        cfg = sensor_configs[spec.sensor]

        try:
            if spec.regimen == "puntual":
                get_or_compute_puntual(cache, block, cfg, dataset_id, spec.metric_id, params=spec.params)
            elif spec.regimen == "grupo_reduccion":
                assert spec.grouping_mode is not None and spec.grouping_value is not None
                get_or_compute_group_reduction(
                    cache, block, cfg, dataset_id, spec.metric_id, spec.grouping_mode, spec.grouping_value,
                    reducer=spec.reducer, percentile_q=spec.percentile_q, params=spec.params,
                )
            elif spec.regimen == "grupo_intrinseca":
                assert spec.grouping_mode is not None and spec.grouping_value is not None
                get_or_compute_group_intrinsic(
                    cache, block, cfg, dataset_id, spec.metric_id, spec.grouping_mode, spec.grouping_value,
                    params=spec.params,
                )
            else:
                raise ValueError(f"Régimen de warmup desconocido: '{spec.regimen}'")
        except Exception:
            # Una métrica que falla (p. ej. MemoryError en un dataset grande) no debe
            # tumbar el resto del precalentamiento -- ni las demás métricas del mismo
            # sensor, ni las del otro sensor que vengan después en `specs`.
            _logger.exception(
                "etapa=cache.warmup error=fallo_metrica sensor=%s metric_id=%s regimen=%s",
                spec.sensor, spec.metric_id, spec.regimen,
            )
            continue
        done.append(spec.metric_id)
    return done


def start_background_warmup(
    cache_dir: str | Path,
    blocks: dict[SensorName, SignalBlock],
    sensor_configs: dict[SensorName, SensorConfig],
    dataset_id: str,
    specs: list[WarmupSpec],
    on_complete: Callable[[list[str]], None] | None = None,
    cancelled: threading.Event | None = None,
) -> threading.Thread:
    """Lanza ``warm_cache`` en un hilo daemon y retorna de inmediato (no bloquea).

    ``cancelled`` se propaga a ``warm_cache``: quien lance el hilo puede señalarlo para
    que abandone un dataset que ya no está en pantalla. Además de ahorrar CPU, cortar
    libera ``blocks`` -- mientras el hilo corre, el marco de ``_run`` mantiene viva la
    matriz completa del sensor (~1 GB en med_5_ago_3.hdf5), aunque la interfaz ya haya
    cargado otro archivo.
    """

    def _run() -> None:
        cache = SqliteHdf5CacheBackend(cache_dir)
        try:
            done = warm_cache(cache, blocks, sensor_configs, dataset_id, specs, cancelled=cancelled)
        except Exception:
            # El precalentamiento es puro adelanto de trabajo: si una métrica falla
            # (dataset degenerado, métrica retirada del registro), la aplicación debe
            # seguir sirviendo -- el usuario solo pagará ese cálculo cuando lo pida.
            _logger.exception("etapa=cache.warmup error=fallo_precalentamiento")
            return
        finally:
            cache.close()
        if on_complete is not None:
            on_complete(done)

    thread = threading.Thread(target=_run, daemon=True, name="cache-warmup")
    thread.start()
    return thread
