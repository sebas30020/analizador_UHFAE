"""Construcción de claves de caché deterministas (FASE0_DISENO_Analizador_UHF_AE.md §7).

```
cache_key = sha256(json_canonico({
    "dataset_id": ...,
    "sensor": "UHF" | "AE",
    "metric_id": ...,
    "metric_version": <int>,
    "metric_params": {...ordenados...},
    "normalization_version": ...,
    "grouping": {mode, value, partial_policy, [reducer, percentile_q]} | null
}))
```

Cambiar cualquier campo produce una clave distinta -> invalidación automática, nunca se
lee por error una entrada calculada con una versión/parámetro anterior.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Literal

GroupingMode = Literal["by_time", "by_count"]

# Única política de grupo parcial implementada hasta ahora (core/grouping.py siempre
# "calcula y marca", nunca descarta -- ver PROMPT §4.2). Se deja como campo explícito
# en la clave para que, si en el futuro se implementa la política de descarte, las
# entradas de caché existentes no se confundan con las nuevas.
PARTIAL_POLICY = "include_marked"


def _canonical_params(params: dict[str, Any] | None) -> dict[str, Any]:
    return dict(sorted((params or {}).items()))


def build_grouping_spec(
    mode: GroupingMode,
    value: float,
    reducer: str | None = None,
    percentile_q: float | None = None,
) -> dict[str, Any]:
    """Construye el sub-objeto ``grouping`` de la clave. ``reducer``/``percentile_q``
    solo aplican al régimen "grupo vía reducción genérica" -- se omiten (no ``None``)
    para las métricas de grupo intrínsecas, así ambos regímenes no colisionan de forma
    accidental si algún día comparten mode/value.
    """
    spec: dict[str, Any] = {"mode": mode, "value": value, "partial_policy": PARTIAL_POLICY}
    if reducer is not None:
        spec["reducer"] = reducer
        if reducer == "percentile":
            spec["percentile_q"] = percentile_q
    return spec


def build_cache_key(
    dataset_id: str,
    sensor: str,
    metric_id: str,
    metric_version: int,
    normalization_version: str,
    metric_params: dict[str, Any] | None = None,
    grouping: dict[str, Any] | None = None,
) -> str:
    """Retorna solo el hash sha256 (hex) -- atajo sobre :func:`build_cache_key_with_json`
    para el caso común en que no hace falta el JSON canónico."""
    digest, _ = build_cache_key_with_json(
        dataset_id, sensor, metric_id, metric_version, normalization_version, metric_params, grouping
    )
    return digest


def build_cache_key_with_json(
    dataset_id: str,
    sensor: str,
    metric_id: str,
    metric_version: int,
    normalization_version: str,
    metric_params: dict[str, Any] | None = None,
    grouping: dict[str, Any] | None = None,
) -> tuple[str, str]:
    """Como :func:`build_cache_key`, pero también retorna el JSON canónico (para
    guardarlo en el índice y facilitar depuración/purga por inspección humana)."""
    payload = {
        "dataset_id": dataset_id,
        "sensor": sensor,
        "metric_id": metric_id,
        "metric_version": metric_version,
        "metric_params": _canonical_params(metric_params),
        "normalization_version": normalization_version,
        "grouping": grouping,
    }
    canonical_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return digest, canonical_json
