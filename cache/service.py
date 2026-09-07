"""Fachada de caché (PROMPT maestro §8.1): intercepta toda solicitud de métrica.

1. Verifica primero si la métrica ya fue calculada (misma clave determinista, §7).
2. Si existe -> devuelve directamente los puntos, sin recalcular.
3. Si no existe -> calcula vía ``metrics/engine.py``, persiste, y devuelve los puntos.

El caché siempre opera sobre el **conjunto completo** de señales del bloque (§8.2 del
PROMPT: "el caché almacena resultados sobre el conjunto completo de señales, no sobre
subconjuntos filtrados") -- el filtrado interactivo por selección del usuario (Fase 6)
se aplica como máscara sobre estos resultados ya cacheados, nunca dispara un recálculo.
"""
from __future__ import annotations

from collections import OrderedDict
import threading
from typing import Any

import numpy as np

from cache.backend import CacheBackend
from cache.keys import build_cache_key_with_json, build_grouping_spec
from core.grouping import GroupingMode, resolve_groups
from core.models import SensorConfig, SignalBlock
from core.normalization import NORMALIZATION_VERSION
from metrics.engine import compute_group_intrinsic, compute_group_reduction, compute_puntual
from metrics.registry import get_metric
from utils.profiling import stage

_GROUP_MEMO_MAX_ENTRIES = 128
_GROUP_MEMO_LOCK = threading.Lock()
_GROUP_MEMO: OrderedDict[tuple[str, int], tuple[np.ndarray, np.ndarray, np.ndarray]] = OrderedDict()


def clear_group_memo() -> None:
    """Limpia el memo LRU en memoria de régimen de grupo."""
    with _GROUP_MEMO_LOCK:
        _GROUP_MEMO.clear()



def _sin_exclusiones(active_mask: np.ndarray | None) -> np.ndarray | None:
    """Una máscara todo-True no excluye nada, así que es equivalente a no tener filtro.
    Normalizarla a None aquí es lo que permite que el régimen de grupo entre por el
    caché cuando el usuario no ha filtrado: el bypass sigue siendo total en cuanto haya
    una sola señal excluida."""
    if active_mask is None or bool(active_mask.all()):
        return None
    return active_mask


