# Rendimiento de interacción de la gráfica #1: medir, luego arreglar

## Contexto

La gráfica de serie temporal global (la segunda en pantalla, tipo #1 en la numeración del
repo: envolvente de todas las señales + temperatura + humedad) se traba cuando el ratón
entra en ella. Con la herramienta de lazo activa el puntero queda en cruz, la página deja
de responder a clics en cualquier otro sitio, y a veces hay que recargar. El lazo se pega y
el filtrado no se puede completar.

Dos funcionalidades **no se pueden perder** — son las que dan valor al análisis:

1. Clicar una descarga y que se dibuje en la gráfica #2 y se resalte en los mapas.
2. Encerrar señales con el lazo sobre la #1 y que ese filtrado se propague a toda la ventana.

Y una restricción dura confirmada por el usuario: **cero diezmado de las señales en la
gráfica #1**. Poder ver todas y cada una es el punto de la gráfica. Cualquier solución que
agregue, submuestree o esconda señales queda descartada de entrada.

### Lo que ya sabemos, y lo que el ciclo anterior enseñó

El ciclo anterior (commits `fd1f4fd`, `f80db41`, y `docs/RENDIMIENTO.md` §1.2) intentó
arreglar esto con `hoverinfo="skip"` en la envolvente, razonando que `scattergl` no
construye su kd-tree por debajo de `TOO_MANY_POINTS = 1e5` y por tanto barre linealmente
los 61 722 puntos de AE en cada evento. **Se midió y no cambió nada**: 46,8 ms contra
42,9 ms por evento. Se revirtió.

La lección no es "no tocar el hover", es **no volver a implementar sobre una hipótesis sin
medir**. Este plan pone la medición delante y la hace bloqueante.

### La aritmética que reencuadra el problema

Lo que faltaba en §1.2 no era otra hipótesis, era esta cuenta:

- `plotly.min.js` define `HOVERMINTIME: 50` — el hover está limitado a **20 eventos/s**.
- La medición archivada dice **~43 ms por evento**.

43 ms × 20 eventos/s = **~86 % del hilo principal**, sostenido, mientras el ratón se mueva.
Eso no es "lento", es saturación: los eventos entran más rápido de lo que se drenan, la
cola crece, y el navegador deja de atender clics en el panel lateral. Explica el síntoma
exacto que se reporta — incluida la necesidad de recargar.

Esto da un **objetivo numérico** en vez de una sensación: hay que bajar `Fx.hover` a
**menos de ~10 ms por evento** (< 20 % del hilo principal). Todo lo demás del plan se
juzga contra ese número.

### El escenario real es más restrictivo de lo que parecía

El usuario reporta el problema con `med_5_ago_3.hdf5` (12 484 UHF / 20 574 AE) y **sin
ninguna gráfica de métrica abierta** — basta con cargar la medición. Esto **descarta** la
primera hipótesis que exploré (que el coste viniera del DOM inflado por las gráficas #3,
que en régimen puntual dibujan `go.Scatter(mode="markers")` — SVG — con un nodo `<path>`
por señal). Ese problema es real y hay que arreglarlo igual, pero **no es el que se está
reportando** y no puede encabezar el plan.

Con solo #1 y #2 en pantalla, lo que hay en la página es:

| Elemento | Tipo | Puntos | Nodos DOM |
|---|---|---:|---:|
| #2 señal individual | `Scatter` SVG, líneas | 3 000 (UHF) | 1 `path` |
| #1 envolvente | `Scattergl` | 37 452 (UHF) / 61 722 (AE) | canvas |
| #1 temperatura | **`Scatter` SVG** | 20 808 | 1 `path`, 20 808 vértices |
| #1 humedad | **`Scatter` SVG** | 20 808 | 1 `path`, 20 808 vértices |
| #1 líneas de evento | `layout.shapes` | 98 | **98 nodos** |

## Veredicto: se arregla, no se migra

**Recomiendo arreglar el stack actual.** Las razones, en orden de peso:

1. **El servidor no es el problema.** `docs/RENDIMIENTO.md` §1 mide render de la #1 en
   63 ms (UHF) / 90 ms (AE), lecturas de caché en 3 ms, cambio de señal en 7 ms. Todo
   cumple con margen. Migrar reescribiría la capa que funciona para arreglar la que no.
