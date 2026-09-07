"""Componente de barra de estado para trabajos en segundo plano (PROMPT maestro §8, R1 / Etapa 1).

Proporciona un componente reactivo con un único ``dcc.Interval(id="job-status-interval", interval=700)``
global para sondeo, unificando la visualización del estado de tareas pesadas (carga de dataset,
precalentamiento de caché, cálculo frío de métricas, exportación y fusión) y sus botones de
cancelación cooperativa.
"""
from __future__ import annotations

from typing import Any
from dash import dcc, html

from ui.jobs import JobInfo


def build_job_status_bar(
    bar_id: str = "job-status-bar",
    interval_id: str = "job-status-interval",
    poll_interval_ms: int = 700,
) -> html.Div:
    """Construye el contenedor de la barra de estado y el Interval global de sondeo."""
    return html.Div(
        id=bar_id,
        className="job-status-bar-wrapper",
        children=[
            dcc.Interval(id=interval_id, interval=poll_interval_ms, disabled=False),
            html.Div(id="job-status-container", className="job-status-container"),
        ],
    )


def render_job_item(job: JobInfo) -> html.Div:
    """Renderiza un elemento individual de trabajo activo o completado."""
    is_active = job.is_active
    status_icon = "⏳" if is_active else ("✓" if job.status == "completed" else "✕")
    status_class = f"job-item job-{job.status}"

    progress_element: html.Div | None = None
    if is_active:
        if job.progress is not None:
            pct = int(job.progress * 100)
            progress_element = html.Div(
                className="job-progress-wrapper",
                children=[
                    html.Progress(value=str(job.progress), max="1.0", className="job-progress"),
                    html.Span(f"{pct}%", className="job-progress-pct"),
                ],
            )
        else:
            progress_element = html.Div(className="job-spinner-indeterminate")

    cancel_btn: html.Button | None = None
    if is_active:
        cancel_btn = html.Button(
            "✕ Cancelar",
            id={"type": "btn-cancel-job", "index": job.id},
            className="btn-cancel-job",
            title="Cancelar este trabajo en segundo plano",
        )

    text_desc = f"{job.label or job.type}"
    if job.message:
        text_desc += f": {job.message}"
    elif job.status == "failed" and job.error:
        text_desc += f": {job.error}"

    children: list[Any] = [
        html.Span(status_icon, className="job-icon"),
        html.Span(text_desc, className="job-text"),
    ]
    if progress_element is not None:
        children.append(progress_element)
    if cancel_btn is not None:
        children.append(cancel_btn)

    return html.Div(
        id={"type": "job-item-card", "index": job.id},
        className=status_class,
        children=children,
    )


def render_job_status_display(
    active_jobs: list[JobInfo],
    recent_jobs: list[JobInfo] | None = None,
) -> list[Any]:
    """Genera la lista de componentes Dash a insertar dentro de `job-status-container`."""
    if not active_jobs and not recent_jobs:
        return []

    elements: list[Any] = []
    for job in active_jobs:
        elements.append(render_job_item(job))

    if recent_jobs:
        for job in recent_jobs:
            if not job.is_active:
                elements.append(render_job_item(job))

    return elements

