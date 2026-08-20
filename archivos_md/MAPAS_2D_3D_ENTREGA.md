# Mapas de separación 2D (#4) y 3D (#5) — entrega

Rama `mapas-2d-3d`. Origen: [prompt-mapas2d3d.md](prompt-mapas2d3d.md); plan y
reconocimiento previo en [PLAN_MAPAS_2D_3D.md](PLAN_MAPAS_2D_3D.md).

Dos gráficas nuevas al final de la ventana de sensor donde **un punto es una señal**,
ubicada por dos (2D) o tres (3D) métricas puntuales cualesquiera del mismo catálogo que
ya usan las gráficas #3.

## 1. Decisiones que recortaron el prompt (acordadas con el usuario)

El prompt daba por existentes cosas que no estaban en el código, o pedía cosas que la
librería de graficado no puede hacer. Las cuatro se resolvieron antes de escribir código:

| # | Decisión | Motivo |
|---|---|---|
| D1 | **Sin clusters**: color único | `grep -i cluster` sobre el código: cero coincidencias. No había paleta, criterio ni algoritmo que "reutilizar". El clustering es un problema de análisis, no de visualización; queda para su propia rama |
| D2 | **Lazo solo en el mapa 2D** | Plotly no ofrece lazo ni caja dentro de una escena 3D — su modebar solo trae órbita/zoom/reset. Verificado en el navegador (§4) |
| D3 | **Ejes solo de régimen puntual** | Es el único que da un escalar por señal. Con métricas de grupo, "un punto = una señal" (requisito marcado como crítico) deja de ser cierto |
| D4 | **Sin exportación de datos** | No existía ningún flujo de exportación en la app que "reutilizar". Queda el PNG del modebar de Plotly, que ya venía activo. Alcance eliminado a petición del usuario |

Sobre el §6 del prompt ("controles que no deben aparecer en #4/#5"): no hubo nada que
ocultar. Línea de referencia, promedio, tendencia, suavizado, eventos y agrupamiento son
controles **globales del panel lateral**, no por gráfica — basta con que los mapas no los
lean.

**Cero dependencias nuevas**: `go.Scatter3d` ya venía en plotly 6.9.0.

## 2. Qué cambió

- **`viz/maps.py`** (nuevo): `build_map_dataset` pide cada eje a
  `cache/service.py::get_or_compute_puntual` — el mismo camino que las gráficas #3, sin
  reimplementar ninguna fórmula — descarta las señales sin valor finito en algún eje y
  cuenta cuántas por métrica. `resolve_map_highlight_coords` resuelve dónde está la señal
  seleccionada dentro de un mapa.
- **`ui/components/graph_map_{common,2d,3d}.py`** (nuevos): `Scattergl` y `Scatter3d`,
  tooltip, estado vacío informativo, aviso de eje repetido, y la disposición de trazas
  como contrato (puntos en la 0, resaltado en la 1).
- **`ui/map_registry.py`** (nuevo): registro de proceso de los `MapDataset` visibles,
  mismo patrón que `ui/reference_registry.py`.
- **`ui/components/control_panel.py`**: bloque "Mapas de separación" con checkbox
  independiente por mapa y selectores de eje poblados solo con métricas puntuales.
- **`ui/components/sensor_window.py`**: `maps-container`, hermano y posterior a
  `metrics-graphs-container`.
- **`ui/callbacks/sensor_window_callbacks.py`**: render de los mapas, clic → Gráfica #2,
  resaltado por `dash.Patch` y lazo → filtrado.

Cero cambios en `metrics/`, `cache/`, `core/` y `data/`.

### Ordenación garantizada por estructura, no por callback

El §2 del prompt exige que los mapas queden **siempre** debajo de las gráficas de
métricas. `maps-container` es un `html.Div` hermano y posterior a
`metrics-graphs-container` en el layout, así que agregar o quitar una métrica nunca puede
reordenarlos: es una propiedad del árbol de componentes, no algo que un callback tenga que
mantener.