2. **El coste está en un puñado de mecanismos concretos y localizables**, todos
   verificados leyendo el `plotly.min.js` que sirve la app (§ Fase 0). No es "plotly no
   escala"; son decisiones puntuales de este código sobre qué mandarle a plotly.
3. **Migrar cuesta la capa `ui/` entera** (~2 000 líneas más sus tests) y, sobre todo, el
   filtrado cruzado bidireccional, que es la pieza con más lógica sutil del proyecto y la
   que el usuario nombra como intocable. El riesgo de romperla es alto y el beneficio
   incierto.
4. **La alternativa seria (Datashader/Bokeh) resuelve rasterizando en el servidor**, que
   es exactamente lo que el usuario descartó: implica no mandar las señales individuales
   al navegador. Es incompatible con "cero diezmado".

Alternativas evaluadas y por qué no:

| Opción | Por qué no |
|---|---|
| `plotly-resampler` | Su mecanismo **es** el diezmado dinámico por rango visible. Choca de frente con la restricción. |
| Bokeh/Panel + Datashader | Rasteriza en el servidor y manda una imagen: adiós al lazo por señal y al clic por punto tal como existen. Reescritura total de `ui/`. |
| Componente Dash propio (regl/uPlot/deck.gl) | Es la salida real **si** la Fase 0 demuestra que el coste vive en el ciclo Dash/React y no en plotly (hipótesis H2). Queda como plan B explícito, no como punto de partida. |

## Fase 0 — Medir. Bloqueante.

Nada se implementa hasta que esta fase señale al culpable. Cada hipótesis viene con una
**predicción falsable**: si la predicción no se cumple, la hipótesis se descarta y se
escribe que se descartó, igual que se hizo con `hoverinfo="skip"`.

**El instrumento**, corrigiendo el defecto que invalidó la medición anterior: una ventana
de **Chrome real en primer plano** (herramientas `mcp__claude-in-chrome__*`), no el panel
embebido. Con la pestaña en segundo plano Chrome limita `setTimeout` a 1/s y suspende
`requestAnimationFrame`, que es por qué en §1.2 quedaron sin medir fps y *long tasks*.

**Protocolo**: `med_5_ago_3.hdf5`, `/sensor/AE` (el caso peor) y `/sensor/UHF`, sin
métricas abiertas, caché caliente. Mediana de 10 muestras separadas > 70 ms para saltar
`HOVERMINTIME` y que cada llamada corra síncrona — mismo método que §1.2, para que los
números sean comparables con los ya archivados.

| # | Hipótesis | Predicción falsable | Medición |
|---|---|---|---|
| **H1** | Las dos curvas ambientales SVG (20 808 puntos cada una) dominan el barrido de hover. Nunca se probó: el A/B anterior solo sacó la envolvente. | `hoverinfo="skip"` en temperatura+humedad baja `Fx.hover` a la mitad o más. | A/B con `Plotly.restyle`, cronometrando `Plotly.Fx.hover(gd, {xpx, ypx}, 'xy')`. |
| **H2** | El coste no está en plotly sino en el ciclo de Dash: `dcc.Graph` llama `setProps({hoverData})` en cada hover, lo que dispara un *dispatch* de redux y una reconciliación de React. **La medición anterior lo excluía por construcción** — React agrupa la actualización fuera de la ventana cronometrada. | El perfil de Performance muestra tareas de React/redux entre eventos de hover, ausentes del número de §1.2. | Panel Performance, grabar un barrido de 5 s, buscar `TreeContainer` / `dispatch` / `updateProps`. |
| **H3** | El reflow forzado de `_calcInverseTransform` (llama `getBoundingClientRect`, confirmado en el bundle) sobre un documento con 98 shapes de evento. | Apagar "Mostrar eventos" (el control **ya existe**) baja el coste de forma medible. | A/B con el toggle existente. Coste cero de implementación. |
| **H4** | El lazo se congela por el test punto-en-polígono. Confirmado en el bundle: el *tester* marca `isRect` y usa comparación de caja O(1) por punto **solo** para el rectángulo; el lazo hace ray-casting sobre los V vértices del polígono. | **Box-select va fluido y el lazo no**, con el mismo dataset y la misma cantidad de puntos. | Comparación directa a mano. Es la prueba más barata del plan. |
| **H5** | `scattergl` re-sube los buffers de selección en cada tick del arrastre. El bundle define `SELECTDELAY: 100`, así que corre ~10 veces por segundo. | El perfil muestra tareas de ~100 ms alineadas con el *throttle* durante el arrastre. | Panel Performance durante un arrastre de lazo. |

