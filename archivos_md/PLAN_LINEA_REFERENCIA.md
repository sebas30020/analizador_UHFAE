# Plan de implementación — Línea horizontal de referencia en las gráficas #3

Plan derivado de `archivos_md/prompt-linea-referencia.md`. Documento de trabajo previo a
la implementación: recoge el reconocimiento del §0, fija las decisiones de diseño y
ordena el trabajo en fases verificables.

---

## 1. Reconocimiento previo (§0) — hallazgos

| Pregunta del §0 | Respuesta encontrada en el código |
|---|---|
| Stack y librería de gráficas | Dash + Plotly `graph_objects`. Figura construida en `ui/components/`, callbacks en `ui/callbacks/sensor_window_callbacks.py`. |
| Módulo de la gráfica #3 | `ui/components/graph_metric.py::build_metric_figure`. |
| Cuántas hay y cómo se instancian | **Una gráfica independiente por métrica seleccionada**, apiladas en `metrics-graphs-container`, sin tope. Id de patrón `{"type": "graph-metric", "index": "<regimen>:<metric_id>"}`. No es una figura con subplots. |
| Estructura de datos por métrica | `(timestamps: (N,) float64 UNIX, values: (N,))` devueltos por las fachadas de `cache/service.py`. En régimen de grupo se suma `is_partial: (N,) bool`. |
| Unidad del eje temporal | El eje X **ya está en minutos transcurridos**: `ui/components/time_axis.py::to_elapsed_minutes(ts, t0)`. El `t` del enunciado (minutos) es directamente comparable con ese eje, sin conversión intermedia. |
| **Definición de "inicio del experimento"** | Existe y es explícita: `ui/state.py::compute_t0` = timestamp mínimo entre **todas** las señales de **todos** los sensores, la serie ambiental y los eventos. Es el mismo cero que ya comparten las gráficas #1 y #3. **Se adopta tal cual**, no se introduce una referencia nueva. |
| ¿Series ordenadas por tiempo? | Sí, garantizado aguas arriba: `data/ingest.py` ordena explícitamente por timestamp y `core/grouping.py` preserva el orden. `viz/smoothing.py` ya declara esa precondición y no reordena. Habilita `searchsorted` sin verificación adicional. |
| Layout de la GUI y patrón de control | `ui/components/control_panel.py`, bloque `H4("Opciones de visualización")`. Patrón: `dcc.Checklist`/`dcc.Input` → `Input` del callback (reflejo inmediato, sin botón "Aplicar") → callback aparte que deshabilita los controles dependientes. |
| Arquitectura y convenciones | Flujo unidireccional datos → procesamiento → caché → presentación. La capa de presentación **no calcula**. Precedentes directos a imitar: `viz/smoothing.py` (módulo puro, sin Dash ni Plotly) y `ui/components/event_lines.py` (constructor de shapes de layout). |
| Controles de visualización existentes | `show-events`, `smooth-puntual`, `smooth-grupo`, `smoothing-method`, `smoothing-window`, `gap-threshold`. Los nuevos se integran en ese mismo bloque. |

### 1.1 El precedente que resuelve el requisito de rendimiento

`_on_toggle_events` (en `sensor_window_callbacks.py`) ya implementa exactamente el patrón
que el §4.1 exige: las shapes se precalculan una vez por dataset en el Store
`event-shapes`, y conmutar el control solo aplica un `dash.Patch` sobre `layout.shapes`
— sin leer `AppState`, sin tocar el caché, sin reconstruir trazas.

**La línea de referencia se implementa sobre ese mismo mecanismo.** Es la diferencia
entre un cambio de `t` que cuesta microsegundos y uno que reentra en
`_on_refresh_metrics` y paga de nuevo lectura de caché + construcción de figura +
serialización JSON para las N gráficas.

### 1.2 Conflicto detectado que hay que resolver sí o sí

`_on_toggle_events` asigna la lista de shapes **entera**:

```python
p["layout"]["shapes"] = visible_shapes
```

Si la línea de referencia se añade como otra shape, conmutar "Mostrar eventos" la
borraría, y viceversa. **`layout.shapes` necesita un único dueño.** Se refactoriza
`_on_toggle_events` en un callback de decoraciones que compone
`shapes_eventos + shapes_referencia` y es el único que escribe esa propiedad.