### Alineación de ejes sin emparejar por timestamp

`get_or_compute_puntual` consulta y persiste el caché siempre sobre el conjunto completo,
y aplica `active_mask` como recorte posterior puro. El orden resultante es siempre
`np.where(valid_mask & active_mask)[0]`. Dos métricas puntuales del mismo sensor con la
misma máscara salen por tanto **alineadas índice a índice**, y ese mismo array es el
índice global de señal de cada punto. No hace falta buscar por timestamp en ninguna parte
del mapa: ni para el tooltip, ni para el clic, ni para el lazo.

## 3. Dos bugs encontrados en uso real, no por las pruebas

Ambos aparecieron cuando el usuario probó la funcionalidad en su navegador, con las
pruebas de la rama en verde. Merecen quedar registrados porque las dos causas son
trampas reutilizables, no descuidos puntuales.

### 3.1 `customdata` nunca llega al servidor

**Síntoma**: el clic en un punto de #4/#5 no graficaba la señal en #2, y el lazo del mapa
2D no filtraba nada — pero el tooltip sí mostraba la señal correcta, y el lazo de #1/#3
seguía funcionando.

**Causa**: plotly.py 6.x serializa los arrays de numpy como *typed arrays* en base64
(`{"dtype", "bdata"}`) en vez de listas. El `filterEventData` de `dcc.Graph` reconstruye
el `customdata` de cada punto haciendo `gd.data[curveNumber].customdata[pointNumber]`:
indexar ese objeto con un entero da `undefined`, así que el punto llega **sin**
`customdata`. Medido en el navegador sobre el dataset real:

| Expresión | Valor |
|---|---|
| `gd.data[0].customdata` | `Object` con claves `{dtype, bdata, _inputArray}` |
| `gd.data[0].customdata[5]` ← lo que Dash envía | `undefined` |
| `gd._fullData[0].customdata` | `Int16Array` |
| `gd._fullData[0].customdata[5]` ← lo que ve el tooltip | `5` |

El tooltip funciona porque lo renderiza Plotly desde `_fullData`, ya decodificado, sin
pasar por Dash. Por eso fallaba justo lo que dependía de `customdata` y sobrevivía lo que
usa `x`: ese viene del evento de Plotly, no lo re-deriva Dash.

**Arreglo**: identificar el punto por **posición** (`curveNumber` + `pointNumber`, números
planos que sobreviven el filtro intactos) contra `MapDataset.signal_indices` del registro.
Los mapas siguen sembrando `customdata` porque el tooltip sí lo usa.

**Por qué las pruebas no lo vieron**: usaban payloads con `customdata`, una forma que el
navegador nunca produce. Se reescribieron con la forma real y montando el mapa de verdad
para poblar el registro.

### 3.2 El lazo de la gráfica #1 ignoraba la amplitud

**Síntoma** (reportado por el usuario): el lazo sobre la envolvente min/max no filtraba lo
que encerraba; la única variable que decidía era el ancho del lazo en tiempo.

**Causa**: una decisión de la Fase 6 que se quedó un paso corta. Como cada señal aporta
tres entradas a la traza (mínimo, máximo y separador `NaN` de `build_vertical_segments`),
se concluyó que "el índice de punto de Plotly no es el índice de señal" y se resolvió la
selección por rango de tiempo: se colapsaba a `[min(x), max(x)]` y se excluían **todas**
las señales de esa franja. Con un lazo alto y estrecho el resultado parecía correcto por
casualidad; con uno ancho excluía señales que el usuario nunca encerró.

**Arreglo**: la posición sí es derivable de forma exacta — la señal del punto
`pointNumber` es `pointNumber // ENTRIES_PER_SEGMENT`. `resolve_timeseries_selection_indices`
sustituye a `resolve_timeseries_selection_range` + `indices_in_time_range`, que quedaron
sin uso y se eliminaron. Filtra además por `curveNumber`, para que un lazo que roce las
series de temperatura/humedad no excluya señales por una correspondencia de índices sin
significado.