**Una pregunta que esta fase ya no tiene que responder**: el formato de transporte de la
figura #1. Se midió al escribir este plan y está resuelto — plotly.py serializa los arrays
de numpy en binario base64, no como texto decimal. Los números y lo que implican, en la
Fase 3.

## Fase 1 — Lo que baja el coste de hover

Se implementa **solo lo que la Fase 0 respalde**, en este orden.

### F1 — Diezmar las curvas ambientales (si H1) · `ui/components/graph_timeseries.py`

Temperatura y humedad son 20 808 puntos cada una sobre un eje de ~1 200 px: **17 puntos
por píxel**. Reducirlas con `viz.decimation.bin_reduce_minmax` — que ya existe y cuya
docstring dice explícitamente "agregación exacta, no un submuestreo" — a ~2 000 puntos
conserva todos los extremos visibles y es indistinguible en pantalla.

**Esto no toca ninguna señal.** Las ambientales no son señales: el propio docstring de
`build_timeseries_figure` ya las trata aparte ("Ambientales/eventos no son 'señales'"), y
`active_mask` nunca las ha afectado. La restricción de "cero diezmado" se respeta al pie
de la letra sobre la envolvente. Aun así conviene que el usuario lo confirme al ver el
resultado, porque es una decisión visible.
**Escape si el diezmado no basta**: `hoverinfo="skip"` en temperatura y humedad las saca
del bucle de trazas de `Fx.hover`, que filtra por `ae.hoverinfo !== "skip"`. Es el mismo
mecanismo que se probó y revirtió sobre la envolvente, aplicado ahora a las trazas que H1
señala. Dos precisiones:

- **No tiene nada que ver con el kd-tree.** El kd-tree es cosa de `scattergl`
  (`TOO_MANY_POINTS`); temperatura y humedad son `go.Scatter` SVG, cuyo hover recorre la
  `calcdata` entera sin índice espacial de ningún tipo. Confundir los dos mecanismos es
  exactamente el error que costó el ciclo anterior.
- **Es peor opción que el diezmado, no complementaria.** Cuesta los tooltips de las dos
  curvas *útiles* (los valores de temperatura y humedad), y deja como única traza con
  tooltip a la envolvente, que es la que menos dice. El diezmado exacto baja el barrido
  ~10× y **conserva** ambos tooltips. Solo se recurre a `skip` si la Fase 0 mide que
  diezmar no alcanza para llegar a los 10 ms.

### F2 — Colapsar las líneas de evento (si H3) · `ui/components/event_lines.py`

98 shapes = 98 nodos SVG por gráfica. Pasarlas a una sola traza con separadores `NaN` —
el mismo patrón que ya usa `build_vertical_segments` — deja 1 nodo. El toggle "Mostrar
eventos" sigue funcionando (pasa a ser `visible` de la traza en vez de `visible` de las
shapes), y el parche con `dash.Patch` de `_on_refresh_event_shapes` se adapta.

Ojo: hay que verificar que la traza nueva no se coma un `curveNumber`, porque
`resolve_timeseries_selection_indices` filtra por `curveNumber == 0`
(`TIMESERIES_ENVELOPE_CURVE`). Va **después** de la envolvente, no antes.

### F3 — Si gana H2 (ciclo Dash/React)

No hay perilla soportada para que `dcc.Graph` deje de emitir `hoverData`; ningún callback
lo escucha y aun así el *dispatch* ocurre. Si H2 domina, las opciones reales son, en orden
de coste:

1. Reducir el trabajo de reconciliación adelgazando el árbol de la ventana (medir primero
   cuánto cuesta el panel lateral).
2. Un `assets/*.js` que intercepte el evento antes de `setProps` — contenido, pero es un
   parche sobre el interior de dash-renderer y hay que documentarlo como tal.
3. Envolver la #1 en un componente Dash propio. **Este es el único escenario donde migrar
   la filosofía de graficación se justifica**, y solo para esta gráfica.

