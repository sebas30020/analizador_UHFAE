"""Conversión de eje temporal para las gráficas tipo #1 y #3 (PROMPT §5.1, §5.3):
minutos transcurridos desde el inicio del experimento, en vez de timestamp UNIX crudo
(ilegible, valores ~1.7e9). Módulo puro, sin dependencia de Dash -- la gráfica #2
(ui/components/graph_signal.py, eje intra-señal µs/ms) y el timestamp UTC absoluto del
panel de metadatos (ui/components/metadata_panel.py, PROMPT §5.2) NO usan este módulo.
"""
from __future__ import annotations

import numpy as np

TIME_AXIS_TITLE = "Tiempo transcurrido (min)"


def to_elapsed_minutes(timestamps: np.ndarray, t0: float) -> np.ndarray:
    """Convierte timestamps UNIX absolutos (segundos) a minutos transcurridos desde ``t0``."""
    return (timestamps - t0) / 60.0


def elapsed_minutes_to_unix_seconds(minutes: float, t0: float) -> float:
    """Inversa de :func:`to_elapsed_minutes` -- usada al traducir un clic en la gráfica
    (ahora en minutos transcurridos) de vuelta a timestamp UNIX antes de
    ``AppState.nearest_index_for_timestamp`` (que sigue operando en segundos crudos)."""
    return t0 + minutes * 60.0
