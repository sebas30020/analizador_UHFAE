
# Fase 5 — Ventanas gemelas y adicionales (entrega)

Formato de respuesta según PROMPT maestro §13. Alcance (§12): parametrización UHF/AE,
generación bajo demanda de ventanas de métricas (máx. 4 gráficas cada una), estado
compartido.

## 1. Decisiones de diseño

- **"Ventanas gemelas" = rutas distintas del mismo servidor, no layouts duplicados.**
  Se agregó ruteo con `dcc.Location` (`ui/app.py`): `/sensor/UHF`, `/sensor/AE` y
  `/sensor/<sensor>/window/<id>`. Los callbacks de `sensor_window_callbacks.py` se
  registran **una sola vez** para todo el proceso (antes tomaban `sensor` como
  parámetro fijo y se registraban por ventana); ahora cada callback lee de qué sensor
  se trata desde un Store `page-sensor` sembrado al construir esa página. Esto cumple
  literalmente el §6.1 del PROMPT ("mismo componente parametrizado, no duplicando
  código") sin necesitar IDs de patrón (`MATCH`/`ALL`), porque solo una página está
  montada en el DOM a la vez por pestaña.
- **Estado compartido vía `AppState` singleton + "sembrado al render"**: cargar un
  dataset en la ventana UHF ingiere ambos sensores a la vez (ya lo hacía desde Fase 1),
  así que abrir `/sensor/AE` en una pestaña nueva ya encuentra los datos. Se agregó
  `AppState.dataset_version` (contador monotónico) para que cada página nueva sepa, al
  construirse, si ya hay un dataset cargado — verificado explícitamente en la prueba
  end-to-end (§3): la ventana AE recién abierta ve `dataset-version=1` sin que el
  usuario repita "Seleccionar base de datos".
- **Límite de 4 gráficas con Dropdown multi-selección**, no botones de "quitar" por
  gráfica con IDs de patrón: usar `dcc.Dropdown(multi=True)` da la funcionalidad de
  agregar/quitar gratis (las "chips" del componente ya son removibles), y el propio
  Store `window-metrics` sirve a la vez de fuente de verdad para las gráficas y de
  "última selección válida" para poder rechazar un 5º elemento y revertir el picker —
  evita necesitar `MATCH`/`ALL` callbacks para esta fase.
- **Selector de métricas de la ventana adicional combina los 3 regímenes** (puntual,
  grupo intrínseca, grupo reducción) en una sola lista de 37 opciones, codificando
  `"<regimen>:<metric_id>"` porque un mismo `metric_id` (p. ej. "kurtosis") puede
  aparecer en dos regímenes distintos con significados distintos.
- **"+ Nueva ventana de métricas" es 100% cliente** (`clientside_callback` con
  `window.open`): no hay nada que calcular en el servidor para "crear" una ventana, el
  id se genera en el navegador (`crypto.randomUUID()`) y recién se hace trabajo de
  servidor cuando esa pestaña nueva carga su propia página.

## 2. Código

```
ui/app.py                              (reescrito: ruteo + registro único de callbacks)
ui/state.py                            (+ AppState.dataset_version)
ui/components/sensor_window.py         (sensor dinámico vía Store, enlaces a ventana gemela, botón nueva ventana)
ui/components/control_panel.py         (+ initial_db_label)
ui/components/metrics_window.py        (nuevo)
ui/callbacks/sensor_window_callbacks.py (register_callbacks(app) sin sensor fijo, + clientside callback)
ui/callbacks/metrics_window_callbacks.py (nuevo)
ui/callbacks/helpers.py                (+ parse_route, encode/decode_metric_option, clamp_metric_selection)
```

## 3. Pruebas

**152/152 pruebas en verde** (`pytest tests/ -q`), 9 nuevas de esta fase (parseo de
rutas, codificación de opciones de métrica, tope de selección). `mypy` sin errores
(59 archivos).

**Verificación end-to-end contra el servidor real** (sin navegador disponible en este
entorno, mismo enfoque que la Fase 4 — peticiones HTTP directas a
`/_dash-update-component`): recorrí el flujo completo —

1. Renderizar `/sensor/UHF` (25 componentes, incluidos `btn-new-metrics-window` y
   `page-sensor`).
2. Cargar el dataset real.
3. Renderizar `/sensor/AE` **desde cero** (simulando una pestaña nueva) y confirmar que
   `dataset-version` ya viene sembrado en `1` — la garantía central de "estado
   compartido" de esta fase.
4. Renderizar `/sensor/UHF/window/test-abc` y confirmar que **no** contiene
   `graph-timeseries` ni `graph-signal` (la ventana adicional es solo gráficas tipo #3,
   PROMPT §6.3).
5. Seleccionar 2 métricas en el picker.
6. Intentar seleccionar una 5ª → **rechazada**, el picker vuelve a las 2 anteriores.
7. Refrescar y confirmar exactamente 2 gráficas renderizadas.

Todo el flujo respondió correctamente.

## 4. Supuestos asumidos

- **Sin empuje en tiempo real entre pestañas ya abiertas** (documentado también en
  `ui/state.py::AppState.dataset_version`): si el usuario carga un dataset *nuevo*
  mientras otra pestaña ya está abierta, esa pestaña no se refresca sola — necesita
  recargar. El "estado compartido" de esta fase cubre "una ventana nueva ve lo que ya
  existe", no sincronización push entre ventanas ya abiertas simultáneamente. Implementar
  eso requeriría polling (`dcc.Interval`) o WebSockets, fuera del alcance que pide el
  PROMPT para esta fase específicamente (el filtrado cruzado de Fase 6 sí exige estado
  compartido de selección en tiempo real entre ventanas, y ahí se revisará si hace falta
  resolver esto de fondo).
- Los controles de agrupamiento (modo/valor/reductor/percentil) de la ventana adicional
  son **compartidos por las 4 gráficas de esa ventana**, no uno por gráfica — simplifica
  la UI; si dos métricas de grupo necesitan criterios de agrupamiento distintos
  simultáneamente, hace falta abrir dos ventanas adicionales.
- El límite de ventanas adicionales abiertas lo impone el propio navegador (memoria/
  pestañas), no hay un tope artificial del lado del servidor — el PROMPT permite
  "tantas ventanas adicionales como desee".

## 5. Riesgos de rendimiento

- Ninguno nuevo. Cada ventana adicional con 4 métricas de grupo dispara hasta 4
  lecturas/cálculos vía `cache/service.py`, ya medido en <20ms por métrica en caché
  caliente (Fase 3) — no se probó el caso de 4 métricas en frío simultáneas, pero al
  ser secuencial dentro de un mismo callback el costo es la suma de los 4 individuales,
  ya aceptable.

## Próximo paso

Fase 6 — Filtrado cruzado: lazo de Plotly desde la gráfica tipo #1 y cualquier gráfica
tipo #3 (en cualquier ventana), máscara de selección única por sensor en `ui/state.py`,
propagación bidireccional total, deshacer/rehacer/restablecer.