---

## 2. Decisiones de diseño ante ambigüedades del enunciado

Las dos ambigüedades de esta sección se consultaron antes de implementar, como pide el
§0 del enunciado. Ambas quedaron **confirmadas** en el sentido descrito abajo.

### 2.1 Fuente de datos en régimen de grupo (§2.1, "independiente de la agrupación")

El enunciado pide que el promedio sea independiente de la agrupación. **Para las métricas
intrínsecas de grupo eso es imposible**: `tasa_pulsos`, `tasa_energia` y `tasa_rafagas`
solo existen *por ventana* — no hay una serie sin agrupar de la que sacar la media, y
`T_w` (la duración declarada de la ventana) está en su propia definición.

**Decisión (confirmada):** el promedio se calcula sobre **la misma serie que la gráfica
dibuja**, tomada después del caché y **antes** del suavizado y del corte por huecos.

- Se cumple el objetivo real del requisito: cambiar suavizado, método, ventana de
  suavizado o umbral de huecos **nunca** mueve la línea (criterio de aceptación 8).
- Cambiar la ventana de agrupamiento sí la mueve en gráficas de régimen de grupo, porque
  la magnitud graficada *es otra* — una media de tasas por ventana de 60 s y una de
  ventana de 600 s no son el mismo número, y la línea debe seguir siendo comparable con
  los puntos que tiene encima.
- Una línea de referencia en unidades distintas de las del eje Y de su propia gráfica
  sería incorrecta, no conservadora.

### 2.2 Interacción con el filtrado

El enunciado no lo menciona. **Decisión (confirmada):** la línea respeta la máscara de
filtrado activa, igual que los puntos.

