"""Promedio acumulado desde el inicio del experimento hasta un minuto ``t``, para la
línea horizontal de referencia de las gráficas #3 (``archivos_md/prompt-linea-
referencia.md``). Módulo puro: sin Dash, sin Plotly, sin tocar ``metrics/`` ni
``cache/`` -- mismo patrón que ``viz/smoothing.py``.

**No es una métrica.** El promedio se calcula sobre valores ya cacheados por
``cache/service.py`` (régimen puntual) o ya devueltos por el bypass de caché de grupo
(``docs/ARQUITECTURA.md`` §5); este módulo no entra en ``cache/keys.py`` ni invalida
nada.

Estrategia (PROMPT §4.1, "sumas acumuladas"): un array ordenado por ``x`` admite
``mean_until(t)`` en tiempo logarítmico -- una búsqueda binaria del índice de corte más
dos lecturas indexadas en las sumas de prefijo -- en vez de filtrar o recorrer la serie
completa en cada cambio de ``t``. Precondición: ``x`` ordenado ascendente, garantizado
aguas arriba por ``data/ingest.py`` y por ``core/grouping.py`` (misma precondición que
ya declara ``viz/smoothing.py::split_on_gaps``).

Convención del límite superior (documentada en la nota de entrega): se incluyen las
muestras con ``x <= t`` (búsqueda binaria con ``side="right"``).

Nulos y NaN: excluidos del promedio mediante un conteo de valores finitos separado del
acumulado -- nunca tratados como cero. Mismo criterio que
``viz/smoothing.py::_rolling_mean_time``.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class PrefixSums:
    """Sumas de prefijo precalculadas una sola vez por serie. ``x`` debe estar ordenado
    ascendente; los NaN de ``y`` ya están excluidos del acumulado de valores y contados
    aparte en ``cumulative_count``."""

    x: np.ndarray
    cumulative_sum: np.ndarray
    cumulative_count: np.ndarray


def build_prefix_sums(x: np.ndarray, y: np.ndarray) -> PrefixSums:
    """Precalcula, una única vez por serie, la suma acumulada de los valores finitos y
    el conteo acumulado de muestras válidas. Con esto, el promedio para cualquier ``t``
    se reduce a una búsqueda binaria más dos lecturas indexadas (:func:`mean_until`).

    ``x`` e ``y`` deben tener la misma forma; ``x`` ordenado ascendente (no se reordena
    aquí).
    """
    finite = np.isfinite(y)
    y_filled = np.where(finite, y, 0.0)
    cumulative_sum = np.concatenate(([0.0], np.cumsum(y_filled, dtype=np.float64)))
    cumulative_count = np.concatenate(([0], np.cumsum(finite)))
    return PrefixSums(x=x, cumulative_sum=cumulative_sum, cumulative_count=cumulative_count)


def mean_until(prefix: PrefixSums, t: float) -> float | None:
    """Media aritmética de los valores cuya abscisa cae en ``x <= t``, excluyendo NaN.

    Retorna ``None`` (no dibujar línea) cuando ``t`` no deja ninguna muestra finita
    dentro del intervalo -- ``t <= 0``, serie vacía, o el propio dato es todo NaN en ese
    tramo. ``t`` mayor que la última abscisa usa todas las muestras disponibles, sin
    tratarlo como error (PROMPT §2.2).
    """
    if prefix.x.shape[0] == 0:
        return None
    cut = int(np.searchsorted(prefix.x, t, side="right"))
    count = int(prefix.cumulative_count[cut])
    if count == 0:
        return None
    total = float(prefix.cumulative_sum[cut])
    return total / count