**Consecuencia aceptada**: un lazo que cruce el centro de un segmento sin contener ninguno
de sus dos extremos ya no selecciona esa señal — Plotly solo conoce los vértices que
dibuja. Es coherente con lo que se ve (ambos extremos llevan marcador,
`mode="lines+markers"`) y preferible a excluir de más en silencio. Se pierde también la
posibilidad de filtrar una franja temporal completa de un tirón; si vuelve a hacer falta,
debe ser un control explícito y no un efecto colateral del lazo.

## 4. Verificación

- **399/399 pruebas** de la suite completa; `mypy core data metrics cache ui viz utils`
  sin errores de tipos reales (mismo ruido preexistente de `import-untyped`).
- **Pruebas nuevas**: `tests/test_maps.py` (alineación de ejes, exclusión por
  `valid_mask`, filtrado en vivo sin invalidar caché, NaN real de kurtosis descartando la
  fila en todos los ejes, rechazo de métricas de grupo), `tests/test_graph_map_2d.py`,
  `tests/test_graph_map_3d.py`, `tests/test_map_registry.py`, más las de callbacks e
  integración en `tests/test_sensor_window_callbacks.py` (clic por posición, traza de
  resaltado ignorada, parche de resaltado sin recálculo, lazo → `AppState` de extremo a
  extremo) y `tests/test_filtering.py`.
- **Navegador, `med_5_ago_3.hdf5`, 12 484 señales UHF**: ambos mapas renderizan datos
  reales; el modebar del 2D (`scattergl`) expone Box Select y Lasso Select mientras el del
  3D (`scatter3d`) expone solo Orbital rotation / Turntable rotation / Reset camera —
  evidencia observable de D2; estado vacío y aviso de eje repetido correctos; agregar una
  métrica la inserta encima de los mapas sin moverlos del final; cero errores de consola.
  La estructura de la gráfica #1 que sustenta §3.2 también se verificó ahí: traza 0
  `scattergl` `lines+markers` con 37 452 entradas = 3 × 12 484, trazas 1 y 2 Temperatura y
  Humedad, y `3k`/`3k+1` compartiendo `x` con `3k+2` a `NaN`.
- **Limitación del entorno**: el clic real sobre las trazas WebGL no se pudo automatizar
  (el panel del navegador de la sesión de desarrollo no compone frames, y el *hit-testing*
  de Plotly depende de leer el framebuffer). Se verificó con pruebas de integración que
  ejecutan el código exacto que Dash invoca, y el usuario confirmó el comportamiento en su
  navegador.

## 5. Rendimiento

Medido sobre `med_5_ago_3.hdf5` (12 484 señales UHF), detalle en
[benchmarks_baseline/mapas_2d_3d.md](benchmarks_baseline/mapas_2d_3d.md):

| Operación | Mediana |
|---|---:|
| `build_map_dataset` 2D, caché caliente | 30 ms |
| `build_map_dataset` 3D, caché caliente | 52 ms |
| `build_map_2d_figure` / `build_map_3d_figure` | 15 ms / 32 ms |
| Resaltado al navegar (`resolve_map_highlight_coords`) | 0,035 ms |

El caché frío (865 ms para los dos ejes del 2D) es el costo de calcular las métricas, el
mismo que ya paga una gráfica #3 la primera vez — no es específico de los mapas, y se
amortiza porque el resultado queda en el caché compartido.

Los 0,035 ms del resaltado son la justificación del diseño de la etapa 4: navegar entre
señales mueve una traza de un punto con `dash.Patch` en vez de reconstruir la nube
completa, que costaría ~45 ms (dataset + figura) en cada "Siguiente" o tick de auto-play.
No se necesitó diezmado: 12 484 puntos en `Scattergl` y `Scatter3d` van sobrados.

## 6. Fuera de alcance

Clusters (D1) y exportación de datos (D4). El mapa 3D no participa del filtrado por
selección (D2) — sí responde al clic, igual que el 2D.
