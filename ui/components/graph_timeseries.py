"""Gráfica tipo #1 — Serie temporal global + variables ambientales (PROMPT §5.1)."""
from __future__ import annotations

import numpy as np
import plotly.graph_objects as go

from core.models import EnvironmentalSeries, EventSeries, SensorConfig, SignalBlock
from ui.components.event_lines import build_event_lines_trace
from ui.components.time_axis import TIME_AXIS_TITLE, to_elapsed_minutes
from utils.profiling import stage
from viz.decimation import (
    ENVELOPE_DECIMATION_BINS,
    ENVELOPE_EXACT_LIMIT,
    bin_reduce_minmax,
    build_vertical_segments,
)

SIGNAL_COLOR = "#4A7BB0"
TEMPERATURE_COLOR = "#E07B39"
HUMIDITY_COLOR = "#39A0A0"
EVENT_COLOR = "#D14343"

# Nombre de la traza ancla del lazo (ver :func:`_build_selection_anchor_trace`). No se
# muestra en la leyenda ni en ningún tooltip; existe solo para que Plotly no retire los
# botones de selección de la barra de herramientas.
EMPTY_ACTIVE_SET_MESSAGE = "Sin señales activas para mostrar con el filtro actual."
SELECTION_ANCHOR_NAME = "_ancla_seleccion"


def _build_selection_anchor_trace(t: np.ndarray, y: np.ndarray) -> go.Scatter:
    """Traza de un solo punto invisible **con marcador**, sin la cual la gráfica #1 se
    queda sin lazo ni caja de selección.

    Plotly decide qué botones pone en la barra de herramientas con ``isSelectable`` (en
    ``components/modebar/manage.js``): una traza ``scatter``/``scattergl`` solo cuenta
    como seleccionable si tiene marcadores o texto, y solo si **alguna** traza lo es
    añade ``select2d`` y ``lasso2d`` al grupo de modos de arrastre. Desde que la
    envolvente pasó a ``mode="lines"``, las cuatro trazas de esta gráfica son de líneas
    puras: cero seleccionables, cero botones, lazo inalcanzable desde la interfaz.

    Forzarlos por configuración **no funciona** -- se comprobó que
    ``modeBarButtonsToAdd=["select2d", "lasso2d"]`` en el ``dcc.Graph`` los sigue
    filtrando. La única palanca es que exista una traza con marcadores, y basta con
    esta: un punto con ``opacity=0``, ``hoverinfo="skip"`` y sin entrada de leyenda.

    Se coloca sobre la primera señal activa a propósito, no en el origen: así queda
    dentro del rango de los datos y no arrastra el autorango de ningún eje.

    Va **última** en la figura para no desplazar los ``curveNumber`` de las trazas
    existentes, de los que depende ``ui/callbacks/filtering.py``.
    """
    return go.Scatter(
        x=t[:1],
        y=y[:1],
        mode="markers",
        marker=dict(size=1, opacity=0, color=SIGNAL_COLOR),
        name=SELECTION_ANCHOR_NAME,
        hoverinfo="skip",
        showlegend=False,
        yaxis="y1",
    )


