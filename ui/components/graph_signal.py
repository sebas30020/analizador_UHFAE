"""Gráfica tipo #2 — Señal individual (PROMPT §5.2)."""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from core.models import SensorConfig
from utils.profiling import stage
from viz.decimation import build_vertical_segments, decimate_signal_by_pixel

SIGNAL_COLOR = "#4A7BB0"
OVERLAY_COLORS = ["#B86B52", "#6BA368", "#A0639A", "#C2A83E"]


def build_signal_figure(
    sensor_config: SensorConfig,
    signal_row: np.ndarray,
    overlay_rows: list[np.ndarray] | None = None,
    x_range_natural_units: tuple[float, float] | None = None,
    n_pixels: int = 1600,
    is_raw: bool = False,
) -> tuple[go.Figure, bool]:
    """Traza una señal a resolución completa (siempre en sensores con
    ``decimate_full_view=False``, como UHF; en los demás -- AE, UHF_KS -- si el tramo
    visible ya cabe en los píxeles disponibles) o diezmada min/max por bin (vista
    completa de un sensor con ``decimate_full_view=True``).

    ``signal_row``/``overlay_rows`` deben venir **ya normalizados** por el llamador
    (``x_norm = x_raw / vrange``, PROMPT §2.4) salvo que ``is_raw=True`` -- este
    componente no normaliza nada por su cuenta, solo rotula el eje según ``is_raw``
    para que la gráfica nunca mienta sobre qué está mostrando. El valor por defecto de
    la UI es la vista normalizada, consistente con lo que miden todas las métricas
    (Vmax, RMS, etc.) -- ``is_raw=True`` es el flag explícito de depuración que exige
    el PROMPT para inspeccionar la señal cruda vs. la normalizada.

    Retorna ``(figura, esta_diezmada)`` -- el llamador usa el segundo valor para
    mostrar el aviso "vista diezmada" que exige el PROMPT §5.2.
    """
    with stage("render.grafica2", sensor=sensor_config.name) as ctx:
        t_axis = sensor_config.time_axis() * sensor_config.axis_scale  # unidad natural (µs/ms)

        if x_range_natural_units is not None:
            lo, hi = x_range_natural_units
            mask = (t_axis >= lo) & (t_axis <= hi)
            t_view, y_view = t_axis[mask], signal_row[mask]
        else:
            t_view, y_view = t_axis, signal_row

        is_decimated = sensor_config.decimate_full_view and t_view.shape[0] > n_pixels
        ctx["diezmada"] = is_decimated
        ctx["n_superpuestas"] = len(overlay_rows or [])

        fig = go.Figure()
        if is_decimated:
            t_plot, y_min, y_max = decimate_signal_by_pixel(t_view, y_view, n_pixels)
            xs, ys = build_vertical_segments(t_plot, y_min, y_max)
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line=dict(color=SIGNAL_COLOR, width=1), name="Señal (diezmada)"))
        else:
            fig.add_trace(go.Scatter(x=t_view, y=y_view, mode="lines", line=dict(color=SIGNAL_COLOR, width=1), name="Señal"))

        for i, overlay in enumerate(overlay_rows or []):
            color = OVERLAY_COLORS[i % len(OVERLAY_COLORS)]
            fig.add_trace(go.Scatter(x=t_axis, y=overlay, mode="lines", line=dict(color=color, width=1), opacity=0.6, name=f"Comparación {i+1}"))

        xaxis_kwargs: dict[str, object] = dict(title=f"Tiempo ({sensor_config.axis_unit})")
        if x_range_natural_units is not None:
            # Fija el rango explícitamente: si no, el autorange de Plotly se expandiría de
            # vuelta a la traza completa de las superposiciones (que no se recortan al
            # tramo visible) y el zoom quedaría sin efecto visual.
            xaxis_kwargs["range"] = list(x_range_natural_units)

        fig.update_layout(
            xaxis=xaxis_kwargs,
            yaxis=dict(title="Amplitud cruda (sin normalizar)" if is_raw else "Amplitud normalizada (x / vrange)"),
            margin=dict(l=60, r=20, t=30, b=40),
            height=280,
            showlegend=bool(overlay_rows),
        )
        return fig, is_decimated
