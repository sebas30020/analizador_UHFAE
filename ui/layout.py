"""Módulo de layout global y montaje de componentes transversales.

Permite montar la barra de estado global de trabajos (`job_status_bar`) en
la raíz del layout de la aplicación Dash o en cualquier contenedor.
"""
from __future__ import annotations

from typing import Any
from dash import html

from ui.components.job_status_bar import build_job_status_bar


def mount_job_status_bar(layout: Any) -> Any:
    """Monta el componente `job_status_bar` en el layout raíz o contenedor principal.

    Si el layout ya contiene el componente `job-status-bar` o `job-status-interval`,
    no lo duplica.
    """
    status_bar = build_job_status_bar()
    if hasattr(layout, "children"):
        children = layout.children
        if isinstance(children, list):
            has_bar = any(
                getattr(child, "id", None) in ("job-status-bar", "job-status-interval")
                for child in children
                if child is not None
            )
            if not has_bar:
                children.insert(0, status_bar)
            return layout
        elif children is not None:
            layout.children = [status_bar, children]
            return layout
        else:
            layout.children = [status_bar]
            return layout
    return html.Div([status_bar, layout])