def build_timeseries_figure(
    sensor_config: SensorConfig,
    block: SignalBlock,
    environmental: EnvironmentalSeries,
    events: EventSeries,
    t0: float,
    active_mask: np.ndarray,
    show_events: bool = True,
    uirevision: str | None = None,
) -> go.Figure:
    """Envolvente min/max de **todas** las señales + ambientales en eje secundario + eventos.

    El par (min, max) por señal ya viene precalculado desde la ingesta (Fase 1,
    ``SignalBlock.minmax``) -- nunca se recalcula aquí (PROMPT §5.1: "nunca se recalcula
    en tiempo de render").

    **Sin diezmado**: se dibuja un segmento vertical por cada señal activa, aunque sean
    decenas de miles -- ningún punto se agrega ni se descarta, así que lo que se ve en
    pantalla es el conjunto completo y cada segmento corresponde a una señal real. Por
    eso la traza es ``Scattergl`` (WebGL) y no ``Scatter`` (SVG): con ~2×10⁴ señales el
    renderizador SVG tendría que crear ~6×10⁴ elementos DOM.

    El eje horizontal se muestra en minutos transcurridos desde ``t0`` (referencia común
    del experimento, ``ui/state.py::compute_t0``), no en timestamp UNIX crudo.

    ``active_mask`` (Fase 6, PROMPT §7): máscara de filtrado interactivo del usuario,
    combinada con ``valid_mask`` -- las señales excluidas desaparecen de la envolvente,
    igual que las de metadato inválido. Ambientales/eventos no son "señales" y no se ven
    afectados.

    ``show_events``: visibilidad de las líneas de evento (control "Mostrar eventos",
    ``archivos_md/prompt-mejora-graficas.md`` §2). ``uirevision``: estable frente a
    redibujados que no deben perder el zoom del usuario.

    **Navegación por clic**: ``clickanywhere=True`` hace que ``Fx.click`` emita
    ``plotly_click`` aunque el cursor no esté sobre ningún punto, con ``xvals`` en el
    payload (``dcc.Graph::filterEventData`` solo lo propaga si ese flag está activo). Así
    se navega clicando en cualquier parte del área de dibujo en vez de tener que acertarle
    a un segmento de 1 px, y ``_on_navigate`` prioriza ``xvals`` sobre ``points`` porque un
    clic encima de las curvas ambientales manda las dos cosas y la ``x`` del punto ambiental
    está discretizada a su propia cadencia de muestreo, mucho más gruesa.

    **La envolvente no participa del hover** (``hoverinfo="skip"``), y de ahí depende que
    la página no se congele. ``scattergl/calc`` solo construye su índice espacial kd-tree
    si la traza tiene ``_length >= TOO_MANY_POINTS`` (``= 1e5``); con 61 722 puntos en AE
    y 37 452 en UHF queda por debajo, así que ``scattergl/hover.js::hoverPoints`` cae a
    ``stash.ids`` y recorre el array entero -- llamando ``c2p()`` y ``sqrt`` punto por
    punto -- cada vez que ``Fx.hover`` se dispara, es decir cada ``HOVERMINTIME = 50`` ms
    mientras el cursor esté encima. El barrido cuesta más que ese presupuesto: el hilo
    principal se satura y la pestaña deja de responder.

    Medido sobre la figura real a tamaño AE, mediana de 12 ``mousemove`` nativos:
    **22,5 ms sin ``skip`` contra 3,3 ms con ``skip``** (6,8x). Sacar la envolvente del
    hover cuesta lo mismo que no dibujarla (3,8 ms), o sea que el lienzo WebGL no es el
    problema; lo es el barrido. El experimento que lo cierra: rellenar la envolvente
    hasta 120 000 puntos, cruzando ``TOO_MANY_POINTS``, la deja en 4,0 ms -- el doble de
    datos, tres veces más rápida.

    Este ``skip`` ya había estado aquí y se revirtió porque la medición de entonces no lo
    respaldaba (46,8 ms contra 42,9 ms). Aquella medición era correcta **para el código de
    entonces**: las series ambientales todavía se dibujaban sin diezmar, 20 808 puntos SVG
    cada una, y ponían un suelo que tapaba el efecto de la envolvente. Con el diezmado
    ambiental a 2000 bins que ya hay más abajo, la ganancia pasa de 1,3x (ruido) a 6,8x.
    Las dos mejoras estaban acopladas. Detalle en ``docs/RENDIMIENTO.md`` §1.2.

    El precio es el tooltip de la envolvente, que era de utilidad marginal (un segmento
    de 1 px por señal) y cuya función real -- saber a qué señal corresponde un punto -- la
    cubre ``clickanywhere``. Las ambientales conservan el suyo, que sí informa.
    """
    with stage("render.grafica1", sensor=sensor_config.name) as ctx:
        fig = go.Figure()

        valid = block.valid_mask & active_mask
        n_active = int(valid.sum())
        ctx["n_senales"] = n_active
        anchor: go.Scatter
        if valid.any():
            t = to_elapsed_minutes(block.timestamps[valid], t0)
            mm = block.minmax[valid]
            anchor = _build_selection_anchor_trace(t, mm[:, 0])

            if n_active > ENVELOPE_EXACT_LIMIT:
                t_env, mm_min_env, mm_max_env = bin_reduce_minmax(
                    t, mm[:, 0], mm[:, 1], n_bins=ENVELOPE_DECIMATION_BINS
                )
                xs, ys = build_vertical_segments(t_env, mm_min_env, mm_max_env)
                y_min = float(mm_min_env.min())
                y_max = float(mm_max_env.max())
                ctx["envolvente_diezmada"] = True
            else:
                xs, ys = build_vertical_segments(t, mm[:, 0], mm[:, 1])
                y_min = float(mm[:, 0].min())
                y_max = float(mm[:, 1].max())
                ctx["envolvente_diezmada"] = False

            if y_min == y_max:
                y_min -= 1.0
                y_max += 1.0

            fig.add_trace(
                go.Scattergl(
                    x=xs,
                    y=ys,
                    mode="lines",
                    line=dict(color=SIGNAL_COLOR, width=1),
                    name=f"Señal {sensor_config.name} (envolvente)",
                    yaxis="y1",
                    hoverinfo="skip",
                )
            )
        else:
            y_min, y_max = 0.0, 1.0
            # Anclar a la primera señal válida del dataset para que Plotly
            # mantenga las herramientas select2d/lasso2d en el modebar
            valid_all = np.where(block.valid_mask)[0]
            if valid_all.size > 0:
                t_anchor = to_elapsed_minutes(block.timestamps[valid_all[:1]], t0)
                y_anchor = block.minmax[valid_all[:1], 0]
            elif block.timestamps.shape[0] > 0:
                t_anchor = to_elapsed_minutes(block.timestamps[:1], t0)
                y_anchor = np.array([0.0])
            else:
                t_anchor = np.array([0.0])
                y_anchor = np.array([0.0])
            anchor = _build_selection_anchor_trace(t_anchor, y_anchor)
            fig.add_annotation(
                text=EMPTY_ACTIVE_SET_MESSAGE,
                xref="paper",
                yref="paper",
                x=0.5,
                y=0.5,
                showarrow=False,
                font=dict(size=14, color="#666666"),
            )

        if environmental.timestamps.shape[0] > 0:
            t_env = to_elapsed_minutes(environmental.timestamps, t0)
            if t_env.shape[0] > 2000:
                t_temp, temp_min, temp_max = bin_reduce_minmax(
                    t_env, environmental.temperature, environmental.temperature, n_bins=2000
                )
                temp_y = (temp_min + temp_max) / 2.0
                t_hum, hum_min, hum_max = bin_reduce_minmax(
                    t_env, environmental.humidity, environmental.humidity, n_bins=2000
                )
                hum_y = (hum_min + hum_max) / 2.0
            else:
                t_temp = t_env
                temp_y = environmental.temperature
                t_hum = t_env
                hum_y = environmental.humidity

            fig.add_trace(
                go.Scatter(
                    x=t_temp, y=temp_y, mode="lines",
                    name="Temperatura (°C)", line=dict(color=TEMPERATURE_COLOR), yaxis="y2",
                    hovertemplate="t = %{x:.2f} min<br>Temp = %{y:.1f} °C<extra></extra>",
                )
            )
            fig.add_trace(
                go.Scatter(
                    x=t_hum, y=hum_y, mode="lines",
                    name="Humedad (%)", line=dict(color=HUMIDITY_COLOR, dash="dot"), yaxis="y2",
                    hovertemplate="t = %{x:.2f} min<br>Humedad = %{y:.1f} %<extra></extra>",
                )
            )

        if events.timestamps.shape[0] > 0:
            event_trace = build_event_lines_trace(
                events, t0, y_min=y_min, y_max=y_max, color=EVENT_COLOR, visible=show_events
            )
            if event_trace is not None:
                fig.add_trace(event_trace)

        # Siempre la última: ver :func:`_build_selection_anchor_trace`.
        fig.add_trace(anchor)

        fig.update_layout(
            xaxis=dict(title=TIME_AXIS_TITLE),
            yaxis=dict(title=f"Amplitud {sensor_config.name} (cruda)"),
            yaxis2=dict(title="Temp. (°C) / Humedad (%)", overlaying="y", side="right"),
            legend=dict(orientation="h", y=1.08),
            margin=dict(l=60, r=60, t=30, b=40),
            height=280,
            uirevision=uirevision,
            hovermode="closest",
            clickanywhere=True,
        )
        return fig
