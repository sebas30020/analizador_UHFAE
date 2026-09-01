# Prompt: Arreglar el bloqueo por hover en la gráfica de serie temporal (gráfica #1)

## 1. Resumen y Metodología AVO

Eliminar el lag extremo y la congelación de la pestaña del navegador al pasar el cursor sobre `#graph-timeseries` (serie temporal global + variables ambientales), preservando al 100 % la funcionalidad interactiva:
1. Clic para navegar a la señal más cercana.
2. Selección por lazo / rectángulo para filtrar señales.
3. Líneas de eventos y conmutación de visibilidad.
4. Tooltip informativo en las trazas ambientales (temperatura y humedad).

### Metodología AVO (*Agentic Variation Operators*)
- **Memoria persistente de dos velocidades:**
  - Consultar `.avo/knowledge.md` antes de proponer cambios para asegurar respeto a las invariantes del proyecto.
  - Mantener `.avo/state.md` reescrito con el objetivo activo y estado actual (~200 líneas).
  - Registrar el intento en `.avo/ledger.jsonl` (`approach_tag: hover-skip-clickanywhere`).
  - Registrar cualquier hipótesis descartada en `.avo/deadends.md`.
- **Feedback anclado:** Ejecutar `.avo/verify.sh` para obtener una evaluación objetiva e independiente basada en la suite de pruebas y typechecker.

---

## 2. Diagnóstico Técnico y Causa Raíz

- **Localización:** `ui/components/graph_timeseries.py` dibuja la envolvente como un único `go.Scattergl` con $3 \times N_{\text{señales}}$ puntos (`build_vertical_segments` en `viz/decimation.py`): **37 452 puntos en UHF y 61 722 en AE** con los datasets reales.
- **Mecanismo de saturación en Plotly.js (v3.8.2):**
  - En `scattergl/calc.js`, el índice espacial kd-tree solo se construye si `_length >= TOO_MANY_POINTS` (`TOO_MANY_POINTS = 1e5`). Al estar por debajo de 100 k puntos, Plotly almacena `stash.ids` con todos los índices.
  - En cada evento de hover (~20/s), `scattergl/hoverPoints.js` ejecuta un barrido lineal sobre el array completo llamando a `xa.c2p()`, `ya.c2p()` y `Math.sqrt` por cada punto. Este cálculo satura el hilo principal del navegador.
- **Solución y Viabilidad:**
  - En Plotly.js `Fx.hover`, `hoverinfo: "skip"` excluye la traza del cálculo de hover por completo (sin barrido en `hoverPoints`, sin etiquetas ni eventos `plotly_hover`).
  - La función `determineSearchTraces` (utilizada para selección por lazo/caja) filtra únicamente por `visible` y `_module.selectPoints`, **sin evaluar `hoverinfo`**, por lo que el filtrado por selección sigue funcionando intacto.
  - Para no perder la navegación al hacer clic sobre el área de la gráfica, se habilita `clickanywhere=True` en el `Layout`, lo que hace que `dcc.Graph` propague `xvals` en el payload de `clickData`.

---

## 3. Especificación Detallada de Cambios

### 3.1. `ui/components/graph_timeseries.py`

1. **Traza de la Envolvente (`go.Scattergl`):**
   - Añadir `hoverinfo="skip"`.
   ```python
   fig.add_trace(
       go.Scattergl(
           x=xs,
           y=ys,
           mode="lines+markers",
           line=dict(color=SIGNAL_COLOR, width=1),
           marker=dict(size=3, color=SIGNAL_COLOR),
           name=f"Señal {sensor_config.name} (envolvente)",
           yaxis="y1",
           hoverinfo="skip",
       )
   )
   ```

2. **Trazas Ambientales (`go.Scatter` de Temperatura y Humedad):**
   - Añadir `hovertemplate` formateado con `<extra></extra>` para evitar cajas secundarias redundantes.
   ```python
   fig.add_trace(
       go.Scatter(
           x=t_env,
           y=environmental.temperature,
           mode="lines",
           name="Temperatura (°C)",
           line=dict(color=TEMPERATURE_COLOR),
           yaxis="y2",
           hovertemplate="t = %{x:.2f} min<br>Temp = %{y:.1f} °C<extra></extra>",
       )
   )
   fig.add_trace(
       go.Scatter(
           x=t_env,
           y=environmental.humidity,
           mode="lines",
           name="Humedad (%)",
           line=dict(color=HUMIDITY_COLOR, dash="dot"),
           yaxis="y2",
           hovertemplate="t = %{x:.2f} min<br>Humedad = %{y:.1f} %<extra></extra>",
       )
   )
   ```

3. **Layout (`fig.update_layout`):**
   - Añadir `clickanywhere=True` y `hovermode="closest"`.
   ```python
   fig.update_layout(
       shapes=build_event_line_shapes(events, t0, EVENT_COLOR, visible=show_events),
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
   ```

4. **Docstring:**
   - Actualizar el docstring de `build_timeseries_figure` documentando explícitamente el uso de `hoverinfo="skip"` y `clickanywhere=True` debido al umbral `TOO_MANY_POINTS = 1e5` en WebGL de Plotly.js.

---