def get_or_compute_puntual(
    cache: CacheBackend,
    block: SignalBlock,
    sensor_config: SensorConfig,
    dataset_id: str,
    metric_id: str,
    params: dict | None = None,
    n_workers: int = 1,
    active_mask: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Régimen puntual: ``(timestamps, values)`` desde caché si existe, si no calcula
    con ``metrics.engine.compute_puntual`` y persiste antes de devolver.

    ``active_mask`` (Fase 6, PROMPT §7): máscara de filtrado interactivo del usuario. El
    caché siempre se consulta/persiste sobre el conjunto completo sin filtrar (§8.2) --
    si se pasa una máscara, se aplica como filtro posterior puro sobre el resultado ya
    obtenido, nunca se reescribe en caché ni dispara un recálculo.
    """
    active_mask = _sin_exclusiones(active_mask)
    definition = get_metric(metric_id)
    key, canonical_json = build_cache_key_with_json(
        dataset_id=dataset_id,
        sensor=sensor_config.name,
        metric_id=metric_id,
        metric_version=definition.version,
        normalization_version=NORMALIZATION_VERSION,
        metric_params=params,
        grouping=None,
    )

    with stage("cache.puntual", sensor=sensor_config.name, metrica=metric_id) as ctx:
        hit = cache.get(key)
        ctx["cache"] = "hit" if hit is not None else "miss"
        if hit is not None:
            timestamps, values = hit.timestamps, hit.values
        else:
            timestamps, values = compute_puntual(block, sensor_config, metric_id, params=params, n_workers=n_workers)
            cache.put(key, canonical_json, dataset_id, sensor_config.name, metric_id, definition.version, timestamps, values)

    if active_mask is not None:
        valid_idx = np.where(block.valid_mask)[0]
        keep = active_mask[valid_idx]
        timestamps, values = timestamps[keep], values[keep]

    return timestamps, values


def get_or_compute_group_reduction(
    cache: CacheBackend,
    block: SignalBlock,
    sensor_config: SensorConfig,
    dataset_id: str,
    metric_id: str,
    grouping_mode: GroupingMode,
    grouping_value: float,
    reducer: str = "median",
    percentile_q: float = 75.0,
    params: dict | None = None,
    active_mask: np.ndarray | None = None,
    filter_version: int | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Régimen grupo vía reducción genérica: ``(center_timestamps, values, is_partial)``.

    ``active_mask`` (Fase 6, PROMPT §7): a diferencia del régimen puntual, los
    agregados de grupo SÍ cambian si se excluye una señal (§7.2, "reconciliación") --
    con máscara activa que excluye al menos una señal, esta función bypasea el caché de disco
    por completo (nunca lee ni escribe en SQLite/HDF5) y recalcula vía ``metrics.engine``,
    o sirve desde el memo LRU en RAM si coincide ``(canonical_cache_key, filter_version)`` (B3).
    """
    active_mask = _sin_exclusiones(active_mask)
    definition = get_metric(metric_id)
    with stage("cache.grupo_reduccion", sensor=sensor_config.name, metrica=metric_id) as ctx:
        grouping_spec = build_grouping_spec(grouping_mode, grouping_value, reducer=reducer, percentile_q=percentile_q)
        key, canonical_json = build_cache_key_with_json(
            dataset_id=dataset_id,
            sensor=sensor_config.name,
            metric_id=metric_id,
            metric_version=definition.version,
            normalization_version=NORMALIZATION_VERSION,
            metric_params=params,
            grouping=grouping_spec,
        )

        if active_mask is not None:
            if filter_version is not None:
                memo_key = (key, filter_version)
                with _GROUP_MEMO_LOCK:
                    if memo_key in _GROUP_MEMO:
                        _GROUP_MEMO.move_to_end(memo_key)
                        ctx["cache"] = "hit"
                        t, v, p = _GROUP_MEMO[memo_key]
                        return t.copy(), v.copy(), p.copy()

            ctx["cache"] = "bypass"
            groups = resolve_groups(block.timestamps, mode=grouping_mode, value=grouping_value)
            t, v, p = compute_group_reduction(
                block, sensor_config, metric_id, groups, reducer=reducer, percentile_q=percentile_q,
                params=params, extra_mask=active_mask,
            )

            if filter_version is not None:
                memo_key = (key, filter_version)
                with _GROUP_MEMO_LOCK:
                    _GROUP_MEMO[memo_key] = (t, v, p)
                    if len(_GROUP_MEMO) > _GROUP_MEMO_MAX_ENTRIES:
                        _GROUP_MEMO.popitem(last=False)

            return t, v, p

        hit = cache.get(key)
        ctx["cache"] = "hit" if hit is not None else "miss"
        if hit is not None:
            is_partial = hit.is_partial if hit.is_partial is not None else np.zeros(hit.values.shape[0], dtype=bool)
            return hit.timestamps, hit.values, is_partial

        groups = resolve_groups(block.timestamps, mode=grouping_mode, value=grouping_value)
        t, v, p = compute_group_reduction(
            block, sensor_config, metric_id, groups, reducer=reducer, percentile_q=percentile_q, params=params
        )
        cache.put(key, canonical_json, dataset_id, sensor_config.name, metric_id, definition.version, t, v, is_partial=p)
        return t, v, p


def get_or_compute_group_intrinsic(
    cache: CacheBackend,
    block: SignalBlock,
    sensor_config: SensorConfig,
    dataset_id: str,
    metric_id: str,
    grouping_mode: GroupingMode,
    grouping_value: float,
    params: dict | None = None,
    active_mask: np.ndarray | None = None,
    filter_version: int | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Régimen grupo intrínseco (tasa_pulsos, tasa_energia, tasa_rafagas).

    ``active_mask`` (Fase 6, PROMPT §7): ver :func:`get_or_compute_group_reduction` --
    misma estrategia de bypass total de caché de disco y memo LRU en RAM (B3) cuando hay filtro activo.
    """
    active_mask = _sin_exclusiones(active_mask)
    definition = get_metric(metric_id)
    with stage("cache.grupo_intrinseca", sensor=sensor_config.name, metrica=metric_id) as ctx:
        grouping_spec = build_grouping_spec(grouping_mode, grouping_value)  # sin reducer: no aplica
        key, canonical_json = build_cache_key_with_json(
            dataset_id=dataset_id,
            sensor=sensor_config.name,
            metric_id=metric_id,
            metric_version=definition.version,
            normalization_version=NORMALIZATION_VERSION,
            metric_params=params,
            grouping=grouping_spec,
        )

        if active_mask is not None:
            if filter_version is not None:
                memo_key = (key, filter_version)
                with _GROUP_MEMO_LOCK:
                    if memo_key in _GROUP_MEMO:
                        _GROUP_MEMO.move_to_end(memo_key)
                        ctx["cache"] = "hit"
                        t, v, p = _GROUP_MEMO[memo_key]
                        return t.copy(), v.copy(), p.copy()

            ctx["cache"] = "bypass"
            groups = resolve_groups(block.timestamps, mode=grouping_mode, value=grouping_value)
            t, v, p = compute_group_intrinsic(block, sensor_config, metric_id, groups, params=params, extra_mask=active_mask)

            if filter_version is not None:
                memo_key = (key, filter_version)
                with _GROUP_MEMO_LOCK:
                    _GROUP_MEMO[memo_key] = (t, v, p)
                    if len(_GROUP_MEMO) > _GROUP_MEMO_MAX_ENTRIES:
                        _GROUP_MEMO.popitem(last=False)

            return t, v, p

        hit = cache.get(key)
        ctx["cache"] = "hit" if hit is not None else "miss"
        if hit is not None:
            is_partial = hit.is_partial if hit.is_partial is not None else np.zeros(hit.values.shape[0], dtype=bool)
            return hit.timestamps, hit.values, is_partial

        groups = resolve_groups(block.timestamps, mode=grouping_mode, value=grouping_value)
        t, v, p = compute_group_intrinsic(block, sensor_config, metric_id, groups, params=params)
        cache.put(key, canonical_json, dataset_id, sensor_config.name, metric_id, definition.version, t, v, is_partial=p)
        return t, v, p
