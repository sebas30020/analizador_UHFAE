"""Callbacks de la ventana de sensor (Fase 4). Envoltorios delgados de Dash sobre la
lógica pura de ``ui/callbacks/helpers.py`` y las fachadas de ``cache/service.py``.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from dash import ALL, Dash, Input, Output, State, ctx, dcc, html
from dash.exceptions import PreventUpdate

from cache.service import get_or_compute_group_intrinsic, get_or_compute_group_reduction, get_or_compute_puntual
from core.grouping import resolve_groups
from core.normalization import normalize
from metrics.registry import get_metric
from ui.callbacks.filtering import (
    format_filter_status,
    indices_in_time_range,
    resolve_group_selection_indices,
    resolve_puntual_selection_indices,
    resolve_timeseries_selection_range,
)
from ui.callbacks.helpers import clamp_index, decode_metric_option, parse_compare_indices, resolve_nav_index
from ui.components.graph_metric import build_metric_figure
from ui.components.graph_signal import build_signal_figure
from ui.components.graph_timeseries import build_timeseries_figure
from ui.components.metadata_panel import build_metadata_panel
from ui.components.time_axis import elapsed_minutes_to_unix_seconds
from ui.state import get_state

_DEFAULT_DATA_DIR_CANDIDATES = [Path(r"D:\data\data\main"), Path.home()]


def _open_file_dialog() -> str | None:
    """Explorador nativo para elegir el archivo `.hdf5` de origen (PROMPT §6.2.1).

    Ejecuta ``tkinter.filedialog`` dentro del propio proceso Dash -- válido porque esta
    es una herramienta de un solo usuario local (servidor y navegador en la misma
    máquina, FASE0_DISENO...md §3.4), no un despliegue multiusuario remoto.
    """
    import tkinter as tk
    from tkinter import filedialog

    initial_dir = next((str(p) for p in _DEFAULT_DATA_DIR_CANDIDATES if p.exists()), str(Path.home()))

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    path = filedialog.askopenfilename(
        title="Seleccionar base de datos (HDF5)",
        initialdir=initial_dir,
        filetypes=[("HDF5", "*.hdf5 *.h5"), ("Todos los archivos", "*.*")],
    )
    root.destroy()
    return path or None


def register_callbacks(app: Dash) -> None:
    """Registra todos los callbacks de la ventana de sensor **una sola vez** para todo
    el proceso Dash (Fase 5): UHF y AE ya no son instancias separadas del layout con
    ids sensor-específicos, son la **misma** página servida en dos URLs distintas
    (``/sensor/UHF``, ``/sensor/AE``, ver ``ui/app.py``). Cada callback lee de qué
    sensor se trata desde el Store ``page-sensor`` sembrado por
    ``build_sensor_window_layout`` al construir esa página -- así una sola definición
    de callback sirve a ambas ventanas gemelas sin duplicar lógica (§6.1 del PROMPT).
    """

    @app.callback(
        Output("db-path-label", "children"),
        Output("dataset-version", "data"),
        Input("btn-select-db", "n_clicks"),
        State("dataset-version", "data"),
        prevent_initial_call=True,
    )
    def _on_select_db(n_clicks: int, current_version: int | None):
        path = _open_file_dialog()
        if not path:
            raise PreventUpdate
        state = get_state()
        dataset = state.load_dataset(path)
        return str(dataset.source_path), (current_version or 0) + 1

    @app.callback(
        Output("nav-index", "value"),
        Input("btn-prev", "n_clicks"),
        Input("btn-next", "n_clicks"),
        Input("nav-index", "value"),
        Input("graph-timeseries", "clickData"),
        Input({"type": "graph-metric", "index": ALL}, "clickData"),
        Input("dataset-version", "data"),
        State("page-sensor", "data"),
        prevent_initial_call=True,
    )
    def _on_navigate(n_prev, n_next, nav_value, click_timeseries, click_metrics, dataset_version, sensor):
        state = get_state()
        dataset = state.dataset
        if dataset is None or sensor not in dataset.blocks:
            raise PreventUpdate
        n_total = dataset.blocks[sensor].data.shape[0]
        if n_total == 0:
            raise PreventUpdate

        current = state.get_active_index(sensor)
        triggered = ctx.triggered_id
        # Cualquiera de las N gráficas apiladas tipo #3 puede disparar la navegación
        # (id de patrón {"type": "graph-metric", "index": <opción>}) -- se normaliza a
        # "graph-metric" para reusar la misma lógica pura que la gráfica #1.
        is_metric_click = isinstance(triggered, dict) and triggered.get("type") == "graph-metric"
        triggered_kind = "graph-metric" if is_metric_click else triggered

        click_target = None
        if triggered_kind in ("graph-timeseries", "graph-metric"):
            click_data = ctx.triggered[0]["value"] if is_metric_click else click_timeseries
            points = (click_data or {}).get("points") or []
            if not points:
                raise PreventUpdate
            # Las gráficas #1/#3 dibujan minutos transcurridos (ui/components/time_axis.py),
            # no timestamp UNIX -- hay que reconvertir el clic antes de buscar el índice.
            clicked_unix = elapsed_minutes_to_unix_seconds(float(points[0]["x"]), dataset.t0)
            click_target = state.nearest_index_for_timestamp(sensor, clicked_unix)

        new_index = resolve_nav_index(triggered_kind, nav_value, current, n_total, click_target)
        state.set_active_index(sensor, new_index)
        return new_index

    # --- Filtrado cruzado (Fase 6, PROMPT §7) -----------------------------------------

    @app.callback(
        Output("pending-exclusion-indices", "data"),
        Input("graph-timeseries", "selectedData"),
        Input({"type": "graph-metric", "index": ALL}, "selectedData"),
        State("page-sensor", "data"),
        State("grouping-mode", "value"),
        State("grouping-value", "value"),
        prevent_initial_call=True,
    )
    def _on_selection_changed(selected_timeseries, selected_metrics, sensor, grouping_mode, grouping_value):
        # Traduce la selección con lazo/rectángulo de Plotly (en CUALQUIERA de las
        # gráficas #1/#3) a índices crudos de señal a excluir -- todavía no aplica nada,
        # solo deja la selección "pendiente" hasta que el usuario confirme con el botón
        # "Filtrar selección" (PROMPT §7.1: seleccionar y filtrar son dos pasos).
        state = get_state()
        dataset = state.dataset
        if dataset is None or sensor not in dataset.blocks:
            raise PreventUpdate
        block = dataset.blocks[sensor]
        triggered = ctx.triggered_id

        if triggered == "graph-timeseries":
            x_range = resolve_timeseries_selection_range(selected_timeseries)
            if x_range is None:
                return []
            x0 = elapsed_minutes_to_unix_seconds(x_range[0], dataset.t0)
            x1 = elapsed_minutes_to_unix_seconds(x_range[1], dataset.t0)
            return indices_in_time_range(block.timestamps, x0, x1).tolist()

        if isinstance(triggered, dict) and triggered.get("type") == "graph-metric":
            regimen, _metric_id = decode_metric_option(triggered["index"])
            selected_data = ctx.triggered[0]["value"]
            points = (selected_data or {}).get("points") or []
            if not points:
                return []

            if regimen == "puntual":
                nearest_fn = lambda seconds: state.nearest_index_for_timestamp(sensor, seconds)  # noqa: E731
                return resolve_puntual_selection_indices(points, dataset.t0, nearest_fn)

            if not grouping_value or grouping_value <= 0:
                raise PreventUpdate
            groups = resolve_groups(block.timestamps, mode=grouping_mode, value=float(grouping_value))
            return resolve_group_selection_indices(points, groups, dataset.t0).tolist()

        raise PreventUpdate

    @app.callback(
        Output("filter-version", "data"),
        Input("btn-apply-filter", "n_clicks"),
        Input("btn-undo-filter", "n_clicks"),
        Input("btn-redo-filter", "n_clicks"),
        Input("btn-reset-filters", "n_clicks"),
        State("pending-exclusion-indices", "data"),
        State("page-sensor", "data"),
        prevent_initial_call=True,
    )
    def _on_filter_action(n_apply, n_undo, n_redo, n_reset, pending_indices, sensor):
        state = get_state()
        triggered = ctx.triggered_id
        if triggered == "btn-apply-filter":
            if not pending_indices:
                raise PreventUpdate
            return state.apply_filter(sensor, np.array(pending_indices, dtype=np.int64))
        if triggered == "btn-undo-filter":
            return state.undo_filter(sensor)
        if triggered == "btn-redo-filter":
            return state.redo_filter(sensor)
        if triggered == "btn-reset-filters":
            return state.reset_filters(sensor)
        raise PreventUpdate

    @app.callback(
        Output("filter-status", "children"),
        Input("filter-version", "data"),
        Input("dataset-version", "data"),
        State("page-sensor", "data"),
    )
    def _on_refresh_filter_status(filter_version, dataset_version, sensor):
        state = get_state()
        return format_filter_status(*state.get_filter_counts(sensor))

    @app.callback(
        Output("graph-timeseries", "figure"),
        Input("dataset-version", "data"),
        Input("filter-version", "data"),
        State("page-sensor", "data"),
    )
    def _on_refresh_timeseries(dataset_version, filter_version, sensor):
        state = get_state()
        dataset = state.dataset
        if dataset is None or sensor not in dataset.blocks:
            return go.Figure()
        block = dataset.blocks[sensor]
        cfg = dataset.sensor_configs[sensor]
        active_mask = state.get_active_mask(sensor)
        return build_timeseries_figure(cfg, block, dataset.environmental, dataset.events, dataset.t0, active_mask)

    @app.callback(
        Output("graph-signal", "figure"),
        Output("metadata-panel-container", "children"),
        Input("nav-index", "value"),
        Input("dataset-version", "data"),
        Input("compare-indices", "value"),
        Input("graph-signal", "relayoutData"),
        Input("show-raw-signal", "value"),
        State("page-sensor", "data"),
    )
    def _on_refresh_signal(nav_value, dataset_version, compare_text, relayout, show_raw, sensor):
        state = get_state()
        dataset = state.dataset
        empty_panel = [build_metadata_panel(0, 0, 0.0, 0.0, 0.0, is_decimated=False)]
        if dataset is None or sensor not in dataset.blocks or dataset.blocks[sensor].data.shape[0] == 0:
            return go.Figure(), empty_panel

        block = dataset.blocks[sensor]
        cfg = dataset.sensor_configs[sensor]
        n_total = block.data.shape[0]
        index = clamp_index(int(nav_value) if nav_value is not None else state.get_active_index(sensor), n_total)

        x_range = None
        if relayout and "xaxis.range[0]" in relayout and "xaxis.range[1]" in relayout:
            x_range = (float(relayout["xaxis.range[0]"]), float(relayout["xaxis.range[1]"]))

        overlay_indices = parse_compare_indices(compare_text, n_total, exclude=index)
        is_raw = bool(show_raw and "raw" in show_raw)

        # Por defecto se normaliza (x_norm = x_raw / vrange, PROMPT §2.4) -- la misma
        # regla que ya aplican todas las métricas en metrics/engine.py -- para que el
        # máximo visible en la gráfica coincida con Vmax/RMS/etc. El checkbox "ver señal
        # cruda" es el flag explícito de depuración que exige el PROMPT para comparar.
        all_indices = [index] + overlay_indices
        if is_raw:
            batch = block.data[all_indices]
        else:
            batch = normalize(block.data[all_indices], block.vrange[all_indices])
        signal_row, overlay_rows = batch[0], list(batch[1:])

        fig, is_decimated = build_signal_figure(
            cfg, signal_row, overlay_rows=overlay_rows, x_range_natural_units=x_range, is_raw=is_raw
        )
        metadata = build_metadata_panel(
            index, n_total, float(block.timestamps[index]), float(block.trigger[index]), float(block.vrange[index]), is_decimated
        )
        return fig, [metadata]

    @app.callback(
        Output("metrics-graphs-container", "children"),
        Input("metrics-picker", "value"),
        Input("dataset-version", "data"),
        Input("filter-version", "data"),
        Input("grouping-mode", "value"),
        Input("grouping-value", "value"),
        Input("grouping-reducer", "value"),
        Input("grouping-percentile-q", "value"),
        State("page-sensor", "data"),
    )
    def _on_refresh_metrics(selected_options, dataset_version, filter_version, grouping_mode, grouping_value, reducer, percentile_q, sensor):
        # Cada métrica agregada apila una gráfica más -- sin tope, la propia página hace
        # scroll (reemplaza a la "ventana adicional" de la Fase 5).
        state = get_state()
        dataset = state.dataset
        if dataset is None or sensor not in dataset.blocks or not selected_options:
            return []

        block = dataset.blocks[sensor]
        cfg = dataset.sensor_configs[sensor]
        active_mask = state.get_active_mask(sensor)
        graphs = []

        for option_value in selected_options:
            regimen, metric_id = decode_metric_option(option_value)
            definition = get_metric(metric_id)
            is_partial = None

            if regimen == "puntual":
                timestamps, values = get_or_compute_puntual(
                    state.cache, block, cfg, dataset.dataset_id, metric_id, active_mask=active_mask
                )
            elif not grouping_value or grouping_value <= 0:
                continue  # ventana de agrupamiento inválida -- se omite esta gráfica, no rompe las demás
            elif regimen == "grupo_intrinseca":
                timestamps, values, is_partial = get_or_compute_group_intrinsic(
                    state.cache, block, cfg, dataset.dataset_id, metric_id, grouping_mode, float(grouping_value),
                    active_mask=active_mask,
                )
            else:  # grupo_reduccion
                timestamps, values, is_partial = get_or_compute_group_reduction(
                    state.cache, block, cfg, dataset.dataset_id, metric_id, grouping_mode, float(grouping_value),
                    reducer=reducer or "median", percentile_q=float(percentile_q or 75.0),
                    active_mask=active_mask,
                )

            fig = build_metric_figure(
                timestamps, values, dataset.events, dataset.t0,
                label=definition.label, unit=definition.unit, is_partial=is_partial,
            )
            graph_id = {"type": "graph-metric", "index": option_value}
            graphs.append(html.Div(dcc.Graph(id=graph_id, figure=fig), className="metrics-graph-slot"))

        return graphs
