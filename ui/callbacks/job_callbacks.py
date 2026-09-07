"""Callbacks reactivos para el servicio de trabajos en segundo plano (R1 / Etapa 1).

Conecta el sondeo periódico de ``job-status-interval`` con la representación visual
en ``job-status-container``, atiende cancelaciones cooperativas de trabajos, y
propaga la publicación asíncrona de datasets a ``dataset-version`` en Dash.
"""
from __future__ import annotations

import logging
from typing import Any
from dash import ALL, Dash, Input, Output, State, ctx
from dash.exceptions import PreventUpdate

from ui.callbacks.helpers import format_db_path_label
from ui.components.job_status_bar import render_job_status_display
from ui.jobs import get_job_service
from ui.state import get_state

_logger = logging.getLogger("analizador.ui.callbacks.jobs")


def register_job_callbacks(app: Dash) -> None:
    """Registra los callbacks reactivos del servicio de trabajos en la aplicación Dash."""

    @app.callback(
        Output("job-status-container", "children"),
        Input("job-status-interval", "n_intervals"),
        prevent_initial_call=False,
    )
    def _on_poll_jobs(n_intervals: int | None) -> list[Any]:
        job_service = get_job_service()
        active_jobs = job_service.active()
        all_jobs = job_service.all_jobs()
        finished_recent = [j for j in all_jobs if j.is_finished]
        finished_recent.sort(key=lambda j: j.completed_at or 0.0, reverse=True)
        recent = finished_recent[:3] if not active_jobs else []
        return render_job_status_display(active_jobs, recent)

    @app.callback(
        Output({"type": "job-item-card", "index": ALL}, "style"),
        Input({"type": "btn-cancel-job", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def _on_cancel_job(n_clicks: list[int | None]) -> list[dict[str, Any]]:
        if not ctx.triggered_id or not isinstance(ctx.triggered_id, dict):
            raise PreventUpdate
        job_id = ctx.triggered_id.get("index")
        if job_id:
            job_service = get_job_service()
            job_service.cancel(str(job_id))
            _logger.info("etapa=job.ui_cancel_clicked job_id=%s", job_id)
        return [{} for _ in n_clicks]

    @app.callback(
        Output("dataset-version", "data", allow_duplicate=True),
        Output("db-path-label", "children", allow_duplicate=True),
        Input("job-status-interval", "n_intervals"),
        State("dataset-version", "data"),
        prevent_initial_call=True,
    )
    def _on_poll_background_dataset(
        n_intervals: int | None,
        current_version: int | None,
    ) -> tuple[int, str]:
        state = get_state()
        dataset = state.dataset
        if dataset is None:
            raise PreventUpdate
        if state.dataset_version != (current_version or 0):
            _logger.info(
                "etapa=job.dataset_version_updated old=%s new=%s",
                current_version,
                state.dataset_version,
            )
            return state.dataset_version, format_db_path_label(dataset.source_path, dataset.partition)
        raise PreventUpdate