### 3.2. `ui/callbacks/sensor_window_callbacks.py` (`_on_navigate`)

1. **Extracción robusta de coordenadas:**
   Extraer la coordenada temporal priorizando `clickData["xvals"][0]` cuando esté presente (emitido por `clickanywhere=True`) y cayendo a `points[0]["x"]` para gráficas sin `clickanywhere` (#3). Si no hay ninguno → `PreventUpdate`.
   ```python
   click_target = None
   if triggered_kind in ("graph-timeseries", "graph-metric"):
       click_data = ctx.triggered[0]["value"] if is_metric_click else click_timeseries
       click_dict = click_data or {}

       # Con clickanywhere=True (gráfica #1), dcc.Graph entrega xvals/yvals con points=[].
       # En gráficas #3 (sin clickanywhere), entrega points=[{"x": ...}].
       x_val = None
       if click_dict.get("xvals"):
           x_val = click_dict["xvals"][0]
       elif click_dict.get("points"):
           x_val = click_dict["points"][0].get("x")

       if x_val is None:
           raise PreventUpdate

       clicked_unix = elapsed_minutes_to_unix_seconds(float(x_val), dataset.t0)
       click_target = state.nearest_index_for_timestamp(sensor, clicked_unix)
   ```

2. **Guardarraíl de doble clic / misma señal:**
   Extender el guardarraíl de `PreventUpdate` para que clics (`"graph-timeseries"`, `"graph-metric"`, `"graph-map"`, `AUTOPLAY_TRIGGER_ID`) que resuelvan en la señal ya activa (`click_target == current`) levanten `PreventUpdate`, evitando repintados innecesarios de `graph-signal`.

---

## 4. Invariantes y Restricciones (Lo que NO se debe tocar)

1. **`viz/decimation.py` y `ENTRIES_PER_SEGMENT`:** No diezmamos la envolvente. Cada señal sigue aportando 3 entradas (`min, max, NaN`).
2. **`ui/callbacks/filtering.py::resolve_timeseries_selection_indices`:** La resolución del lazo se mantiene por `curveNumber == 0` y `pointNumber // 3`. `hoverinfo="skip"` no altera la indexación de trazas ni la captura de puntos seleccionados.
3. **`mode="lines+markers"`:** Se mantiene tal cual para proporcionar retroalimentación visual al encerrar señales con el lazo.
4. **Navegación en Mapas 2D/3D (`graph-map`):** No alterar la rama `elif triggered_kind == "graph-map"`.

---

## 5. Plan de Pruebas Unitarias

### 5.1. `tests/test_graph_timeseries.py`
- `test_envelope_trace_is_excluded_from_hover`: `fig.data[0].hoverinfo == "skip"`.
- `test_layout_enables_clickanywhere_and_closest_hover`: `fig.layout.clickanywhere is True` y `fig.layout.hovermode == "closest"`.
- `test_environmental_traces_have_informative_hovertemplate`: Verificar `hovertemplate` definido en trazas ambientales.
- Caso sin señales activas: Verificar que la ausencia de señales activas no rompe la figura.

### 5.2. `tests/test_sensor_window_callbacks.py`
- `test_click_on_timeseries_with_clickanywhere_payload_navigates`: Payload `{"points": [], "xvals": [1.5], "yvals": [0.0]}`.
- `test_click_on_timeseries_prefers_xvals_over_environmental_point`: Payload con ambos, verifica prioridad de `xvals`.
- `test_click_on_metric_with_standard_points_payload_navigates`: Payload tradicional con `points`.
- `test_click_with_empty_payload_prevents_update`: `{"points": [], "xvals": []}` y `None`.
- `test_click_on_same_signal_prevents_update`: Verifica guardarraíl de `PreventUpdate`.

---

## 6. Procedimiento de Verificación AVO y Criterios de Aceptación

1. **Línea base (Antes de los cambios):**
   - Arrancar app: `.venv\Scripts\python.exe scripts/run_dev_server.py`
   - Abrir `/sensor/AE` y `/sensor/UHF`. Medir tiempo por evento en `Fx.hover` y conteo de long tasks.

2. **Verificación tras los cambios:**
   - **Fluidez:** 60 fps en hover sobre `#graph-timeseries` sin lag ni congelación.
   - **Tooltips:** Hover sobre temperatura/humedad muestra tooltip con `min`, `°C` y `%`.
   - **Navegación:** Clic en cualquier área navega a la señal más cercana; doble clic restablece zoom sin doble repintado.
   - **Filtrado:** Lazo / rectángulo sobre envolvente descuenta exactamente las señales seleccionadas.
   - **Líneas de eventos:** Conmutación "Mostrar eventos" funciona sin error.

3. **Contrato de Verificación AVO y Suite:**
   ```bash
   .avo/verify.sh
   .venv\Scripts\python.exe -m pytest tests/ -q
   .venv\Scripts\python.exe -m mypy core data metrics cache ui viz utils
   ```

4. **Documentación y Registro AVO:**
   - `docs/RENDIMIENTO.md`: Registrar mediciones de latencia en navegador en §1.2.
   - `README.md`: Documentar interacción de clic y tooltips.
   - `.avo/state.md` y `.avo/ledger.jsonl`: Registrar el intento y veredicto.