Si H2 domina, este plan se detiene aquí y se rehace la decisión con el número en la mano.

## Fase 2 — Resolver la selección en el servidor

Es el cambio de fondo y **es independiente de qué gane la Fase 0**: mejora el lazo,
elimina el payload de miles de puntos, y hace el filtrado *más* exacto que hoy.

**Hecho verificado**: `dcc.Graph::filterEventData` adjunta `range` (caja) y `lassoPoints`
(polígono) a `selectedData` de forma incondicional. Está en el bundle que sirve la app,
`.venv/Lib/site-packages/dash/dcc/async-graph.js`:

```js
has("range", t) && (n.range = t.range);
has("lassoPoints", t) && (n.lassoPoints = t.lassoPoints);
```

Hoy `_on_selection_changed` ignora ambos y usa solo `points`, lo que obliga a que cada
señal encerrada viaje como un objeto JSON completo desde el navegador.

**El cambio**: `ui/callbacks/filtering.py` gana una función pura que recibe el polígono (o
el rectángulo) en coordenadas de datos y lo prueba contra los arrays completos en numpy —
`block.timestamps` convertidos a minutos y `block.minmax`. `resolve_timeseries_selection_indices`
pasa a preferir `lassoPoints`/`range` cuando vienen, y conserva la ruta por `points` como
respaldo.

Lo que se gana:

- **El payload del navegador al servidor pasa de miles de puntos a ~40 vértices.**
- **El filtrado se vuelve exacto.** Hoy el `README.md` documenta una limitación real: "Un
  lazo que cruce el centro de un segmento sin tocar ninguno de sus dos extremos no lo
  selecciona — Plotly solo conoce los vértices que dibuja". Probando el segmento completo
  `(t, min, max)` contra el polígono en el servidor, esa limitación desaparece. Es una
  mejora de exactitud científica, no solo de velocidad.
- El trabajo pasa a numpy vectorizado sobre la matriz entera, que es la disciplina que
  `CLAUDE.md` ya exige para el régimen puntual.

### El barrido en el cliente: hay un escape soportado, verificado en el bundle

Resolver en el servidor no impide, por sí solo, que plotly barra los puntos en el cliente
durante el arrastre (H4/H5). Pero el `selectPoints` de `scattergl` **empieza con una salida
temprana**:

```js
var m = hasMarkers(s), v = hasText(s), b = !m && !v;
if (s.visible !== true || b) return o;          // <- sale sin recorrer nada
for (var M = 0; M < u; M++)                      // 61 722 iteraciones si NO sale
    r.contains([l.xpx[M], l.ypx[M]], false, M, t) ? ... : ...
h.selectBatch[d] = p; h.unselectBatch[d] = k;    // + re-render del scene WebGL
```

La envolvente es `mode="lines+markers"` ([graph_timeseries.py:79](ui/components/graph_timeseries.py:79)),
así que `hasMarkers` es verdadero y el bucle corre entero: 61 722 tests punto-en-polígono,
más la construcción de un objeto `{pointNumber, x, y}` por punto encerrado, más el
re-render del scene — unas 10 veces por segundo (`SELECTDELAY: 100`).

**Pasar la envolvente a `mode="lines"` activa la salida temprana**: cero barrido, cero
asignaciones, cero re-render de selección. Y no toca el hover, que va por otra función
(`hoverPoints`, que usa `tree`/`ids` y no mira `hasMarkers`).

Es un cambio de un parámetro que **solo es viable junto con la Fase 2**: hoy los marcadores
son lo único que hace seleccionables los extremos del segmento. Al resolver el polígono
contra `(t, min, max)` en el servidor, dejan de hacer falta para el filtrado. Las dos
piezas se habilitan mutuamente; ninguna por separado sirve.

**Es un cambio visible** (desaparecen los puntos de 3 px en los extremos de cada segmento;
las líneas verticales de todas las señales siguen ahí, ninguna se pierde) y hay que
enseñárselo al usuario antes de darlo por bueno.

> **Corrección a una idea descartada.** Se propuso apagar el re-estilizado con
> `selected={"marker": {"opacity": ...}}`. Hace lo contrario. `Lib.makeSelectedPointStyleFns`
> crea `selectedOpacityFn` precisamente **cuando** `selected.marker.opacity` o
> `unselected.marker.opacity` están definidos, y `selectedPointStyle` recorre los nodos solo
> si existe al menos una de esas funciones (`i.length && e.each(...)`). Como el código de
> hoy no define ninguna, ese bucle **no se está ejecutando**; declararlas lo encendería.