Razón: el filtrado es una exclusión a nivel de dato (el usuario declara "estas señales no
cuentan"), no una opción de presentación. Una referencia calculada sobre señales que el
usuario acaba de excluir no sería comparable con lo que ve en pantalla. Además, en
régimen de grupo el resultado filtrado ni siquiera está en el caché (bypass total,
`docs/ARQUITECTURA.md` §5), así que la serie que llega a la gráfica ya viene filtrada.

### 2.3 Convenciones que se documentarán en la nota de entrega

- **Límite superior inclusivo:** se incluyen las muestras con `x <= t` (`searchsorted`
  con `side="right"`).
- **Límite inferior:** `t0` de `compute_t0`. Como `t0` es el mínimo global entre sensores,
  ambiental y eventos, la primera señal de un sensor puede caer en `x > 0`; el intervalo
  arranca igual en el cero del eje, sin recorte.
- **Nulos y NaN:** excluidos del promedio mediante conteo de finitos separado del
  acumulado de valores — nunca tratados como cero. Es el mismo criterio que ya aplica
  `viz/smoothing.py::_rolling_mean_time`.
- **Sin muestras finitas en el intervalo:** no se dibuja línea (`None`), no se dibuja un
  cero.

---

## 3. Arquitectura de la solución

```
viz/reference_line.py            módulo PURO (sin Dash, sin Plotly)
  build_prefix_sums(x, y)        -> PrefixSums: cumsum de valores + cumcount de finitos
  mean_until(prefix, t)          -> float | None   (searchsorted + 2 lecturas indexadas)

ui/components/reference_line.py  constructor de shape + anotación
  build_reference_shape(value)       -> dict  (xref="paper", layer="below")
  build_reference_annotation(...)    -> dict  (valor numérico, formato :.6g)

ui/components/graph_metric.py    build_metric_figure(..., reference_value=None)
                                 dibuja la línea en el render inicial

ui/callbacks/sensor_window_callbacks.py
  registro de PrefixSums por gráfica (memo de proceso, acotado)
  callback ÚNICO dueño de layout.shapes + layout.annotations
```

### 3.1 Representación gráfica: shape de layout, no traza

Se dibuja como **shape** (`type="line"`, `xref="paper"`, `x0=0`, `x1=1`, `yref="y"`,
`y0=y1=promedio`, `layer="below"`, `dash="dot"`, `opacity≈0.5`), más una **anotación**
con el valor numérico.

Motivos, todos verificables contra el código actual:

- `xref="paper"` da "a lo ancho de todo el lienzo" de forma exacta y **estable frente a
  zoom y paneo** — una traza de dos puntos con extremos fijos se quedaría corta al hacer
  paneo lateral.
- `layer="below"` cumple "por detrás de la serie principal" sin depender del orden de
  inserción de trazas.
- `yref="y"` la ancla al valor real del eje Y.
- La figura tiene `showlegend=False`, así que **la leyenda no es una opción**: el valor
  numérico va en la anotación (criterio de aceptación 5).
- Como shape de layout, es parcheable con `dash.Patch` sin tocar ninguna traza.

**Color:** se reutiliza un tono ya definido en el módulo, sin introducir uno nuevo
(§2.3). Candidatos: `TREND_COLOR` / `PARTIAL_COLOR`, ya presentes en `graph_metric.py`;
la elección final se fija al ver las tres capas juntas en pantalla, priorizando que la
línea no compita con la tendencia suavizada.

### 3.2 Rendimiento: dónde está el cuello de botella real

El costo de promediar no es el problema — una media sobre un prefijo de ≤20 000 puntos es
una operación vectorizada de microsegundos. **El riesgo real es que cambiar `t` reentre
en `_on_refresh_metrics`**, que por cada gráfica paga lectura de caché (objetivo < 200 ms)
y, en régimen de grupo, **recálculo completo con bypass de caché**.

Por eso:

- `t` y el selector de visibilidad **no son `Input` de `_on_refresh_metrics`**. Alimentan
  un callback aparte que solo parchea `layout.shapes` / `layout.annotations`.
- Las sumas de prefijo se construyen **de forma perezosa**, la primera vez que la
  funcionalidad se usa para una serie dada, y se memorizan en un registro de proceso
  acotado, con la misma clave que ya identifica la serie (dataset, sensor, opción de
  métrica, versión de filtrado, especificación de agrupamiento).
- El registro guarda **referencias** a los arrays que ya están vivos en memoria, no
  copias — coste adicional nulo mientras la funcionalidad está apagada, que es el estado
  por defecto (§4.1, "costo nulo cuando está apagada").
- El registro se acota por tamaño y se purga al cambiar de dataset. Es una precaución
  deliberada: el repo ya arrastra un episodio de agotamiento de memoria (commit
  `bd5814d`), y retener arrays de datasets viejos lo reproduciría.
- El `dcc.Input` de `t` usa `debounce=True` (§4.1, antirrebote).

**Sobre las sumas acumuladas.** El §4.1 pide *evaluar* precalcularlas. Se implementan,
pero la Fase 6 mide explícitamente la alternativa ingenua (`np.nanmean` sobre el prefijo)
frente a la acumulada. Si con el volumen real la diferencia es irrelevante, se dice con el
número medido en vez de justificar la complejidad por defecto.

---

## 4. Fases de trabajo

### Fase 0 — Línea base medida (antes de tocar código)

Correr `python -m benchmarks.run_benchmarks --dataset RUTA.hdf5 --repeats 5 --json ...`
sobre el dataset real y guardar el resultado. Sin esta medición previa, el §4.2.5
("comparación contra la línea base") no es verificable.

### Fase 1 — `viz/reference_line.py` (módulo puro) + tests

`build_prefix_sums`, `mean_until`. Sin Dash, sin Plotly, sin tocar `metrics/` ni
`cache/`. Tests: promedio correcto contra `np.nanmean` independiente, inclusividad de `t`,
NaN excluidos, `t <= 0`, `t` mayor que la duración, serie vacía, una sola muestra.

### Fase 2 — `ui/components/reference_line.py` + integración en `build_metric_figure`

Constructores de shape y anotación; parámetro `reference_value: float | None` en
`build_metric_figure`. Tests: shape presente/ausente, `layer="below"`, `xref="paper"`,
valor en la anotación, y **que la línea no cambia al variar `smoothing`** (criterio 8).

### Fase 3 — Registro de sumas de prefijo (memo de proceso acotado)

Construcción perezosa, clave completa, purga por dataset, cota de tamaño. Tests del
comportamiento de la clave y de la cota.

### Fase 4 — Controles en la GUI

En `control_panel.py`, bloque "Opciones de visualización":
`Mostrar línea de referencia` (checklist, **apagado** por defecto) y
`Definir intervalo de referencia (min)` (`dcc.Input` numérico, `min>0`, `debounce=True`),
con `title` explicativo como el resto de los controles. Valor por defecto a fijar
observando la duración típica de los datasets del proyecto.

### Fase 5 — Callbacks

1. Refactor de `_on_toggle_events` → callback único dueño de `layout.shapes` y
   `layout.annotations`, que compone eventos + referencia.
2. Callback que recalcula los promedios al cambiar `t`, la visibilidad o la serie
   subyacente, y parchea. **No entra al caché ni al motor de métricas.**
3. Callback que deshabilita el campo de intervalo cuando la visibilidad está apagada
   (mismo patrón que `_on_refresh_smoothing_controls_disabled`).
4. `_on_refresh_metrics` pasa `reference_value` en el render inicial.

Tests en `tests/test_sensor_window_callbacks.py`, que ya cubre este archivo.

### Fase 6 — Benchmarks del §4.2

Las seis mediciones pedidas, en `benchmarks/run_benchmarks.py` con `BenchmarkResult` y
`format_markdown_table`; tabla antes/después en `docs/RENDIMIENTO.md`.

### Fase 7 — Auditoría de texto (§3), nota de entrega y verificación

Revisión del texto visible contra las convenciones del §3 (registro formal, infinitivos,
sin filtraciones de la documentación interna), corrigiendo lo preexistente que incumpla.
Nota `archivos_md/LINEA_REFERENCIA_ENTREGA.md` con el formato de
`MEJORA_GRAFICAS_ENTREGA.md`. Cierre: `pytest tests/ -q` y
`mypy core data metrics cache ui viz utils`.

---

## 5. Trazabilidad con los criterios de aceptación

| Criterio | Dónde se cumple | Dónde se verifica |
|---|---|---|
| 1. Una línea por métrica con sus propios datos | Fase 5, promedio por `option_value` | Fase 5 |
| 2. Valor = promedio entre inicio y `t` | Fase 1, `mean_until` | Fase 1 (contra `np.nanmean` independiente) |
| 3. Un `t` global actualiza todas a la vez | Fase 4 + 5, un solo control → parche a todas | Fase 5 |
| 4. Punteada, transparente, todo el lienzo, por detrás | Fase 2, shape `xref="paper"` + `layer="below"` | Fase 2 |
| 5. Valor numérico consultable | Fase 2, anotación (`showlegend=False` descarta la leyenda) | Fase 2 |
| 6. Selector, apagado por defecto | Fase 4 | Fase 5 |
| 7. Conmutar no recalcula ni altera zoom | Fase 5, `dash.Patch` + `uirevision` intacto | Fase 5 + Fase 6 (medición 3) |
| 8. No cambia con agrupación/suavizado | §2.1 + Fase 2 (pre-suavizado) | Fase 2 |
| 9. Casos borde | Fase 1 + Fase 5 | Fase 1 |
| 10. Texto conforme al §3 | Fase 4 + Fase 7 | Fase 7 |
| 11. Benchmarks sin degradación | Fase 6 | Fase 0 vs Fase 6 |
| 12. Respeta estructura y convenciones | Transversal (§3 de este plan) | `mypy` + `pytest` |

---

## 6. Invariantes del repo que este trabajo NO puede romper

Del `CLAUDE.md` y de `docs/ARQUITECTURA.md`, las que este cambio roza de cerca:

- **La capa de presentación no contiene lógica de cálculo.** El cálculo del promedio va en
  `viz/`, no en `ui/`. `ui/components/reference_line.py` solo construye shapes.
- **No se toca `cache/keys.py`.** La línea de referencia no es una métrica: no altera
  ningún valor calculado ni cacheado, así que `t` **no entra** en la clave de caché. El
  memo de sumas de prefijo es una estructura de presentación, separada del caché de
  métricas.
- **El caché sigue almacenando el conjunto completo de señales.** Nada de esta
  funcionalidad escribe en él.
- **Sin bucles señal por señal.** `searchsorted` + lecturas indexadas, vectorizado.
- **`T_w` de las métricas intrínsecas sigue siendo la duración declarada.** Esta
  funcionalidad no la toca.
- **Los callbacks se registran una sola vez** y resuelven el sensor leyendo el Store
  `page-sensor`.
- Documentación, comentarios y mensajes de commit **en español**.
