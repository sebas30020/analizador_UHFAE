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
    has_trigger_metadata: bool = True,
) -> html.Div:
    """``has_trigger_metadata=False`` (rama ``lectura_keysight``, sensores cuyo origen
    no registra nivel de disparo por señal, p. ej. ``UHF_KS``): se muestra "no
    registrado" en vez de imprimir ``trigger`` como si fuera un valor medido -- el
    reader lo rellena con ``0.0`` solo para no romper ``compute_valid_mask``, nunca fue
    una lectura real del instrumento.
    """
    dt_str = datetime.fromtimestamp(timestamp, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
    trigger_text = f"Trigger: {trigger:.6g} V" if has_trigger_metadata else "Trigger: no registrado"
    return html.Div(
        className="metadata-panel",
        children=[
            html.Span(f"Índice: {index} / {max(n_total - 1, 0)}", className="meta-item"),
            html.Span(f"Marca de tiempo: {timestamp:.6f} s ({dt_str} UTC)", className="meta-item"),
            html.Span(trigger_text, className="meta-item"),
            html.Span(f"Escala vertical: {vrange:.6g}", className="meta-item"),
            html.Span("Vista diezmada" if is_decimated else "Resolución completa",
                       className="meta-item meta-decimated" if is_decimated else "meta-item"),
        ],
    )