## Fase 3 — Que el transporte deje de crecer, sin diezmar

Con la restricción de cero diezmado, el payload de la #1 crece linealmente con el dataset
y eso es un techo real: 12 484 señales son 37 452 puntos; `med_5_ago_2.hdf5` (247 060
señales, ya en disco) serían 741 180. Como no se puede reducir el número de puntos, se
reduce lo que ocupa cada uno.

**Esto ya no es hipótesis: se midió con el plotly 6.9.0 de este venv**, serializando una
envolvente sintética de 12 484 señales (37 452 puntos) con `plotly.io.to_json`:

| Variante | Bytes | vs. hoy |
|---|---:|---:|
| `float64` (lo que hay hoy) | 981 709 | — |
| **`float32`** | **545 661** | **−44 %** |
| `np.round(x, 3)` sobre `float64` | 981 144 | −0,06 % |
| listas de Python (no se usa) | 1 078 224 | +10 % |

La conclusión invierte lo que se había anotado aquí: **plotly.py ya serializa los arrays de
numpy en binario base64**, no como texto decimal. La salida contiene `{"dtype":"f8",
"bdata":"..."}`, no dígitos. Por eso:

- **`float32` sí reduce el volumen casi a la mitad** — es la anchura real del dato.
- **Redondear no reduce nada** (0,06 %): recortar decimales no cambia los 8 bytes que ocupa
  un `float64` en binario. El argumento de "el JSON envía texto y `float32` imprime
  `0.1000000014`" solo aplicaría a la ruta de listas de Python, que este código
  deliberadamente **no** usa: `build_vertical_segments` devuelve arrays de numpy justamente
  para evitarla (su docstring lo documenta: ~500 ms contra ~40 ms de validación).

La precisión sobra por varios órdenes de magnitud: con `float32`, un eje X de ~300 minutos
tiene resolución de ~2 ms, contra los ~0,25 min/píxel que dibuja la gráfica. **No afecta a
ningún cálculo**: `build_vertical_segments` produce datos de presentación, y las métricas
siguen leyendo `block.minmax` en `float64`.

Y encaja con la Fase 2: al resolver la selección contra los arrays originales del servidor,
la pérdida de precisión del transporte **no puede** contaminar qué señales se filtran. Sin
la Fase 2 este cambio sería más delicado, porque el filtrado dependería de coordenadas ya
degradadas.

Sobre la advertencia de `CLAUDE.md` (los arrays tipados en base64 rompen `customdata`,
porque el `filterEventData` de dash no sabe indexarlos): no aplica aquí, y ahora se sabe por
qué con certeza — **la envolvente ya viaja en base64 hoy** (`dtype: "f8"`) y el lazo
funciona, porque resuelve por posición (`pointNumber // ENTRIES_PER_SEGMENT`) y no por
`customdata`. Pasar de `f8` a `f4` no cambia nada estructural. Aun así hay que verificar que
el cambio no se cuele a los mapas #4/#5, que sí dependen de la resolución por posición.

## Fase 4 — La gráfica #3, aparte y explícita

`ui/components/graph_metric.py` usa `go.Scatter(mode="markers")` en régimen puntual: **un
nodo SVG `<path>` por señal**, 20 574 con AE, por cada métrica abierta. Eso es coste de
maquetación, pintado y reflow del navegador, y se paga en cada evento de hover de
*cualquier* gráfica de la página, no solo de la #3.

*(Corrección a una versión anterior de este plan: aquí se afirmaba que la selección sobre
traza SVG ejecuta `selectedPointStyle` con un `.each()` por nodo en cada tick del arrastre.
Es falso para este código. Ese bucle corre solo si existe alguna función de estilo de
selección, y `makeSelectedPointStyleFns` no crea ninguna mientras no se declaren
`selected.marker.*` / `unselected.marker.*` ni un `marker.opacity` de array — y
`graph_metric.py` no declara nada de eso. El problema de la #3 es el número de nodos, no el
re-estilizado.)*

