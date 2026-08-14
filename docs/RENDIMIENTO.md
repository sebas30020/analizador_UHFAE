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

Con esa advertencia, hay un matiz que sí importa: en `Filtro + propagación` la **mediana**
cumple el umbral de 200 ms en ambos sensores, pero las muestras individuales más lentas lo
excedieron (202 ms en UHF, 220 ms en AE). Es decir: cumple de forma típica, pero no con
margen — es el indicador a vigilar si el dataset crece o si se apilan más gráficas tipo #3.

### 1.2 Lo que estos números no dicen

Todo se mide **del lado del servidor**: el tiempo termina cuando la figura está
serializada y lista para viajar. No incluye la latencia de red local ni el tiempo que
tarda el navegador en pintar la traza WebGL. Para la gráfica #1, con ~37 000 (UHF) y
~62 000 (AE) puntos y 1,9 / 2,5 MB de JSON por refresco, ese tramo no es despreciable y
**no está medido**: hace falta un cronometraje en navegador real para cerrar el número de
punta a punta.

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
| Carga perezosa por bloques | **Parcial.** `data/storage.py` escribe por bloques, pero `data/ingest.py` arma la matriz completa en RAM antes de persistir. Con el dataset real (~800 MB AE) cabe de sobra; con datasets varias veces mayores habría que pasar a escritura incremental. Riesgo documentado desde la Fase 1. |
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
