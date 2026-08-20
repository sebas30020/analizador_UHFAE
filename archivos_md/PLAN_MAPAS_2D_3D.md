# Plan — Mapas de separación 2D (#4) y 3D (#5)

Origen: `archivos_md/prompt-mapas2d3d.md`. Este documento registra el reconocimiento
previo (§0 del prompt), las decisiones tomadas junto con el usuario para resolver las
ambigüedades del prompt original, y el plan por etapas vigente.

## Reconocimiento (§0 del prompt)

1. **Catálogo de métricas y cálculo.** Registro por decorador en `metrics/registry.py`
   (`@metric(...)`), descubierto por introspección (`discover_metrics`). El catálogo de
   la UI se puebla en `ui/components/control_panel.py::_all_metric_options`, que emite
   tres opciones por métrica codificadas `"<regimen>:<metric_id>"`
   (`ui/callbacks/helpers.py::encode_metric_option`). El cálculo entra siempre por
   `cache/service.py` (`get_or_compute_puntual` / `get_or_compute_group_reduction` /
   `get_or_compute_group_intrinsic`).
2. **Renderizado #1/#2/#3 y soporte 3D.** Plotly 6.9.0 + Dash 4.4.1. #1 =
   `ui/components/graph_timeseries.py` (`Scattergl`), #2 = `graph_signal.py`, #3 =
   `graph_metric.py` (N gráficas apiladas, id de patrón
   `{"type":"graph-metric","index":<opción>}`). Plotly soporta 3D nativo (`go.Scatter3d`)
   — cero dependencias nuevas.
3. **Máscara de filtrado.** Fuente única: `AppState._active_mask[sensor]`
   (`ui/state.py`), con pilas undo/redo y contador `filter_version`. Se consume vía
   `state.get_active_mask(sensor)` y se pasa como `active_mask=` a las fachadas de
   caché.
4. **Cluster.** No existe en el código (cero resultados de `grep -i cluster`). Ver
   decisión D1.
5. **Flujo click → Gráfica #2.** Bus único: click en #1/#3 → `_on_navigate` resuelve el
   índice → escribe `nav-index.value` → dispara `_on_refresh_signal`, que redibuja #2.
   Los mapas se integran sumando su `clickData` como `Input` de `_on_navigate`.
6. **Estado de UI.** No hay store de estado de UI; vive en los propios componentes Dash
   mientras la pestaña está abierta (igual que hoy). Solo dataset/índice activo/máscara
   viven en `AppState` (estado de proceso).

## Hallazgos que contradicen supuestos del prompt

- No existe ningún concepto de cluster (algoritmo, campo en `SignalBlock`, paleta o
  leyenda) para reutilizar.
- Plotly no ofrece lazo/caja de selección en escenas 3D (`go.Scatter3d`); el modebar 3D
  solo trae órbita/zoom/reset, sin `selectedData`.
- Solo el régimen **puntual** produce un escalar por señal; los regímenes de grupo
  producen un valor por ventana de agrupamiento, incompatible con "un punto = una
  señal" (§1 del prompt, marcado como crítico).
- No existe ningún flujo de exportación de datos en la app hoy (solo el botón PNG del
  modebar de Plotly, activo por defecto).
- Los controles "fuera de alcance" del §6 del prompt (línea de referencia, promedio,
  tendencia, suavizado, eventos, agrupamiento) son controles **globales** del panel
  lateral, no por gráfica — no hay nada que ocultar en #4/#5, basta con que los mapas no
  los lean.

## Decisiones (aprobadas por el usuario, 2026-08-20)

- **D1 — Clusters: aplazados.** Los mapas usan color único. El clustering es un
  problema de análisis aparte, para una rama futura con su propio prompt.
- **D2 — Lazo: solo en el mapa 2D (#4).** El mapa 3D (#5) es de solo lectura para
  navegación (click, hover, órbita, reset); no participa en el filtrado por selección.
- **D3 — Ejes: solo métricas de régimen puntual.** Garantiza "un punto = una señal" y
  que los ejes queden alineados por construcción (mismo orden que
  `get_or_compute_puntual` produce sobre el mismo `active_mask`).
- **D4 — Exportación de datos: eliminada del alcance.** Solo queda la imagen que ya
  produce el modebar de Plotly; no se agrega ningún flujo nuevo.

## Punto técnico clave

`get_or_compute_puntual` siempre enmascara por `valid_mask` para consultar/persistir en
caché, y aplica `active_mask` como recorte posterior puro (`cache/service.py`). El orden
resultante es siempre `np.where(block.valid_mask & active_mask)[0]` (ascendente). En
consecuencia, dos llamadas para dos métricas puntuales distintas del mismo sensor, con
el mismo `active_mask`, devuelven arrays **alineados índice a índice** sin necesidad de
buscar por timestamp — los ejes X/Y/Z de un mapa se combinan directamente, y el índice
de señal global de cada punto se puede derivar una sola vez con esa misma máscara.

## Plan por etapas

0. Este documento.
1. **Núcleo puro** (`viz/maps.py` + `tests/test_maps.py`): combina ejes puntuales ya
   calculados, deriva el índice de señal global, descarta NaN/inf por eje y cuenta
   omitidos. Sin Dash, sin Plotly, sin recalcular ninguna fórmula.
2. **Componentes de figura** (`ui/components/graph_map_2d.py`, `graph_map_3d.py`):
   `Scattergl` / `Scatter3d`, tooltip, estado vacío, aviso de eje repetido.
3. **Layout, controles y render**: bloque en el panel de control, contenedor
   `maps-container` hermano y posterior a `metrics-graphs-container` — el orden queda
   garantizado por estructura del layout. Integración con filtrado vía
   `dataset-version`/`filter-version`, igual que el resto de la ventana.
4. **Click → Gráfica #2 y resaltado bidireccional**: ampliar `_on_navigate` con los
   `clickData` de #4/#5.
5. **Lazo → filtrado (solo #4)**: rama nueva en `_on_selection_changed` /
   `ui/callbacks/filtering.py`, reutilizando `pending-exclusion-indices` y el botón
   "Filtrar selección" ya existentes.
6. **Documentación y medición**: `README.md`, `docs/ARQUITECTURA.md`, nota de entrega
   `archivos_md/MAPAS_2D_3D_ENTREGA.md`, benchmark sobre el dataset real.

## Estado final

Las seis etapas están completas. El resultado, las decisiones tal como quedaron y la
verificación se documentan en [MAPAS_2D_3D_ENTREGA.md](MAPAS_2D_3D_ENTREGA.md) — este
documento queda como bitácora de lo que se planeó, no se edita para reflejar lo que se
construyó.

Lo que el plan no anticipó: dos bugs de identificación de puntos que solo aparecieron al
usar la interfaz de verdad, ambos con las pruebas de la rama en verde (§3 de la entrega).
El primero, específico de esta rama, era que `customdata` nunca llega al servidor con
plotly.py 6.x. El segundo, preexistente desde la Fase 6, era que el lazo de la gráfica #1
ignoraba la amplitud y filtraba por franja temporal. Los dos comparten la misma lección:
identificar un punto de Plotly por posición, no por datos adjuntos.

Controles del panel lateral que los mapas **no leen** (globales, dependientes del eje
temporal o del agrupamiento): `show-reference-line`, `reference-line-t`,
`smooth-puntual`, `smooth-grupo`, `smoothing-method`, `smoothing-window`,
`gap-threshold`, `show-events`, `grouping-mode`, `grouping-value`,
`grouping-reducer`, `grouping-percentile-q`.
