"""Registro de proceso de las series (timestamps, values) que las gráficas #3
actualmente muestran, para que la línea de referencia
(``archivos_md/prompt-linea-referencia.md``) pueda recalcular su promedio al cambiar
``t`` **sin volver a pasar por el caché ni por el motor de métricas**.

Mismo nivel arquitectónico que ``ui/state.py::AppState``: un singleton de proceso, no de
pestaña ni de sesión (ver ``docs/ARQUITECTURA.md`` §9, "el estado es de proceso"). Vive
en ``ui/`` y no en ``viz/`` porque su ciclo de vida está atado al de los callbacks de
Dash -- ``viz/reference_line.py`` se mantiene puro, sin ningún estado.

``ui/callbacks/sensor_window_callbacks.py::_on_refresh_metrics`` es el único escritor:
sustituye el registro **completo** cada vez que reconstruye las gráficas #3 (mismos
``Input`` que ya disparaban ese callback -- selección de métricas, dataset, filtro,
agrupamiento). El callback de la línea de referencia (disparado por ``t`` o por el
selector de visibilidad) es un lector puro que nunca dispara ese refresco.

Acotado por construcción, no por una cota explícita: el tamaño nunca excede el número de
gráficas #3 actualmente seleccionadas, porque cada llamada a :meth:`replace_all`
descarta la generación anterior entera -- las series de una selección o un dataset ya
abandonados quedan sin referencias y se liberan con el resto del *frame* que las
construyó, no se acumulan entre datasets (mismo riesgo que el episodio de agotamiento de
memoria corregido en el commit ``bd5814d``, evitado aquí por diseño).

Las sumas de prefijo (:func:`viz.reference_line.build_prefix_sums`) se calculan de
forma perezosa -- la primera vez que :meth:`mean_until` las necesita para una gráfica
dada -- y se reutilizan mientras el registro no cambie: "el promedio se recalcula
únicamente cuando cambia ``t`` o cuando cambia el conjunto de datos subyacente"
(PROMPT §4.1). Conmutar la visibilidad de la línea nunca llega a construirlas si el
usuario no la activa.
"""
from __future__ import annotations

import numpy as np

from viz.reference_line import PrefixSums, build_prefix_sums, mean_until


class ReferenceSeriesRegistry:
    def __init__(self) -> None:
        self._series: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        self._prefix_sums: dict[str, PrefixSums] = {}

    def replace_all(self, series: dict[str, tuple[np.ndarray, np.ndarray]]) -> None:
        """Sustituye el registro completo por la generación actual de gráficas #3.
        ``series`` mapea ``option_value`` (mismo id que ``{"type": "graph-metric",
        "index": option_value}``) a ``(timestamps, values)`` -- referencias a los
        arrays ya construidos por ``_on_refresh_metrics``, no copias."""
        self._series = series
        self._prefix_sums = {}

    def clear(self) -> None:
        self.replace_all({})

    def mean_until(self, option_value: str, t: float) -> float | None:
        """Promedio acumulado desde el inicio del experimento hasta el minuto ``t``
        para la gráfica ``option_value``. ``None`` si la gráfica no está en el registro
        (no seleccionada, o `t` deja el intervalo sin muestras finitas)."""
        pair = self._series.get(option_value)
        if pair is None:
            return None
        prefix = self._prefix_sums.get(option_value)
        if prefix is None:
            prefix = build_prefix_sums(*pair)
            self._prefix_sums[option_value] = prefix
        return mean_until(prefix, t)


_registry = ReferenceSeriesRegistry()


def get_reference_registry() -> ReferenceSeriesRegistry:
    return _registry
