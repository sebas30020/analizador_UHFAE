"""Modelo puro de dominio para filtrado declarativo por métricas puntuales.

Encapsula la definición de condiciones de filtrado sobre métricas, operadores de
desigualdad, evaluación vectorizada en NumPy con verificación explícita de finitud,
dispersión sobre índices globales, composición booleana AND, normalización de umbrales
y detección de contradicciones lógicas.

No depende de Dash, Plotly, SQLite, HDF5 ni de la capa de caché.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Literal, Sequence

import numpy as np

FilterOperator = Literal[">=", "<="]


@dataclass(frozen=True)
class MetricCondition:
    """Condición declarativa de filtrado por una métrica puntual.

    Permite acceso por atributo ``metric_name`` y ``metric_id`` indistintamente.
    """
    metric_name: str
    operator: FilterOperator
    threshold: float
    params: tuple[tuple[str, Any], ...] = ()

    def __init__(
        self,
        metric_name: str = "",
        operator: FilterOperator = ">=",
        threshold: float = 0.0,
        params: tuple[tuple[str, Any], ...] = (),
        metric_id: str | None = None,
    ) -> None:
        name = metric_id if metric_id is not None else metric_name
        object.__setattr__(self, "metric_name", str(name))
        object.__setattr__(self, "operator", operator)
        object.__setattr__(self, "threshold", float(threshold))
        object.__setattr__(self, "params", params)

    @property
    def metric_id(self) -> str:
        return self.metric_name


def condition_key(c: MetricCondition) -> str:
    """Clave canónica estable para identificar la condición en la UI y controles."""
    metric = c.metric_name or c.metric_id
    if not c.params:
        return f"{metric}|{c.operator}|{c.threshold}"
    params_str = ",".join(f"{k}={v}" for k, v in sorted(c.params))
    return f"{metric}|{params_str}|{c.operator}|{c.threshold}"


def parse_condition_key(key: str) -> MetricCondition | None:
    """Parsea una clave canónica generada por :func:`condition_key`."""
    parts = key.split("|")
    if len(parts) == 3:
        metric, op, thresh_str = parts
        if op not in (">=", "<="):
            return None
        try:
            return MetricCondition(metric_name=metric, operator=op, threshold=float(thresh_str))  # type: ignore[arg-type]
        except ValueError:
            return None
    elif len(parts) == 4:
        metric, params_str, op, thresh_str = parts
        if op not in (">=", "<="):
            return None
        try:
            params_list: list[tuple[str, Any]] = []
            for item in params_str.split(","):
                if "=" in item:
                    k, v = item.split("=", 1)
                    params_list.append((k, v))
            return MetricCondition(
                metric_name=metric,
                operator=op,  # type: ignore[arg-type]
                threshold=float(thresh_str),
                params=tuple(params_list),
            )
        except ValueError:
            return None
    return None


def evaluate_condition(
    arg1: MetricCondition | np.ndarray,
    arg2: MetricCondition | np.ndarray,
) -> np.ndarray:
    """Evaluación puramente vectorizada en NumPy.

    Garantiza semántica 'sin valor o no finito (NaN/Inf) => no pasa el filtro (False)'.
    Soporta ``evaluate_condition(condition, values)`` y ``evaluate_condition(values, condition)``.
    """
    if isinstance(arg1, MetricCondition):
        condition = arg1
        values = np.asarray(arg2, dtype=np.float64)
    else:
        values = np.asarray(arg1, dtype=np.float64)
        if not isinstance(arg2, MetricCondition):
            raise TypeError("Una de las entradas debe ser MetricCondition")
        condition = arg2

    finite_mask = np.isfinite(values)
    if condition.operator == ">=":
        return np.asarray((values >= condition.threshold) & finite_mask, dtype=bool)
    elif condition.operator == "<=":
        return np.asarray((values <= condition.threshold) & finite_mask, dtype=bool)
    raise ValueError(f"Operador no soportado: {condition.operator}")


def scatter_to_full(partial: np.ndarray, valid_idx: np.ndarray, n_total: int) -> np.ndarray:
    """Mapea una máscara de señales válidas ``(N_valid,)`` a la longitud total ``(N_total,)``.

    Las señales con ``valid_mask=False`` quedan en ``False``.
    """
    full = np.zeros(n_total, dtype=bool)
    if valid_idx.size > 0:
        full[valid_idx] = partial
    return full


def combine_conditions(masks: Iterable[np.ndarray], n_total: int | None = None) -> np.ndarray:
    """Composición booleana AND de múltiples máscaras.

    Si la lista está vacía, retorna un array todo-True. Si se provee ``n_total``,
    de longitud ``n_total``; si no, de la longitud de la primera máscara (o vacío si masks está vacío).
    """
    seq = list(masks)
    if not seq:
        if n_total is not None:
            return np.ones(n_total, dtype=bool)
        return np.array([], dtype=bool)
    if n_total is None:
        n_total = len(seq[0])
    res = np.ones(n_total, dtype=bool)
    for m in seq:
        res &= np.asarray(m, dtype=bool)
    return res


# Alias contractual según PLAN_OPTIMIZACION_Y_FILTRADO.md §E.1
combine_masks = combine_conditions


def normalize_threshold(arg1: Any, arg2: Any = None) -> float | None:
    """Limpia y valida la entrada numérica del usuario.

    Soporta tanto ``normalize_threshold(raw)`` como ``normalize_threshold(metric_name, raw)``.
    Retorna un ``float`` finito si es válido, o ``None`` si es nulo, no numérico o no finito.
    """
    raw = arg2 if arg2 is not None else arg1
    if raw is None:
        return None
    if isinstance(raw, (int, float, np.integer, np.floating)):
        val = float(raw)
        return val if np.isfinite(val) else None
    if isinstance(raw, str):
        cleaned = raw.strip().replace(",", ".")
        if not cleaned:
            return None
        try:
            val = float(cleaned)
            return val if np.isfinite(val) else None
        except ValueError:
            return None
    return None


class ContradictionPair(tuple):
    """Par (MetricCondition, MetricCondition) contradictorio con representación textual."""
    def __new__(cls, c1: MetricCondition, c2: MetricCondition, message: str = "") -> ContradictionPair:
        instance = super().__new__(cls, (c1, c2))
        instance.c1 = c1  # type: ignore[attr-defined]
        instance.c2 = c2  # type: ignore[attr-defined]
        instance.message = message  # type: ignore[attr-defined]
        return instance

    def __str__(self) -> str:
        return self.message  # type: ignore[attr-defined]

    def __repr__(self) -> str:
        return f"ContradictionPair({self[0]!r}, {self[1]!r}, message={self.message!r})"  # type: ignore[attr-defined]

    def __contains__(self, item: Any) -> bool:
        if isinstance(item, str) and item in self.message:  # type: ignore[attr-defined]
            return True
        return super().__contains__(item)


def detect_contradictions(conditions: Sequence[MetricCondition]) -> list[ContradictionPair]:
    """Detecta contradicciones lógicas (min > max) puramente sobre las condiciones,
    sin consultar datos ni caché.
    """
    by_metric: dict[tuple[str, tuple[tuple[str, Any], ...]], dict[str, MetricCondition]] = {}
    for c in conditions:
        key = (c.metric_name or c.metric_id, c.params)
        if key not in by_metric:
            by_metric[key] = {}
        if c.operator == ">=":
            if ">=" not in by_metric[key] or c.threshold > by_metric[key][">="].threshold:
                by_metric[key][">="] = c
        elif c.operator == "<=":
            if "<=" not in by_metric[key] or c.threshold < by_metric[key]["<="].threshold:
                by_metric[key]["<="] = c

    warnings: list[ContradictionPair] = []
    for (metric_name, _params), ops in by_metric.items():
        if ">=" in ops and "<=" in ops:
            c_ge = ops[">="]
            c_le = ops["<="]
            if c_ge.threshold > c_le.threshold:
                msg = f"Rango vacío: {metric_name} ≥ {c_ge.threshold:g} y {metric_name} ≤ {c_le.threshold:g} se contradicen"
                warnings.append(ContradictionPair(c_ge, c_le, message=msg))
    return warnings
