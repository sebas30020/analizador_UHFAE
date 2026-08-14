"""Panel de metadatos de la señal en pantalla (PROMPT §5.2: "trigger y escala vertical
de la señal en pantalla, junto con su timestamp absoluto e índice", visible siempre)."""
from __future__ import annotations

from datetime import datetime, timezone

from dash import html


def build_metadata_panel(
    index: int,
    n_total: int,
    timestamp: float,
    trigger: float,
    vrange: float,
    is_decimated: bool,
) -> html.Div:
    dt_str = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    return html.Div(
        className="metadata-panel",
        children=[
            html.Span(f"Índice: {index} / {max(n_total - 1, 0)}", className="meta-item"),
            html.Span(f"Timestamp: {timestamp:.6f} s ({dt_str} UTC)", className="meta-item"),
            html.Span(f"Trigger: {trigger:.6g} V", className="meta-item"),
            html.Span(f"Escala vertical: {vrange:.6g}", className="meta-item"),
            html.Span("Vista diezmada" if is_decimated else "Resolución completa",
                       className="meta-item meta-decimated" if is_decimated else "meta-item"),
        ],
    )