No es lo que el usuario está reportando (lo reporta sin métricas abiertas), pero es un
problema real y peor que el de la #1 en cuanto se abre una métrica. Cambio: `Scattergl`
**por encima de un umbral de puntos** (p. ej. 5 000), `Scatter` por debajo.

El umbral no es adorno: Chrome limita los contextos WebGL por página (~16). Con #1, los
mapas #4/#5 y N gráficas #3 todas en `Scattergl`, un usuario con muchas métricas abiertas
puede provocar pérdida de contexto. En régimen de grupo una #3 tiene decenas de puntos y
no necesita WebGL. 
*(Nota técnica: React y Plotly.js en SPA a veces no liberan los contextos WebGL limpiamente al desmontar el componente, lo que agrava el riesgo de "context leak". Mantener las métricas en `Scatter` SVG cuando los puntos sean bajos previene este colapso sistemático).*
Hay que verificar además que `Scattergl` respeta el array de colores
por punto que usa `is_partial` y la `opacity` de traza del modo atenuado.

## Verificación

1. **El número que manda**: `Fx.hover` por debajo de **10 ms** de mediana en `/sensor/AE`
   con `med_5_ago_3.hdf5`, mismo protocolo que la tabla de §1.2, para que sea comparable
   con los 42,9 / 46,8 ms archivados.
2. **La prueba de uso, que es la que importa**: barrer el ratón por la #1 durante 10 s y
   que un botón del panel lateral responda al instante. Y un lazo completo sobre la #1 sin
   pegarse, con el filtrado aplicándose.
3. **Las dos funcionalidades intocables, a mano**: clicar en la #1 navega a la señal más
   cercana y la dibuja en la #2; el lazo sobre la #1 filtra y se propaga a #3 y a los
   mapas; el clic en el mapa 2D resalta en ambos mapas.
4. **Que ninguna señal desapareció**: contar los segmentos dibujados antes y después
   (`ctx["n_senales"]` de la etapa `render.grafica1` ya lo registra) y confirmar que es
   idéntico. Es la comprobación directa de la restricción de cero diezmado.
5. Suite y tipos:
   ```bash
   .venv/Scripts/python.exe -m pytest tests/ -q
   ```
   ```bash
   .venv/Scripts/python.exe -m mypy core data metrics cache ui viz utils
   ```
6. Sin regresión en el servidor:
   ```bash
   .venv/Scripts/python.exe -m benchmarks.run_benchmarks --dataset D:/data/data/main/med_5_ago_3.hdf5 --repeats 5
   ```
   Comparar contra `docs/benchmarks_med_5_ago_3.json`, leyendo antes §1.1 sobre
   variabilidad de máquina: solo cuentan las diferencias de orden de magnitud.

Tests a tocar: `tests/test_graph_timeseries.py`, `tests/test_filtering.py` (la resolución
por polígono es lógica pura y va cubierta con polígonos sintéticos, incluido el caso que
hoy falla: un lazo que cruza el centro de un segmento), `tests/test_graph_metric.py`,
`tests/test_sensor_window_callbacks.py`.

**Trampa conocida** (`CLAUDE.md`, `docs/ARQUITECTURA.md` §9): un payload de test escrito a
mano pasa aunque la app no funcione. Los tests de la Fase 2 deben construirse con la forma
real de `selectedData` — con `lassoPoints`/`range` — y **ninguna verificación de las Fases
1-4 se da por buena solo con tests verdes**: los puntos 1-4 de arriba son a mano, en el
navegador.

## Documentación

- `docs/RENDIMIENTO.md` §1.2: reescribir con los resultados de la Fase 0. Cada hipótesis
  descartada se anota como descartada, con su número — es lo que hace útil a esa sección.
  La pregunta abierta que dejó el ciclo anterior ("de dónde salen los ~30-55 ms") se cierra
  ahí o se dice explícitamente que sigue abierta.
- `README.md`: quitar la limitación del lazo sobre la #1 si la Fase 2 la elimina.
- `docs/ARQUITECTURA.md`: la resolución de selección por polígono en el servidor es una
  decisión estructural nueva, y el motivo por el que se conserva la ruta por `points`.
- `CLAUDE.md`: si la #1 pasa a resolver la selección por polígono, el invariante de
  `pointNumber // ENTRIES_PER_SEGMENT` deja de ser la única vía y hay que decirlo, porque
  hoy está escrito como si lo fuera.