"""Callbacks de la ventana de sensor (Fase 4). Envoltorios delgados de Dash sobre la
lógica pura de ``ui/callbacks/helpers.py`` y las fachadas de ``cache/service.py``.
"""
from __future__ import annotations

import logging
from functools import partial
from pathlib import Path

import numpy as np
import plotly.graph_objects as go
from dash import ALL, Dash, Input, Output, Patch, State, ctx, dcc, html
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
from ui.callbacks.helpers import (
    clamp_index,
    decode_metric_option,
    parse_compare_indices,
    resolve_nav_index,
    resolve_reference_line_display,
    resolve_sensor_availability_notice,
)
from ui.components.event_lines import build_event_line_shapes
from ui.components.graph_metric import build_metric_figure
from ui.components.graph_signal import build_signal_figure
from ui.components.graph_timeseries import build_timeseries_figure
from ui.components.metadata_panel import build_metadata_panel
from ui.components.reference_line import build_reference_annotation, build_reference_message_annotation, build_reference_shape
from ui.components.time_axis import elapsed_minutes_to_unix_seconds, to_elapsed_minutes
from ui.reference_registry import get_reference_registry
from ui.state import get_state
from viz.reference_line import build_prefix_sums
from viz.reference_line import mean_until as compute_reference_mean
from viz.smoothing import SmoothingSpec

_logger = logging.getLogger("analizador.ui.metrics_graph")

_DEFAULT_SMOOTHING_WINDOW = 5.0


