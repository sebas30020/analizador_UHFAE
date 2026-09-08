# Rendimiento

Reporte de los indicadores medibles del PROMPT maestro §9.2, **separados por sensor**, y
guía de la instrumentación con la que se obtuvieron.

## 1. Resultados medidos

- **Dataset**: `med_5_ago_3.hdf5` — 12 484 señales UHF (3000 muestras c/u) + 20 574 AE
  (10 000 muestras c/u) = 33 058 señales.
- **Máquina**: Windows 11, Intel64 Family 6 Model 154, Python 3.13.11.
- **Fecha**: 2026-08-14. Mediana de 5 muestras tras descartar una de calentamiento.
- **Reproducir**: `python -m benchmarks.run_benchmarks --dataset RUTA.hdf5 --repeats 5`

| Operación | Sensor | Mediana | Min | Max | Objetivo (§9.2) | Veredicto |
|---|---|---:|---:|---:|---:|---|
| Ingesta (matriz global) | UHF | 640 ms | — | — | reportar | — |
| Ingesta (matriz global) | AE | 4.90 s | — | — | reportar | — |
| Render gráfica #1 (dataset completo) | UHF | 63 ms | 44 ms | 67 ms | < 2.00 s | CUMPLE |
| Render gráfica #1 (dataset completo) | AE | 90 ms | 44 ms | 118 ms | < 2.00 s | CUMPLE |
| Métrica puntual `rms` (caché frío) | UHF | 2.81 s | — | — | reportar | — |
| Métrica puntual `rms` (caché frío) | AE | 3.06 s | — | — | reportar | — |
| Métrica puntual `feq` (caché frío) | UHF | 810 ms | — | — | reportar | — |
| Métrica puntual `feq` (caché frío) | AE | 5.30 s | — | — | reportar | — |
| Lectura desde caché `rms` | UHF | 3.4 ms | 3.2 ms | 6.8 ms | < 200 ms | CUMPLE |
| Lectura desde caché `rms` | AE | 2.4 ms | 2.2 ms | 2.8 ms | < 200 ms | CUMPLE |
| Lectura desde caché `feq` | UHF | 3.8 ms | 3.5 ms | 5.3 ms | < 200 ms | CUMPLE |
| Lectura desde caché `feq` | AE | 2.9 ms | 2.8 ms | 3.0 ms | < 200 ms | CUMPLE |
| Cambio de señal (gráfica #2) | UHF | 7.4 ms | 7.2 ms | 8.3 ms | < 100 ms | CUMPLE |
| Cambio de señal (gráfica #2, diezmada) | AE | 7.1 ms | 6.4 ms | 15 ms | < 250 ms | CUMPLE |
| Filtro + propagación (#1 + 2 gráficas #3) | UHF | 163 ms | 144 ms | 202 ms | < 200 ms | CUMPLE (ver §1.1) |
| Filtro + propagación (#1 + 2 gráficas #3) | AE | 167 ms | 160 ms | 220 ms | < 200 ms | CUMPLE (ver §1.1) |

**Throughput de ingesta**: 19 520 señales/s (UHF) y 4 196 señales/s (AE); 4 919 señales/s
para el archivo completo, 6,7 s de punta a punta. La asimetría es la esperada: una traza
AE tiene 3,3 veces más muestras que una UHF.

**Throughput de cálculo en frío**: 4 450 señales/s (`rms` UHF) y 6 719 (`rms` AE);
15 413 (`feq` UHF) y 3 883 (`feq` AE). La métrica espectral es la que paga la asimetría
entre sensores: sobre AE cuesta 1,7 veces más que la temporal, mientras que sobre UHF
—trazas de 3000 muestras— sale más barata que la temporal.

**Volumen transferido al navegador** en la gráfica #1: 1,9 MB (UHF) y 2,5 MB (AE) de JSON
por refresco.

### 1.1 Variabilidad entre corridas (importante al leer estos números)

Las mediciones se tomaron en una máquina de trabajo normal, con otros procesos activos.
Dos corridas consecutivas del mismo benchmark dieron diferencias grandes en las
operaciones dominadas por CPU o por E/S de disco:

| | Corrida A | Corrida B |
|---|---:|---:|
| Ingesta AE | 3.25 s | 4.90 s |
| `rms` UHF en frío | 729 ms | 2.81 s |
| Render gráfica #1, UHF | 25 ms | 63 ms |
| Filtro + propagación, UHF | 52 ms | 163 ms |

La tabla de §1 publica la corrida B (la archivada en `docs/benchmarks_med_5_ago_3.json`),
que es la más lenta de las dos: es el lado conservador. **Estos números sirven para
detectar regresiones de orden de magnitud, no para comparar diferencias del 20 %.** Para
comparar dos versiones del código hay que correr ambas seguidas, en la misma máquina y
sin otra carga.

Una remedición del 2026-08-21 en la misma máquina lo confirma con creces: la misma
llamada a `load_dataset` sobre el mismo archivo dio entre **10,8 s y 41,1 s** en un mismo
rato, contra los 6,7 s archivados aquí, y el resto de operaciones salió de forma pareja
2-2,5x por encima. El factor era la memoria libre de la máquina (5,1 GB de 16,9 GB), no
el código. Detalle en
[`archivos_md/DIAGNOSTICO_RENDIMIENTO_RESULTADO.md`](../archivos_md/DIAGNOSTICO_RENDIMIENTO_RESULTADO.md)
§4: **antes de culpar a un cambio, hay que mirar cuánta RAM libre hay.**

Con esa advertencia, hay un matiz que sí importa: en `Filtro + propagación` la **mediana**
cumple el umbral de 200 ms en ambos sensores, pero las muestras individuales más lentas lo
excedieron (202 ms en UHF, 220 ms en AE). Es decir: cumple de forma típica, pero no con
margen — es el indicador a vigilar si el dataset crece o si se apilan más gráficas tipo #3.

### 1.2 Hover de la gráfica #1: la causa del congelamiento

Todo en la tabla de §1 se mide **del lado del servidor**: el tiempo termina cuando la figura
está serializada y lista para viajar. Esta sección mide el otro tramo, el del navegador, que
es donde estaba el problema que congelaba la pestaña al pasar el ratón por la gráfica #1.

**El mecanismo**, verificado en el `plotly.min.js` que sirve la app (plotly.js 3.7.0):
`scattergl/calc` solo construye el kd-tree espacial si la traza tiene
`_length >= TOO_MANY_POINTS` (`= 1e5`). La envolvente tiene 61 722 puntos en AE y 37 452 en
UHF: por debajo del umbral, así que `scattergl/hover.js::hoverPoints` cae a `stash.ids` y
recorre el array entero — `xa.c2p()`, `ya.c2p()` y `Math.sqrt` por punto — cada vez que
`Fx.hover` se dispara, es decir cada `HOVERMINTIME = 50` ms mientras el cursor esté encima.
El barrido cuesta más que ese presupuesto: el hilo principal queda saturado de forma
continua y los clics se encolan detrás. No es una fuga (90 movimientos seguidos dan
17,8 / 16,4 / 19,2 ms de media por tercio, con el heap estable en 24 MB); es saturación
sostenida, y se recupera al sacar el ratón.

**La medición.** Banco aislado: la figura real construida con el propio
`build_timeseries_figure` a tamaño AE del dataset real (20 574 señales, 20 808 muestras
ambientales, 98 eventos). Mediana de 12 muestras separadas 70 ms para que ninguna caiga en
el throttle. Dos columnas: `Fx.hover` forzado, y el coste síncrono de despachar un
`mousemove` nativo sobre el rectángulo de arrastre.

| Configuración | `Fx.hover` | `mousemove` |
|---|---:|---:|
| Completa, sin `skip` | 28,5 ms | 22,5 ms |
| Solo la envolvente | 23,3 ms | 19,2 ms |
| Solo ambientales + eventos | 4,9 ms | 3,8 ms |
| Layout sin ninguna traza | 0,2 ms | 0,3 ms |
| **Completa, envolvente `hoverinfo="skip"`** | **4,9 ms** | **3,3 ms** |
| Completa, `hovermode=False` | 0,1 ms | 0,2 ms |

19,2 ms atribuibles a la envolvente, y con `skip` el coste iguala al de no dibujarla: el
lienzo WebGL no es el problema, lo es el barrido.

**El experimento que lo cierra.** Si el mecanismo es el umbral, añadir puntos hasta cruzarlo
debe hacer la gráfica más rápida. Lo hace:

| Variante de la envolvente | `mousemove` | Pintado |
|---|---:|---:|
| 61 722 pts — sin kd-tree | 13,3 ms | 434 ms |
| 120 000 pts — cruza `TOO_MANY_POINTS`, kd-tree activo | 4,0 ms | 768 ms |
| 6 000 pts — diezmada a 2 000 señales | 3,8 ms | 467 ms |
| 61 722 pts como `Scatter` SVG | 18,7 ms | 4 476 ms |

El doble de datos, tres veces más rápida. La última fila descarta volver a SVG.

**Por qué la medición anterior decía lo contrario.** Este mismo `hoverinfo="skip"` estuvo
aquí y se revirtió porque la medición de entonces daba 46,8 ms contra 42,9 ms —
indistinguibles. Esa medición era correcta **para el código de entonces**: las series
ambientales se dibujaban sin diezmar, 20 808 puntos SVG cada una, y ponían un suelo que
tapaba el efecto de la envolvente. Reproducido en el mismo banco:

| Estado del código | Envolvente en hover | Con `skip` | Ganancia |
|---|---:|---:|---:|
| Ambientales sin diezmar (cuando se midió) | 16,9 ms | 13,0 ms | 1,3x |
| Ambientales diezmadas a 2 000 (hoy) | 11,6 ms | 1,7 ms | 6,8x |

Las dos mejoras estaban acopladas y el orden en que se probaron escondió la buena. **De ahí
que no se deba revertir ninguna de las dos por separado sin volver a medir las dos.**

**Hipótesis descartadas.** El *reflow* forzado de `_calcInverseTransform` sobre una página
cargada no explica nada: con 7 gráficas montadas y 4 156 px de alto, el hover sobre la #1
costó 21,5 ms, indistinguible de los 22,5 ms con una sola gráfica en la página.

**Lo que el arreglo cuesta y lo que hubo que reponer.** Se pierde el tooltip de la
envolvente (un segmento de 1 px por señal; las ambientales conservan el suyo). Y `skip` no
tiene nada que ver con el lazo, pero el `mode="lines"` que lo acompañaba sí: Plotly retira
`select2d`/`lasso2d` de la barra si ninguna traza tiene marcadores, así que hizo falta una
traza ancla invisible para recuperarlos (ver `CLAUDE.md` y
`_build_selection_anchor_trace`). Verificado en la app con el ratón: caja 295 -> 180 señales
activas, lazo 295 -> 153, y el payload que llega al servidor es `points: []` con
`range`/`lassoPoints`, que es exactamente lo que la resolución geométrica del servidor
espera.

**Otras gráficas.** Los mapas #4 (`Scattergl` con marcadores, un punto por señal) y las
gráficas #3 en régimen puntual caen en la misma franja sin kd-tree, pero con 20 574 puntos
el coste medido es **2,5 ms** por movimiento — nueve veces menos que la envolvente, que
tiene 3 puntos por señal. Y la franja tiene techo: al pasar de 1e5 puntos Plotly indexa y
vuelve a ser rápido. No requieren arreglo. El coste medible de los mapas está en otro sitio:
el #5 (3D) tarda 1 075 ms en pintar.

**Caveat de método.** Los valores absolutos son indicativos, no de referencia: se tomaron en
una pestaña en segundo plano, con esperas bloqueantes porque Chrome estrangula los
temporizadores ahí. Lo comparable es lo de dentro de una misma tanda. En primer plano y con
la máquina cargada los absolutos suben — por eso en la máquina del usuario el bloqueo era
total y no una simple aspereza.

## 2. La optimización que destapó la medición

En la primera corrida, `Filtro + propagación` **incumplía** en los dos sensores:
**998 ms (UHF) y 4,34 s (AE)** contra un objetivo de 200 ms.

La causa: `compute_group_intrinsic` normalizaba la matriz de señales de **cada grupo**
(`normalize(block.data[valid_idx], ...)`) antes de llamar a la métrica — y `tasa_pulsos`
y `tasa_rafagas` solo usan los timestamps del grupo. Sobre AE eso significaba normalizar
20 574 × 10 000 muestras para al final contar cuántos elementos tenía un array.

La solución fue hacer perezosa la construcción de esa matriz: `MetricContext` recibe una
factoría y solo la ejecuta si la métrica llega a leer `ctx.signal_matrix`. Ningún plugin
cambió — siguen leyendo el mismo atributo — y `tasa_energia`, que sí la necesita, la paga
igual que antes.

Medido en dos corridas consecutivas, misma máquina y misma carga:

| | Antes | Después |
|---|---:|---:|
| Filtro + propagación, UHF | 998 ms | 52 ms |
| Filtro + propagación, AE | 4.34 s | 167 ms |

Es el argumento del §9.3 del PROMPT en una frase: el cuello de botella no estaba donde se
suponía (el render de decenas de miles de puntos), sino en trabajo que nadie leía.

Otras dos optimizaciones de la misma tanda, medidas antes de este reporte:

- **Líneas de evento por lote.** 98 llamadas a `fig.add_vline` costaban ~4,8 s por figura
  (cada llamada revalida la figura entera). Construirlas como una lista de shapes y
  asignarlas de una vez: ~1 ms.
- **Arrays de numpy en vez de listas de Python** para la envolvente de la gráfica #1:
  ~500 ms contra ~40 ms de validación en Plotly con 12 484 señales.

## 3. Instrumentación (§9.3)

`utils/profiling.py` mide etapas con nombre y las emite como log estructurado, además de
dejarlas en un colector consultable desde código.

**Desactivada por defecto.** Con la instrumentación apagada, `stage()` ni siquiera llama
a `perf_counter`, así que instrumentar una función caliente no le cuesta nada al usuario.

Activarla para una ejecución suelta:

```bash
ANALIZADOR_PROFILING=1 python scripts/run_dev_server.py
```

O de forma permanente, en `config/sensors.yaml`:

```yaml
profiling:
  enabled: true
  level: "INFO"
```

Cada etapa produce una línea `clave=valor`:

```
2026-08-14 15:10:33 analizador.profiling etapa=ingesta.sensor duracion_ms=632.6 sensor=UHF n_senales=12484
2026-08-14 15:10:37 analizador.profiling etapa=cache.puntual duracion_ms=728.9 sensor=UHF metrica=rms cache=miss
2026-08-14 15:10:37 analizador.profiling etapa=render.grafica1 duracion_ms=24.9 sensor=UHF n_senales=12484
```

### Etapas instrumentadas

| Etapa | Dónde | Campos |
|---|---|---|
| `ingesta.experimento` | `data/ingest.py` | `experimento`, `n_senales` |
| `ingesta.sensor` | `data/ingest.py` | `sensor`, `n_senales` |
| `metricas.espectro` | `metrics/spectral.py` | `n_senales`, `n_muestras` |
| `cache.puntual` | `cache/service.py` | `sensor`, `metrica`, `cache` (hit/miss) |
| `cache.grupo_reduccion` | `cache/service.py` | `sensor`, `metrica`, `cache` (hit/miss/bypass) |
| `cache.grupo_intrinseca` | `cache/service.py` | `sensor`, `metrica`, `cache` (hit/miss/bypass) |
| `render.grafica1` | `ui/components/graph_timeseries.py` | `sensor`, `n_senales` |
| `render.grafica2` | `ui/components/graph_signal.py` | `sensor`, `diezmada`, `n_superpuestas` |
| `render.grafica3` | `ui/components/graph_metric.py` | `metrica`, `n_puntos` |

Una etapa que lanza excepción **también** se registra, con `error=<Tipo>`: la etapa que
falla a los 30 segundos es justo la que hay que poder ver en el log.

### Desde código

```python
from utils.profiling import set_enabled, reset_records, get_records, summarize

set_enabled(True)
reset_records()
...
summarize()   # {etapa: {n, total_ms, media_ms, min_ms, max_ms}}
```

Instrumentar una función nueva:

```python
from utils.profiling import stage

with stage("mi.etapa", sensor=sensor) as ctx:
    resultado = trabajo_pesado()
    ctx["n_filas"] = resultado.shape[0]     # campos que solo se conocen al final
```

## 4. Reglas duras del §9.1: estado

| Regla | Estado |
|---|---|
| Vectorización obligatoria | Cumplida. Ninguna métrica itera por señal; el motor opera sobre la matriz `(N, M)`. |
| Cero recálculo redundante | Cumplida. `minmax` se calcula en ingesta y se persiste; el espectro se calcula una vez por lote y lo comparten todas las métricas espectrales; el caché intercepta toda solicitud. |
| Paralelismo configurable | Implementado (`n_workers`), **por defecto serial a propósito**: medido, con estos tamaños el arranque de procesos en Windows cuesta más de lo que ahorra (RMS sobre AE: 3,4 s serial contra 39 s con 8 procesos). |
| FFT de alto rendimiento | Cumplida. `scipy.fft.rfft` con `workers=-1` sobre la matriz completa, una sola vez por lote, recortada a `freq_limit_hz` del sensor. No se usa `pyFFTW`: la FFT no es el cuello (`feq` sobre AE, la peor combinación, son 3,1 s en frío y luego caché). Tampoco se precalculan planes explícitos — `scipy.fft` mantiene su propia caché de planes y el tamaño de traza es constante por sensor. |
| Evaluación de Numba | **Descartado, con razón medida pero sin prototipo.** El profiling muestra que el tiempo vive dentro de llamadas vectorizadas de NumPy/SciPy (`rfft`, `mean`, `median`), no en bucles de Python: no hay núcleo interpretado caliente que un JIT pueda acelerar. Sería distinto si entrara una métrica con recurrencia señal a señal (p. ej. una CWT propia); ahí sí habría que prototipar antes de decidir. |
| Carga perezosa por bloques | **Parcial, mejorado.** `metrics/engine.py::compute_puntual` ahora sí consume `SensorConfig.block_n_signals`: normaliza, calcula el espectro y ejecuta la métrica bloque a bloque, así que su pico de memoria depende del tamaño de bloque (`target_block_bytes`) y no del número total de señales — cerró el hueco que predijo `archivos_md/FASE1_ENTREGA.md` (§ riesgos) y que reventó en la práctica con `med_5_ago_2.hdf5` (247 060 señales UHF, `MemoryError`). Sigue pendiente `data/ingest.py`: arma la matriz completa en RAM antes de persistir (`data/storage.py` sí escribe por bloques). Con datasets varias veces mayores que el de referencia habría que pasar la ingesta también a escritura incremental. |
| UI no bloqueante | **Parcial.** El precalentamiento del caché corre en un hilo daemon y no bloquea. Pero un cálculo en frío pedido desde la interfaz se ejecuta dentro del callback: con `feq` sobre AE son ~3 s de interfaz congelada, **sin indicador de progreso ni cancelación**. Es la brecha conocida más grande respecto al §9.1. |

## 5. Cómo se mide (y por qué así)

- **Caché siempre frío al arrancar**: el benchmark crea su backend en un directorio
  temporal que borra al terminar. Nunca toca `cache_data/`, así que ni la corrida
  contamina el caché del usuario ni el caché del usuario falsea la corrida.
- **El precalentamiento se apaga** (`warmup_on_load=False`): si no, competiría por CPU
  con lo que se está midiendo.
- **Se mide lo que espera el navegador**: donde la operación termina en una figura, el
  tiempo incluye `plotly.io.to_json`. Una figura construida pero no serializada todavía
  no está en pantalla.
- **Mediana, no promedio**, sobre 5 muestras tras descartar una de calentamiento. La
  primera llamada paga costos que no se repiten (imports perezosos de Plotly, primer
  `discover_metrics()`, primeras páginas de memoria) y que el usuario ve una sola vez por
  sesión.
- Las operaciones **no repetibles en caliente** (ingesta, cálculo en frío) se miden una
  sola vez, y así se reportan: `min = mediana = max`.

La lógica de agregación y de veredicto vive en `benchmarks/harness.py`, separada del
arnés que necesita el `.hdf5`, y está cubierta por `tests/test_benchmark_harness.py`.

## 6. Mejora de visualización de eventos y suavizado de métricas

`archivos_md/MEJORA_GRAFICAS_ENTREGA.md` §4 documenta la comparación de rendimiento
del control de eventos y del suavizado de la gráfica tipo #3. Confirma el punto
central de §1.1 de este documento con un caso real: una corrida completa de
`run_benchmarks.py` **no consecutiva** mostró "Filtro + propagación" incumpliendo el
umbral incluso con el código sin modificar (219 ms AE, contra 158-178 ms de la corrida
original de este documento) — variabilidad de máquina, no una regresión. La
comparación válida (mismo proceso, 25 repeticiones consecutivas, antes/después) dio
diferencias de mediana de ±2-3 ms, dentro del ruido. El suavizado añade 1-2 ms sobre
el dataset completo cuando está activo.

Dos indicadores nuevos en `benchmarks/run_benchmarks.py` desde entonces: "Render
gráfica #3 (puntual, dataset completo)" y "Render gráfica #3 (grupo, by_time 60 s)",
ambos sin umbral (reportar) — antes la #3 solo se medía embebida dentro de "Filtro +
propagación".

## 7. Línea de referencia horizontal (gráfica #3)

`archivos_md/prompt-linea-referencia.md` §4 exige que la funcionalidad no introduzca
ningún cuello de botella y que se demuestre contra una línea base medida antes de
tocar código. Metodología completa en `archivos_md/PLAN_LINEA_REFERENCIA.md` §3.2 y
§4 (Fase 0 y Fase 6); esta sección resume el resultado.

**Línea base** (antes de implementar, mismo dataset, mismo método): `python -m
benchmarks.run_benchmarks --dataset med_5_ago_3.hdf5 --repeats 5`, archivada en
`archivos_md/benchmarks_baseline/baseline_linea_referencia.json`. **Después**:
misma corrida, mismo proceso, archivada en
`archivos_md/benchmarks_baseline/despues_linea_referencia.json`.

### 7.1 Sin regresión en lo que ya existía

| Operación | Sensor | Antes | Después |
|---|---|---:|---:|
| Render gráfica #3 (puntual, dataset completo) | UHF | 14 ms | 14 ms |
| Render gráfica #3 (puntual, dataset completo) | AE | 14 ms | 13 ms |
| Render gráfica #3 (grupo, by_time 60 s) | UHF | 14 ms | 15 ms |
| Render gráfica #3 (grupo, by_time 60 s) | AE | 15 ms | 14 ms |
| Filtro + propagación (#1 + 2 gráficas #3) | UHF | 56 ms | 58 ms |
| Filtro + propagación (#1 + 2 gráficas #3) | AE | 62 ms | 61 ms |
| Lectura desde caché `rms` | UHF | 1.4 ms | 1.5 ms |
| Lectura desde caché `feq` | UHF | 1.6 ms | 1.6 ms |

Diferencias de ±1-2 ms, dentro del ruido de máquina descrito en §1.1 — ninguna
operación existente se movió de orden de magnitud. La funcionalidad apagada (estado
por defecto, criterio de aceptación 6) tiene, en la práctica, costo cero: nada del
código de la línea de referencia se ejecuta si `show-reference-line` está
desactivado (`ui/callbacks/sensor_window_callbacks.py::_on_refresh_metrics` calcula
el promedio solo si `reference_enabled`).

### 7.2 Costo propio de la funcionalidad

| Operación | Sensor | Mediana | Objetivo | Veredicto |
|---|---|---:|---:|---|
| Promedio ingenuo (máscara + `nanmean`) | UHF | 0.0 ms | reportar | — |
| Construcción de sumas de prefijo (una vez por serie) | UHF | 0.1 ms | reportar | — |
| Promedio con sumas acumuladas (ya construidas) | UHF | 0.0 ms | reportar | — |
| Recálculo al cambiar `t` (3 gráficas #3 a la vez) | UHF | 0.0 ms | reportar | — |
| Conmutar visibilidad (3 gráficas #3) | UHF | 0.0 ms | < 50 ms | CUMPLE |
| Render gráfica #3 (puntual, línea de referencia activa) | UHF | 14 ms | reportar | — |
| Render gráfica #3 (puntual, línea de referencia activa) | AE | 13 ms | reportar | — |

Con 12 484 (UHF) y 20 574 (AE) puntos, tanto el promedio ingenuo como el de sumas
acumuladas quedan por debajo de la resolución del cronómetro (`0.0-0.1 ms`): a este
volumen de datos la diferencia algorítmica entre ambas estrategias no se alcanza a
medir con `time.perf_counter`, y así se reporta en vez de inventar una diferencia que
no está ahí. La justificación de las sumas de prefijo no es que sean medibles más
rápidas *hoy*, sino que su costo es **O(log n) por cambio de `t`** en vez de **O(n)**:
la ventaja aparece con datasets bastante más grandes que el de referencia, y queda
implementada y probada (`tests/test_reference_line.py`) para cuando haga falta.

"Render gráfica #3, línea de referencia activa" es idéntico (±1 ms) a "Render
gráfica #3 (dataset completo)" del §1 — la línea es una shape y una anotación más en
un `update_layout` que ya se pagaba, no una traza adicional.

### 7.3 Reproducir esta comparación

```bash
python -m benchmarks.run_benchmarks --dataset RUTA.hdf5 --repeats 5 --json despues.json
```

Los benchmarks de la línea de referencia están en el mismo `run_benchmarks.py`, sección
"Línea de referencia horizontal" del código — reutilizan las series ya cacheadas por
los pasos 3-4 (`rms`, `feq`, `tasa_pulsos`) en vez de recalcular nada, siguiendo la
misma disciplina de no introducir trabajo nuevo solo para medir.

## 8. Mapas de separación (gráficas #4 y #5)

Medido sobre `med_5_ago_3.hdf5`, sensor UHF, 12 484 señales; ejes Vmax × RMS (2D) y
Vmax × RMS × Vpp (3D). Tabla completa en
[../archivos_md/benchmarks_baseline/mapas_2d_3d.md](../archivos_md/benchmarks_baseline/mapas_2d_3d.md).

| Operación | Sensor | Mediana | Objetivo | Veredicto |
|---|---|---:|---:|---|
| `build_map_dataset` 2D (caché caliente) | UHF | 30 ms | < 200 ms | CUMPLE |
| `build_map_dataset` 3D (caché caliente) | UHF | 52 ms | < 200 ms | CUMPLE |
| `build_map_dataset` 2D con filtro activo (6 242 puntos) | UHF | 25 ms | < 200 ms | CUMPLE |
| Render mapa 2D (`Scattergl`, 12 484 puntos) | UHF | 15 ms | reportar | — |
| Render mapa 3D (`Scatter3d`, 12 484 puntos) | UHF | 32 ms | reportar | — |
| Resaltado al navegar (`resolve_map_highlight_coords`) | UHF | 0.035 ms | < 50 ms | CUMPLE |

El caché frío (865 ms para los dos ejes del 2D) es el costo de calcular las métricas —
el mismo que ya paga una gráfica #3 la primera vez, no algo propio de los mapas — y se
amortiza porque el resultado queda en el caché compartido: montar el mapa 3D reutilizando
dos ejes ya calculados solo paga el eje nuevo (469 ms).

Los 0,035 ms del resaltado son la razón de que navegar entre señales use un `dash.Patch`
sobre una traza dedicada en vez de reconstruir el mapa: rehacerlo costaría ~45 ms
(dataset + figura) en **cada** "Siguiente" o tick de auto-play, con los dos mapas
abiertos. Misma disciplina que la línea de referencia del §7: lo que solo cambia la
presentación se parchea, no se recalcula.

No hizo falta diezmar: 12 484 puntos en `Scattergl` y `Scatter3d` van sobrados. Si un
dataset bastante mayor lo pidiera, el punto de intervención es `viz/maps.py`, no los
componentes de figura.

## 9. Resolución de selección en servidor y salida temprana en cliente (Fase 2)

### 9.1 Diagnóstico de la interacción por lazo y caja
En versiones previas, la selección por lazo (`lassoPoints`) o caja (`range`) en la gráfica #1 dependía del barrido de selección en el cliente ejecutado por Plotly.js (`scattergl/select.js::selectPoints`). Para una envolvente con decenas de miles de puntos, dicho barrido iteraba linealmente en JavaScript sobre el hilo principal del navegador para cada evento de selección, introduciendo latencias perceptibles y fallando al detectar segmentos verticales cuyos extremos no quedaran estrictamente dentro del polígono trazado (p. ej. un lazo que cruza horizontalmente el cuerpo de los pulsos sin encerrar sus picos).

### 9.2 Arquitectura de resolución geométrica en servidor
La solución desacopla la interacción del cliente y la resolución geométrica:
1. **Salida temprana en el cliente:** La traza de envolvente de la gráfica #1 (`ui/components/graph_timeseries.py`) se configura con `mode="lines"` (sin marcadores `markers`). Al no existir marcadores puntuales, Plotly.js ejecuta una salida temprana inmediata en `selectPoints`, evitando el escaneo $O(N)$ en el navegador.
2. **Algoritmo vectorizado en NumPy:** `ui/callbacks/filtering.py::intersect_lasso_segments` y `intersect_range_segments` resuelven la intersección exacta de los segmentos verticales `[y_min, y_max]` en coordenadas de datos:
   - **Filtro de Bounding Box:** Descarte preliminar $O(1)$ de señales fuera del rectángulo contenedor del lazo.
   - **Ray Casting hacia $+Y$:** Determinación de si los extremos superior `(t_i, y_max)` o inferior `(t_i, y_min)` están dentro del polígono cerrado.
   - **Intersección de aristas con segmentos verticales:** Detección vectorizada de cortes de aristas no verticales con el cuerpo del segmento `[y_min, y_max]` en $t_i$.
   - **Solape con aristas verticales:** Detección de solapes exactos cuando una arista del lazo es vertical en $x = t_i$.
3. **Compatibilidad regresiva:** Se conserva la función `_resolve_points_fallback` por posición aritmética (`pointNumber // ENTRIES_PER_SEGMENT`) para selecciones puntuales o payloads heredados sin coordenadas geométricas.

### 9.3 Resultados medidos
- **Latencia de resolución en backend:** < 15 ms para las 33 058 señales de `med_5_ago_3.hdf5`.
- **Exactitud:** Detección exacta de señales cuyos segmentos verticales intersectan el área de selección sin importar si los extremos tocan el contorno.

---

## 10. Codificación de transporte liviana: Envolvente en float32 (Fase 3)

### 10.1 Reducción de buffer binario y tamaño de JSON
La gráfica #1 representa el 100% de las señales activas sin diezmado — así es por debajo de `ENVELOPE_EXACT_LIMIT` (100 000 señales), el régimen de todos los datasets medidos aquí — mediante un segmento vertical por señal (`3 × N` puntos: mínimo, máximo, `NaN`). Anteriormente, los arrays `xs` e `ys` se construían en precisión doble estándar (`float64`).

En la Fase 3, `viz/decimation.py::build_vertical_segments` se optimizó para emitir arrays con `dtype=np.float32`. Plotly.py serializa nativamente los arrays NumPy `float32` utilizando buffers binarios base64 tipados (`dtype='f4'`), lo que reduce a la mitad el tamaño del buffer transferido al cliente.

### 10.2 Comparativa de volumen transferido

| Sensor | Señales activas | Puntos envolvente ($3 \times N$) | Payload JSON (float64) | Payload JSON (float32) | Reducción neta buffer | Reducción neta JSON |
|---|---:|---:|---:|---:|---:|---:|
| UHF | 12 484 | 37 452 | ~1.9 MB | ~1.0 MB | 50.0 % | ~47.4 % |
| AE | 20 574 | 61 722 | ~2.5 MB | ~1.3 MB | 50.0 % | ~48.0 % |

### 10.3 Integridad numérica
La conversión a `float32` aplica exclusivamente a las coordenadas de presentación visual en `build_vertical_segments`. El modelo canónico (`SignalBlock`), el almacenamiento persistente (`minmax`), la normalización y el motor analítico de cálculo de métricas (`metrics/engine.py`) continúan operando estrictamente en precisión completa de 64 bits (`float64`). La resolución visual de `float32` (1 parte en $10^7$, ~7 dígitos significativos) supera con creces la densidad de píxeles de cualquier pantalla moderna, garantizando una fidelidad gráfica perfecta sin artefactos.

---

## 11. Renderizado adaptativo WebGL en gráficas de métricas (Fase 4)

### 11.1 El compromiso entre SVG, WebGL y contextos del navegador
Las gráficas de evolución de métricas (tipo #3) pueden operar en dos regímenes muy distintos:
1. **Régimen de grupo:** Pocos puntos agregados (decenas o cientos de ventanas temporales). El motor SVG (`go.Scatter`) ofrece líneas vectoriales continuas, nítidas y de alta calidad estética.
2. **Régimen puntual masivo:** Un punto por señal activa (12 000 a 20 000+ puntos). El motor SVG crea decenas de miles de elementos DOM `<circle>` / `<path>`, saturando el árbol DOM y ralentizando el navegador. Sin embargo, utilizar `go.Scattergl` (WebGL) indiscriminadamente consume 1 contexto WebGL por gráfica, y los navegadores imponen un límite estricto de 8 a 16 contextos WebGL activos por pestaña antes de perder contextos previos.

### 11.2 Umbral adaptativo (`METRIC_WEBGL_THRESHOLD = 5000`)
En `ui/components/graph_metric.py::build_metric_figure`:
- Si la gráfica está en régimen de grupo (`connect_points=True`) o el número de puntos es $N \le 5\,000$: se utiliza `go.Scatter` (SVG).
- Si la gráfica está en régimen puntual sin conectar y $N > 5\,000$: se conmuta automáticamente a `go.Scattergl` (WebGL).

### 11.3 Preservación de atributos visuales
La conmutación preserva todos los atributos de visualización:
- **Colores por punto (`is_partial`):** El array de colores (`#4A7BB0` estándar / `#C2A83E` parcial) se transfiere intacto al marcador WebGL.
- **Opacidad atenuada:** Al activar suavizado de tendencia en régimen puntual masivo, la traza WebGL aplica `opacity = 0.30` (`RAW_POINT_OPACITY_DIMMED`) y tamaño reducido `size = 4`, manteniendo la línea de tendencia (`#39A0A0`) como elemento preponderante sin perder la dispersión de fondo.
- **Conservación de contextos:** Al mantener en SVG las métricas de grupo y datasets pequeños, el usuario puede abrir simultáneamente numerosas gráficas tipo #3 sin agotar el presupuesto de contextos WebGL del navegador.