def _is_checked(value: list[str] | None, option: str = "show") -> bool:
    """Traduce el valor de un ``dcc.Checklist`` de una sola opción (patrón ya usado por
    ``show-raw-signal``) a booleano."""
    return bool(value and option in value)

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
        Output("sensor-availability-notice", "children"),
        Output("sensor-availability-notice", "className"),
        Input("dataset-version", "data"),
        State("page-sensor", "data"),
    )
    def _on_refresh_sensor_availability(dataset_version, sensor):
        # Rama lectura_keysight (§4.5 del plan): con tres sensores posibles y cada
        # origen entregando solo un subconjunto, una ventana sin señales de su sensor
        # debe decirlo en vez de quedar con tres gráficas vacías sin explicación.
        state = get_state()
        dataset = state.dataset
        sensor_has_signals = dataset is not None and sensor in dataset.blocks and dataset.blocks[sensor].data.shape[0] > 0
        notice = resolve_sensor_availability_notice(sensor, dataset is not None, sensor_has_signals)
        class_name = "sensor-empty-notice" if notice else "sensor-empty-notice hidden"
        return notice or "", class_name

    # --- Visibilidad de eventos (archivos_md/prompt-mejora-graficas.md §2) -----------

    @app.callback(
        Output("event-shapes", "data"),
        Input("dataset-version", "data"),
        State("page-sensor", "data"),
    )
    def _on_refresh_event_shapes(dataset_version, sensor):
        # Construye las shapes una sola vez por dataset -- el toggle de visibilidad
        # (más abajo) las reutiliza vía dash.Patch sin volver a llamar a esta función
        # ni a ninguna fachada de caché/métricas.
        state = get_state()
        dataset = state.dataset
        if dataset is None:
            return []
        return build_event_line_shapes(dataset.events, dataset.t0)

    @app.callback(
        Output("show-events", "options"),
        Input("dataset-version", "data"),
        State("page-sensor", "data"),
    )
    def _on_refresh_show_events_options(dataset_version, sensor):
        state = get_state()
        dataset = state.dataset
        has_events = dataset is not None and dataset.events.timestamps.shape[0] > 0
        label = " Mostrar eventos" if has_events else " Mostrar eventos (sin eventos registrados)"
        return [{"label": label, "value": "show", "disabled": not has_events}]

    @app.callback(
        Output("graph-timeseries", "figure", allow_duplicate=True),
        Output({"type": "graph-metric", "index": ALL}, "figure", allow_duplicate=True),
        Input("show-events", "value"),
        Input("show-reference-line", "value"),
        Input("reference-line-t", "value"),
        State("event-shapes", "data"),
        State({"type": "graph-metric", "index": ALL}, "id"),
        prevent_initial_call=True,
    )
    def _on_refresh_metric_decorations(show_events_value, show_reference_value, reference_t, event_shapes, metric_ids):
        # Dueño único de layout.shapes / layout.annotations en las gráficas #3: eventos
        # (verticales) y línea de referencia (horizontal, archivos_md/prompt-linea-
        # referencia.md) comparten esas propiedades de layout, así que tienen que
        # componerse en un solo callback -- dos callbacks parcheando la misma
        # propiedad de forma independiente se pisarían entre sí.
        #
        # Único efecto: parchear las figuras ya construidas. No lee AppState, no
        # consulta el caché ni el motor de métricas, no reconstruye ninguna traza --
        # así conmutar cualquiera de los tres controles nunca dispara un recálculo de
        # métricas (criterio de aceptación 2 y 7). La línea de referencia lee del
        # registro de proceso (``ui/reference_registry.py``), que ya tiene las series
        # listas desde el último ``_on_refresh_metrics``.
        visible_event_shapes = event_shapes if _is_checked(show_events_value, "show") else []
        reference_enabled = _is_checked(show_reference_value, "show")
        t_value = float(reference_t) if reference_t is not None else None

        ts_patch = Patch()
        ts_patch["layout"]["shapes"] = visible_event_shapes

        registry = get_reference_registry()
        metric_patches = []
        for metric_id in metric_ids:
            option_value = metric_id["index"]
            mean_lookup = partial(registry.mean_until, option_value)
            reference_value, reference_message = resolve_reference_line_display(reference_enabled, t_value, mean_lookup)

            shapes = list(visible_event_shapes)
            annotations: list[dict] = []
            if reference_value is not None:
                shapes = [*shapes, build_reference_shape(reference_value)]
                annotations = [build_reference_annotation(reference_value)]
            elif reference_message is not None:
                annotations = [build_reference_message_annotation(reference_message)]

            p = Patch()
            p["layout"]["shapes"] = shapes
            p["layout"]["annotations"] = annotations
            metric_patches.append(p)

        return ts_patch, metric_patches

    @app.callback(
        Output("graph-timeseries", "figure"),
        Input("dataset-version", "data"),
        Input("filter-version", "data"),
        State("page-sensor", "data"),
        State("show-events", "value"),
    )
    def _on_refresh_timeseries(dataset_version, filter_version, sensor, show_events_value):
        state = get_state()
        dataset = state.dataset
        if dataset is None or sensor not in dataset.blocks:
            return go.Figure()
        block = dataset.blocks[sensor]
        cfg = dataset.sensor_configs[sensor]
        active_mask = state.get_active_mask(sensor)
        return build_timeseries_figure(
            cfg, block, dataset.environmental, dataset.events, dataset.t0, active_mask,
            show_events=_is_checked(show_events_value, "show"),
            uirevision=f"{sensor}|{dataset.dataset_id}",
        )

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
            index, n_total, float(block.timestamps[index]), float(block.trigger[index]), float(block.vrange[index]),
            is_decimated, has_trigger_metadata=cfg.has_trigger_metadata,
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
        Input("smooth-puntual", "value"),
        Input("smooth-grupo", "value"),
        Input("smoothing-method", "value"),
        Input("smoothing-window", "value"),
        Input("gap-threshold", "value"),
        State("page-sensor", "data"),
        State("show-events", "value"),
        State("show-reference-line", "value"),
        State("reference-line-t", "value"),
    )
    def _on_refresh_metrics(
        selected_options, dataset_version, filter_version, grouping_mode, grouping_value, reducer, percentile_q,
        smooth_puntual_value, smooth_grupo_value, smoothing_method, smoothing_window_value, gap_threshold_value,
        sensor, show_events_value, show_reference_value, reference_t,
    ):
        # Cada métrica agregada apila una gráfica más -- sin tope, la propia página hace
        # scroll (reemplaza a la "ventana adicional" de la Fase 5).
        #
        # Los controles de suavizado y de corte por huecos son Input (no State): el
        # requisito de la GUI es que se reflejen de inmediato, sin botón "Aplicar"
        # (archivos_md/prompt-mejora-graficas.md §4). "Mostrar eventos" y la línea de
        # referencia son la excepción -- ver ``_on_refresh_metric_decorations`` más
        # arriba, que los resuelve con un parche sin pasar por aquí. Aquí solo se leen
        # como ``State`` para que la primera figura de cada gráfica ya nazca coherente
        # con lo que el usuario tenía configurado (criterio "persiste durante la
        # sesión", §2.4).
        state = get_state()
        dataset = state.dataset
        registry = get_reference_registry()
        if dataset is None or sensor not in dataset.blocks or not selected_options:
            # Dataset descargado o selección vaciada: las series que el registro
            # retenía ya no corresponden a nada visible -- se purgan explícitamente en
            # vez de dejarlas colgadas hasta el próximo refresco (documento de diseño
            # §3.2, mismo cuidado que motivó el commit bd5814d).
            registry.clear()
            return []

        block = dataset.blocks[sensor]
        cfg = dataset.sensor_configs[sensor]
        active_mask = state.get_active_mask(sensor)
        graphs = []
        registry_entries: dict[str, tuple[np.ndarray, np.ndarray]] = {}

        show_events = _is_checked(show_events_value, "show")
        smooth_puntual = _is_checked(smooth_puntual_value, "smooth")
        smooth_grupo = _is_checked(smooth_grupo_value, "smooth")
        smoothing_method = smoothing_method or "media_movil_temporal"
        smoothing_window = float(smoothing_window_value) if smoothing_window_value else _DEFAULT_SMOOTHING_WINDOW
        gap_threshold = float(gap_threshold_value) if gap_threshold_value else None
        reference_enabled = _is_checked(show_reference_value, "show")
        reference_t_value = float(reference_t) if reference_t is not None else None

        for option_value in selected_options:
            regimen, metric_id = decode_metric_option(option_value)
            definition = get_metric(metric_id)
            is_partial = None

            if regimen != "puntual" and (not grouping_value or grouping_value <= 0):
                continue  # ventana de agrupamiento inválida -- se omite esta gráfica, no rompe las demás

            # Cada métrica se calcula y grafica de forma aislada: como el Output de este
            # callback es único (todo el contenedor), un fallo sin capturar (p. ej.
            # MemoryError en un dataset grande, ver plan de memoria) subiría a Dash como
            # HTTP 500 y se perderían TAMBIÉN las gráficas que sí se pudieron calcular.
            try:
                if regimen == "puntual":
                    timestamps, values = get_or_compute_puntual(
                        state.cache, block, cfg, dataset.dataset_id, metric_id, active_mask=active_mask
                    )
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

                # Régimen de grupo (intrínseca o reducción): unión directa siempre, más
                # suavizado opcional (§3.3). Régimen puntual: nunca unión directa (sierra
                # ilegible con miles de puntos, §3.1) -- solo la línea suavizada opcional
                # sobre marcadores atenuados (§3.2).
                is_group_regimen = regimen != "puntual"
                smooth_enabled = smooth_grupo if is_group_regimen else smooth_puntual
                smoothing_spec = SmoothingSpec(method=smoothing_method, window=smoothing_window) if smooth_enabled else None

                # Registro de la serie cruda (misma referencia de array, sin copia)
                # para que la línea de referencia pueda recalcular su promedio al
                # cambiar ``t`` sin volver a pasar por aquí (``ui/reference_registry.py``).
                # En minutos transcurridos -- mismas unidades que el control de la GUI
                # y que el eje X que dibuja ``build_metric_figure``.
                x_minutes = to_elapsed_minutes(timestamps, dataset.t0)
                registry_entries[option_value] = (x_minutes, values)

                reference_value: float | None = None
                reference_message: str | None = None
                if reference_enabled:
                    # Se calcula una vez aquí (no en cada cambio de t: eso lo resuelve
                    # ``_on_refresh_metric_decorations`` leyendo del registro) para que
                    # la primera figura ya nazca coherente con la configuración vigente.
                    prefix = build_prefix_sums(x_minutes, values)
                    reference_value, reference_message = resolve_reference_line_display(
                        True, reference_t_value, partial(compute_reference_mean, prefix)
                    )

                fig = build_metric_figure(
                    timestamps, values, dataset.events, dataset.t0,
                    label=definition.label, unit=definition.unit, is_partial=is_partial,
                    show_events=show_events, connect_points=is_group_regimen,
                    smoothing=smoothing_spec, gap_threshold=gap_threshold,
                    reference_value=reference_value, reference_message=reference_message,
                    uirevision=f"{option_value}|{dataset.dataset_id}",
                )
                graph_id = {"type": "graph-metric", "index": option_value}
                graphs.append(html.Div(dcc.Graph(id=graph_id, figure=fig), className="metrics-graph-slot"))
            except Exception as exc:
                _logger.exception(
                    "etapa=ui.metrics_graph error=fallo_calculo sensor=%s metric_id=%s regimen=%s",
                    sensor, metric_id, regimen,
                )
                graphs.append(
                    html.Div(
                        f"No se pudo calcular '{definition.label}': {exc}",
                        className="metrics-graph-slot metrics-graph-error",
                    )
                )

        registry.replace_all(registry_entries)
        return graphs

    # --- Controles dependientes del bloque "Opciones de visualización" ---------------

    @app.callback(
        Output("smoothing-window-label", "children"),
        Input("smoothing-method", "value"),
    )
    def _on_refresh_smoothing_window_label(method):
        # La unidad de la ventana depende del método: tiempo para la media móvil
        # temporal, número de puntos para la mediana móvil (viz/smoothing.py).
        if method == "mediana_movil_puntos":
            return "Ventana de suavizado (cantidad de puntos)"
        return "Ventana de suavizado (min)"

    @app.callback(
        Output("smoothing-method", "disabled"),
        Output("smoothing-window", "disabled"),
        Output("gap-threshold", "disabled"),
        Input("smooth-puntual", "value"),
        Input("smooth-grupo", "value"),
    )
    def _on_refresh_smoothing_controls_disabled(smooth_puntual_value, smooth_grupo_value):
        # Los controles de método/ventana/umbral solo aplican si algún suavizado está
        # activo -- se atenúan cuando ninguno lo está (§4: "deshabilitar o atenuar los
        # controles dependientes cuando no apliquen").
        any_smoothing = _is_checked(smooth_puntual_value, "smooth") or _is_checked(smooth_grupo_value, "smooth")
        disabled = not any_smoothing
        return disabled, disabled, disabled

    @app.callback(
        Output("reference-line-t", "disabled"),
        Input("show-reference-line", "value"),
    )
    def _on_refresh_reference_line_control_disabled(show_reference_value):
        # Mismo patrón que ``_on_refresh_smoothing_controls_disabled``: el campo de
        # intervalo solo aplica si la línea de referencia está activa
        # (archivos_md/prompt-linea-referencia.md §2.4).
        return not _is_checked(show_reference_value, "show")
