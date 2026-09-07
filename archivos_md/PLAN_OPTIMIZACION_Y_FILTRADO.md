# Auditoría de rendimiento y plan de optimización + filtrado por métricas

## Paso 0 — Entrega del documento

**Primera acción de la implementación:** copiar este documento a
`archivos_md/PLAN_OPTIMIZACION_Y_FILTRADO.md`, siguiendo la convención del repo
(`PLAN_MAPAS_2D_3D.md`, `PLAN_LECTURA_KEYSIGHT.md`, `PLAN_LINEA_REFERENCIA.md`). Versionarlo
ahí es lo que lo hace ejecutable por otra sesión: fuera del repo no lo encuentra nadie.

## Arranque para quien lo ejecute

Leer antes de tocar código: `CLAUDE.md` (invariantes), `docs/ARQUITECTURA.md` (capas),
`docs/RENDIMIENTO.md` §1.2 y §9 (qué se midió y qué se revirtió, para no repetir errores).

**Reglas de ejecución, no negociables:**

- **La Etapa 0 va primero, siempre.** Esta máquina da variaciones de hasta 3× entre corridas
  del *mismo código sin modificar*. Sin línea base archivada no se puede afirmar ninguna mejora,
  y el repo ya revirtió una optimización correcta por medirla mal (§B, "Aviso de método").
- **B3 no se separa de la Etapa 2.** El memo de proceso del régimen de grupo es dependencia
  dura del filtrado por métricas: entregar la Etapa 2 sin él degrada el rendimiento en cuanto
  se usa la funcionalidad (§E.8).
- **La Etapa 5 va en rama aparte**, con la suite verde y la línea base archivada detrás. Toca
  `SignalBlock`, que toca todo el repo.
- **Multiproceso está prohibido** (§C invariante 2, §F.5). Está medido: 39 s contra 3,4 s.
- Cada etapa cierra con `pytest tests/ -q` y `mypy core data metrics cache ui viz utils` en
  verde. Documentación, comentarios y commits **en español**.

**Alcance por sesión sugerido:** Etapa 0+1 · Etapa 2 (incluido B3) · Etapa 3+4 · Etapa 5 · Etapa 6.

## Contexto

La aplicación funciona y su documentación está al día, pero se han declarado tres objetivos
que la arquitectura actual no sostiene:

1. **Escalar a 250k–1M+ señales.** Hoy `data/ingest.py` ensambla la matriz `(N,M)` completa en
   RAM y la retiene toda la sesión. Con `med_5_ago_3.hdf5` (33 058 señales) el pico medido es
   ~2 GB; con `med_5_ago_2.hdf5` (247 060 señales UHF) ya hubo un `MemoryError` real. A 1M
   señales AE la matriz sola son 40 GB. **No es una optimización, es un límite duro.**
2. **Máximo rendimiento.** El diagnóstico confirma que el cómputo ya está donde debe (backend),
   pero corre **dentro de los callbacks de Dash**, así que cada operación cara congela la
   interfaz: 11–41 s al cargar un dataset, 5,3 s una `feq` fría sobre AE, 26 s una `kurtosis`.
3. **Filtrado por métricas** (métrica + `>=`/`<=` + umbral, múltiples condiciones en AND), que
   hoy no existe: el único filtrado es geométrico por lazo/caja.

Decisiones tomadas con el usuario: escala objetivo **250k–1M+**; el filtro por métricas se
**compone en AND** con el filtrado geométrico; catálogo **solo de métricas puntuales**; el
cálculo en frío corre **en segundo plano con indicador**, no bloqueando la interfaz.

---

# A. Diagnóstico de la arquitectura actual

## A.1 Flujo real de la información

```
archivo .hdf5
  └─ open_reader()                    data/readers/factory.py:35   (sniffing por contenido)
  └─ ingest_experiment()              data/ingest.py:107           ← TODO A RAM, pico 2×
       └─ concat + argsort + data[order] + compute_minmax
  └─ AppState.load_dataset()          ui/state.py:156              ← DENTRO del callback Dash
       └─ LoadedDataset.blocks                                      ← (N,M) float32 viva toda la sesión
       └─ start_background_warmup()   ui/state.py:248               ← 1 hilo, 9 métricas × sensor, serie

interacción del usuario
  └─ callback Dash (proceso único, hilo del request)
       └─ cache/service.get_or_compute_*()                          ← SQLite + HDF5 gzip
            └─ metrics/engine.compute_*()                           ← numpy/scipy, serial
       └─ viz/ + ui/components/ → figura Plotly completa
  └─ HTTP /_dash-update-component → navegador                       ← 1,0–3,3 MB por refresco
       └─ Plotly.js: pintado, hover, zoom, pan
```

## A.2 Qué se calcula en el backend — **todo**

Verificado exhaustivamente: **cero `clientside_callback`, cero archivos `assets/*.js`, cero
JavaScript propio en el repo.** La única coincidencia de `clientside` es histórica, en
`archivos_md/FASE5_ENTREGA.md`, sobre una funcionalidad eliminada en la Fase 6.

Todas las métricas, FFTs, normalización, agrupamiento, resolución geométrica del lazo,
diezmado y suavizado corren en Python en el proceso Dash. La resolución del lazo incluso se
movió deliberadamente al servidor (`ui/callbacks/filtering.py:28`, ray casting vectorizado).

## A.3 Qué se calcula en el frontend — **solo pintado**

El navegador ejecuta únicamente Plotly.js: layout, pintado, hover, zoom y pan. Es trabajo
irreducible de presentación, no procesamiento de señales.

> **Conclusión que corrige la premisa de la pregunta:** la separación backend/frontend que
> se pedía **ya existe y es correcta**. El problema real no es *dónde* se calcula, sino
> *cuándo y en qué hilo*: el backend calcula **sincrónicamente dentro del callback HTTP**, así
> que "backend pesado" y "GUI congelada" son hoy el mismo evento. Eso es lo que hay que
> romper, no la separación de capas.

Único cómputo pesado que sí hace el navegador y no debería: el mapa 3D pinta 12 484 puntos en
**1 075 ms** (`docs/RENDIMIENTO.md`), y a 1M puntos es inviable.

## A.4 Auditoría por etapa

| Etapa | Dónde corre | Coste medido | Cuello de botella |
|---|---|---|---|
| Lectura HDF5 | callback Dash | incluido abajo | `keysight_reader.py:216`: bucle Python **por señal** con `int16→float64→float32` |
| Ingesta | callback Dash, **síncrono** | 640 ms UHF / 4,9 s AE archivado; **10,8–41,1 s** medido bajo presión de RAM | `ingest.py:78` `data[order]` = copia completa `(N,M)` → pico 2× |
| Cálculo métricas | callback Dash, serial | `rms` 2,8/3,1 s frío; `feq` 0,8/5,3 s; `kurtosis` AE **26 s** | serial; `spectral.py:38-42` calcula el espectro entero y descarta 90–97% |
| Caché | SQLite + HDF5 gzip, lock global | lectura 2,4–3,4 ms | `backend.py:123` abre/cierra el `.h5` en **cada** lectura; el `.h5` nunca se compacta |
| Filtrado | servidor, vectorizado | < 15 ms para 33 058 señales | correcto; **pero** dispara bypass total de caché en régimen de grupo |
| Transporte | HTTP sin compresión | #1: 1,0 MB UHF / 1,3 MB AE; #3: ~440 KB/métrica; mapas 0,7 MB | sin gzip; #3 en float64 |
| Visualización | Plotly.js | #1 63–90 ms; mapa 3D **1 075 ms** | sin diezmado en #1, #3 ni mapas |

---

# B. Cuellos de botella, priorizados

Criterio: **impacto × frecuencia ÷ dificultad**. Los números vienen de `docs/RENDIMIENTO.md` y
`archivos_md/DIAGNOSTICO_RENDIMIENTO_RESULTADO.md`; los marcados *(estimado)* no están medidos.

| # | Cuello de botella | Evidencia | Impacto | Dificultad | Prioridad |
|---|---|---|---|---|---|
| B1 | **Cómputo síncrono dentro del callback**: carga 11–41 s, `feq` AE 5,3 s, `kurtosis` 26 s, sin progreso ni cancelación | `RESULTADO.md:129-146`; `RENDIMIENTO.md:276` lo declara brecha abierta | Congela la interfaz por completo | Media | **Crítica** |
| B2 | **Matriz `(N,M)` completa en RAM + copia por `data[order]`** | pico ~2 GB con 33 k señales; `MemoryError` con 247 k | Techo duro de escala | Alta | **Crítica** |
| B3 | **Bypass total de caché en régimen de grupo con filtro activo**: recalcula desde la matriz cruda en *cada* refresco | `cache/service.py:106-112`, `:158-161` | Hoy ocasional; **con filtros por métrica pasa a ser el caso normal** | Baja | **Crítica** |
| B4 | **Sin diezmado en #1, #3 y mapas**: 3N puntos, 1 punto/señal, 1 punto/señal | `viz/decimation.py:12-14`; `RENDIMIENTO.md:410` | A 1M señales: 12 MB de payload y pintado inviable | Media | **Crítica** (a escala) |
| B5 | **Sin compresión HTTP**: 1,0–3,3 MB de JSON por interacción | `Dash(compress=)` no se usa | Latencia percibida en cada refresco | **Muy baja** | **Alta** |
| B6 | `State("graph-timeseries","figure")` sube **1,0–1,3 MB navegador→servidor** para leer un `name` | `sensor_window_callbacks.py:712` | Cada toggle de eventos / línea de referencia | **Muy baja** | **Alta** |
| B7 | **4 `dcc.Input` numéricos sin `debounce`** disparan `_on_refresh_metrics` completo por tecla | `control_panel.py:127,142,181,186` | Reconstruye todas las gráficas #3 por pulsación | **Muy baja** | **Alta** |
| B8 | **Warmup serial en 1 hilo**: 62,6 s con 12 núcleos disponibles | `RESULTADO.md:148-156` | Ventana larga de caché frío compitiendo por CPU | Baja | Alta |
| B9 | `compute_spectrum` calcula el espectro completo y **descarta 90–97%** tras 3 temporales `(N,F)` | `spectral.py:38-42` vs `sensors.yaml:11-13` | `feq` es la métrica puntual más cara | Baja | Alta |
| B10 | `resolve_groups_by_time` es **O(G·N)** y se recalcula en cada bypass sin cachearse | `grouping.py:62-64`; `service.py:108,160` | Superlineal en nº de grupos | Baja | Media |
| B11 | `_on_refresh_signal` escucha `relayoutData` → figura completa **en cada zoom/pan** | `sensor_window_callbacks.py:790` | Auto-play a 6 Hz = 6 figuras/s | Baja | Media |
| B12 | `_on_refresh_metrics` reconstruye **todo el contenedor** (destruye y recrea cada `dcc.Graph`) ante cualquiera de 12 Inputs | `sensor_window_callbacks.py:836` | Impide `Patch`; repinta todo por un cambio de suavizado | Media | Media |
| B13 | Bucles Python **señal a señal** en `rise_time.py:31`, `zcr.py:35` (y `f_aprox` lo hereda), `keysight_reader.py:216` | violan §9.1 del propio repo | O(N) iteraciones de intérprete | Media | Media |
| B14 | Temporales `(N,M)` evitables: `teq.py:20-25` (~5, una en float64), `shannon_entropy.py:37-50` (~7, dos en int64) | | Presión de RAM y ancho de banda de memoria | Baja | Media |
| B15 | Reparseo repetido: `load_sensor_configs` dentro de los generadores de lotes, `discover_metrics()` en cada `list_metrics()`, `time_axis()` sin memoizar | `hdf5_reader.py:78`, `keysight_reader.py:209`, `registry.py:180`, `models.py:65` | Micro, pero gratis de arreglar | **Muy baja** | Baja |
| B16 | Caché en disco: abre/cierra el `.h5` por operación; `purge_orphaned` en Python; el `.h5` **nunca se compacta** | `backend.py:123,155,174-195` | Crecimiento monótono del disco | Baja | Baja |

### Refutado — no volver a intentarlo

Medido y descartado en este repo; el plan **no** lo revisita:

- **Multiproceso en `compute_puntual`**: `loky` con 8 workers dio **39 s** frente a **3,4 s**
  serial sobre AE (coste de `spawn` en Windows) — `metrics/engine.py:99-110`.
- **Numba**: el tiempo vive dentro de llamadas vectorizadas, no en bucles Python.
- **Volver a SVG en la envolvente**: 4 476 ms de pintado.
- **Bajar el gzip del payload de caché**: el lock global del caché no es cuello de botella
  (9,2 ms mediana en reposo vs 8,1 ms con warmup activo — indistinguible).

> **Aviso de método:** la misma máquina dio corridas A/B del **código sin modificar** con
> variaciones de hasta 3× (`RENDIMIENTO.md:45-62`). Nada por debajo de ~2× es señal aquí.
> Toda medición del plan debe ser **A/B consecutiva en el mismo proceso**, como
> `MEJORA_GRAFICAS_ENTREGA.md §4`.

---

# C. Arquitectura objetivo

```
┌─ Proceso ÚNICO (obligatorio: AppState/registros son singletons de proceso con ~1 GB) ─┐
│                                                                                        │
│  Servidor WSGI (waitress, N hilos)                                                     │
│      │                                                                                 │
│      ├─ Callbacks Dash  ── SOLO: leer estado, formatear, construir figura, devolver     │
│      │                     Nunca cómputo pesado. Nunca I/O de archivo grande.          │
│      │                                                                                 │
│      └─ ui/jobs.py  ── ThreadPoolExecutor + registro de trabajos + cancelación          │
│             │           (hilos, NO procesos: numpy/scipy sueltan el GIL y el estado     │
│             │            compartido de ~1 GB no se puede duplicar)                      │
│             ├─ carga de dataset      ├─ evaluación de filtros por métrica               │
│             ├─ cálculo de métrica    ├─ warmup    ├─ exportación    ├─ fusión           │
│                                                                                        │
│  AppState (fuente única de verdad)                                                     │
│      ├─ metadatos (N,) en RAM: timestamps, vrange, valid_mask, minmax  ← ~25 B/señal   │
│      ├─ máscara efectiva = manual AND condiciones de métrica                            │
│      └─ handle perezoso a la matriz (N,M) EN DISCO ────────────────────────────────┐   │
└────────────────────────────────────────────────────────────────────────────────────│───┘
                                                                                     ▼
                                                            archivo canónico HDF5 chunked
                                                            (data/storage.py, hoy sin usar)
```

Cuatro invariantes nuevos que gobiernan todo el plan:

1. **Ningún callback calcula.** Un callback lee estado ya materializado, formatea y devuelve.
   Lo que tarde más de ~50 ms va al servicio de trabajos.
2. **Un solo proceso, muchos hilos.** Multiproceso está prohibido por dos razones medidas:
   `AppState` es un singleton de proceso con ~1 GB, y `loky` en Windows fue 11× más lento.
   Esto **descarta gunicorn multi-worker de forma definitiva**.
3. **La matriz `(N,M)` no vive en RAM.** Solo los metadatos `(N,)`. Todo consumidor de la
   matriz la pide por rangos de filas.
4. **Lo que se dibuja se diezma; lo que se filtra, no.** La selección y el filtrado operan
   siempre sobre los arrays completos en el servidor, así que diezmar el dibujo **no altera
   ningún resultado** — es lo que hace viable la escala.

---

# D. Plan de implementación

## Etapa 0 — Línea base medible *(prerequisito, no negociable)*

Sin esto no se puede distinguir una mejora del ruido de ±3× de esta máquina.

- Generador de datasets sintéticos en `benchmarks/`: 50k / 250k / 1M señales, escribiendo con
  `data/storage.py::write_canonical` (que ya existe y produce el formato correcto).
- Etapas de profiling nuevas en `utils/profiling.py`: `carga.dataset` (hoy no existe ninguna
  para `load_dataset` completo), `filtro.metricas`, `job.<tipo>`.
- Reejecutar `python -m benchmarks.run_benchmarks --repeats 5` y archivar como
  `docs/benchmarks_baseline_2026.json`.
- Añadir a `benchmarks/run_benchmarks.py` dos escenarios: **RSS máximo durante la ingesta** y
  **bytes serializados por figura**.

## Etapa 1 — Servicio de trabajos en segundo plano *(Crítica — desbloquea todo lo demás)*

Generaliza el patrón que el repo **ya usa dos veces**: hilo daemon + estado en `AppState` +
`dcc.Interval` de sondeo (`sensor_window_callbacks.py:130-157` exportación, `:176-185` fusión).

**Nuevo `ui/jobs.py`:**

```python
@dataclass(frozen=True)
class JobStatus:
    job_id: str; kind: str; label: str
    state: Literal["pendiente","en_curso","ok","error","cancelado"]
    progress: float | None; message: str; result_version: int | None

class JobService:                       # singleton de proceso, como AppState
    def submit(self, kind, label, fn, *, cancel_token=None) -> str
    def status(self, job_id) -> JobStatus
    def active(self) -> list[JobStatus]
    def cancel(self, job_id) -> None
```

- `ThreadPoolExecutor(max_workers=2)`: hilos, no procesos (invariante C.2). numpy/scipy sueltan
  el GIL, así que un cálculo de fondo **sí** progresa mientras la interfaz responde.
- Cancelación cooperativa reutilizando el `threading.Event` que `cache/warmup.py` ya acepta.
  Limitación honesta y ya documentada: una llamada vectorizada de numpy no es interrumpible, la
  cota real de cancelación es la métrica en curso (`kurtosis` AE ≈ 26 s).
- Componente `ui/components/job_status_bar.py` + un único `dcc.Interval` global de sondeo
  (700 ms, el mismo periodo que los dos Intervals que reemplaza).

**Migrar a él, en este orden:** `load_dataset` (B1, el peor) → warmup → cálculo de métrica en
frío → exportación y fusión (unificar los dos ad-hoc existentes).

`AppState.load_dataset` se parte en `begin_load(path, partition) -> job_id` y la publicación
del `LoadedDataset` al terminar, subiendo `dataset_version`. El callback del botón solo lanza
el trabajo y devuelve.

## Etapa 2 — Filtrado por métricas *(Crítica — es el requisito funcional)*

Detalle completo en la sección **E**. Depende de la Etapa 1 para la evaluación en segundo plano.

## Etapa 3 — Optimizaciones de bajo riesgo *(Alta, gran relación beneficio/coste)*

Todas independientes entre sí; cada una es un commit medible por separado.

| Cambio | Archivo | Ataca |
|---|---|---|
| **Memo de proceso para régimen de grupo**, clave `(cache_key_canónica, filter_version)`, LRU acotada | `cache/service.py` | **B3** |
| `resolve_groups_by_time` con `np.searchsorted` sobre `unique_bins` (O(N) en vez de O(G·N)) + memo por `(dataset_id, sensor, modo, valor)` | `core/grouping.py` | B10 |
| **Truncar el espectro ANTES de la magnitud**: `k = np.searchsorted(freqs, freq_limit_hz, "right")`, luego `fft[..., :k]` y `re²+im²` en vez de `abs()**2` | `metrics/spectral.py:37-42` | **B9** — elimina 2 temporales `(N,F)` y ~90–97% del trabajo posterior a la FFT |
| Warmup paralelo **entre** métricas con `ThreadPoolExecutor` (distinto del multiproceso *dentro* de una métrica, que sí fracasó) | `cache/warmup.py` | B8 |
| `debounce=True` en `grouping-value`, `grouping-percentile-q`, `smoothing-window`, `gap-threshold` | `control_panel.py` | **B7** |
| Sustituir `State("graph-timeseries","figure")` por el índice de traza en un `dcc.Store` pequeño | `sensor_window_callbacks.py:712` | **B6** |
| `relayoutData` fuera de los Inputs de `_on_refresh_signal`, o gateado a cambios reales de rango | `sensor_window_callbacks.py:790` | B11 |
| Memoizar `load_sensor_configs`, `SensorConfig.time_axis()`, y cortocircuitar `discover_metrics()` en `list_metrics()` | `core/models.py`, `metrics/registry.py` | B15 |
| Vectorizar `rise_time` y `zcr` (`dead_zone` con `np.where` + `cumsum` por filas en vez de bucle) | `metrics/time_domain/` | B13 |
| `teq`: reutilizar `t_diff` y evitar la promoción a float64; `shannon_entropy`: `int32` en vez de `int64` para los bins | `metrics/time_domain/` | B14 |

## Etapa 4 — Transporte y payload *(Alta, muy barata)*

- **`Dash(compress=True)`** (requiere `dash[compress]` → `flask-compress`). El JSON de Plotly
  comprime muy bien; es la mejora de mayor relación beneficio/esfuerzo de todo el plan sobre
  B5. Añadir la dependencia con versión exacta a `requirements.txt`.
- Emitir `x`/`y` de las gráficas #3 en `float32` (el mismo argumento y la misma técnica que ya
  usa `viz/decimation.py:90-91` para la envolvente): ~440 KB → ~220 KB por métrica.
- `config=` explícito en cada `dcc.Graph` (hoy ninguno lo pasa) para recortar el modebar.
- `customdata` de los mapas a `int32` o eliminarlo: son ~100 KB int64 que **nunca vuelven al
  servidor** (bug conocido de typed arrays, `graph_map_common.py:26-32`).

## Etapa 5 — Escalabilidad fuera de núcleo *(Crítica para el objetivo, riesgo alto — va al final)*

Es el cambio estructural. El repo ya tiene la mitad escrita y sin usar.

**5a. Ingesta en streaming, sin la copia por permutación.**
`ingest_sensor` (`data/ingest.py:34-94`) deja de construir la matriz. Escribe cada lote a un
HDF5 canónico chunked conforme llega, y calcula `order = np.argsort(timestamps)` **solo sobre
el vector `(N,)`**, que se persiste como *índice de permutación*. El invariante "`ingest_sensor`
ordena explícitamente por timestamp" se preserva en la semántica: todo lector aplica la
permutación al leer. Elimina la copia `data[order]` de `ingest.py:78`, que es la mitad del pico
de RAM. `compute_minmax` pasa a acumularse por lote (ya es una reducción por filas).

**5b. `SignalBlock` como handle perezoso.**
Mantiene en RAM los `(N,)` — `timestamps`, `trigger`, `vrange`, `valid_mask`, `minmax`: ~25 B
por señal, **25 MB con 1M señales** — y expone `rows(start, stop) -> np.ndarray` respaldado por
`data/storage.py::CanonicalStore.get_block` (**ya escrito y probado**, hoy código muerto).
`core/models.py:90-91` ya lo documenta como si funcionara así; esto lo hace cierto.

**5c. Adaptar los tres consumidores de la matriz.**
- `metrics/engine.compute_puntual` **ya itera por rangos de filas** de `block_n_signals`
  (`engine.py:142-155`): solo cambia de dónde sale cada bloque. Es el cambio más pequeño y el
  que más rinde.
- `compute_group_*` piden el rango del grupo.
- Gráfica #2 pide una fila: `CanonicalStore.get_signal_row`, ya existe, O(1).

**5d. Política de diezmado por escala (presentación).**
El invariante actual "cero diezmado en la envolvente" tiene una justificación válida —
garantizar que toda señal activa está representada — pero a 1M señales produce 3M puntos.
Se sustituye por una regla explícita con umbral:

- Por debajo de `ENVELOPE_EXACT_LIMIT` (≈ 100 000 señales activas, el `TOO_MANY_POINTS` de
  Plotly): comportamiento actual, exacto, sin cambios.
- Por encima: `viz/decimation.bin_reduce_minmax` a ancho de píxel — **agregación exacta de
  min/max, no submuestreo**, así que ninguna señal desaparece de la envolvente visible.
- **Esto no afecta al filtrado**: `_on_selection_changed` resuelve la geometría contra los
  arrays completos de `timestamps`/`minmax` en el servidor
  (`sensor_window_callbacks.py:580-590`), no contra lo dibujado. El único camino que sí
  dependía del dibujo es `_resolve_points_fallback` (`filtering.py:121`), ya marcado como
  fallback legacy: debe desactivarse cuando la envolvente venga diezmada.
- Misma política para #3 (min/max por columna de píxel) y para los mapas (binning 2D por
  densidad por encima del umbral; el mapa 3D, que ya cuesta 1 075 ms con 12 k puntos, necesita
  un tope duro).

## Etapa 6 — Servidor de producción *(Media)*

Detalle en la sección **F**.

---

# E. Sistema de filtrado por métricas

Diseño validado. Dos capas en `AppState`, composición **eager**, evaluación en segundo plano.

## E.1 Modelo de datos — `core/metric_filters.py` (nuevo)

En `core/` por el mismo criterio que `core/grouping.py::Group`: es un concepto de dominio,
importable desde `ui/state.py` y desde los tests, sin arrastrar Dash, Plotly ni el caché.
Recibe arrays de valores **ya calculados**; nunca los pide.

```python
FilterOperator = Literal[">=", "<="]

@dataclass(frozen=True)                    # frozen ⇒ hashable ⇒ deduplicable
class MetricCondition:
    metric_id: str
    operator: FilterOperator
    threshold: float
    params: tuple[tuple[str, Any], ...] = ()      # () = defaults; ya previsto en cache/keys.py

def condition_key(c) -> str                       # "vpp|>=|0.1" — id estable del botón «×»
def evaluate_condition(values, c) -> np.ndarray   # comparación & np.isfinite(values) EXPLÍCITO
def scatter_to_full(partial, valid_idx, n_total) -> np.ndarray
def combine_masks(masks, n_total) -> np.ndarray   # AND; lista vacía ⇒ todo True
def detect_contradictions(conditions) -> list[...]# rango vacío, puro, sin tocar datos
def normalize_threshold(raw) -> float | None
```

`& np.isfinite(values)` es redundante con IEEE-754 (`nan >= x` ya es `False`), pero hace la
semántica **"sin valor ⇒ no pasa el filtro"** una decisión legible y testeable en vez de un
efecto colateral.

## E.2 Estado — `ui/state.py`

```python
self._manual_mask:   dict[SensorName, np.ndarray]                   # exclusiones geométricas
self._metric_filters:dict[SensorName, tuple[MetricCondition, ...]]  # declarativas
self._active_mask:   dict[SensorName, np.ndarray]                   # composición YA materializada
self._undo_stack / self._redo_stack: dict[SensorName, list[FilterSnapshot]]

@dataclass(frozen=True)
class FilterSnapshot:
    manual_mask: np.ndarray
    metric_filters: tuple[MetricCondition, ...]
    active_mask: np.ndarray        # ← guardar la compuesta hace undo/redo instantáneo e infalible
```

**`get_active_mask()` no cambia ni una línea** — sigue siendo la copia defensiva O(N) que ya
leen los seis consumidores (gráfica #1, gráficas #3, mapas, navegación, estado, exportación).
Ninguno se toca. El invariante de `CLAUDE.md` "una sola máscara de filtrado por sensor" queda
intacto.

**La composición es eager, en el método mutador, nunca perezosa dentro de `get_active_mask()`.**
Razón: `get_active_mask` toma el lock y lo llaman cuatro callbacks concurrentemente tras cada
cambio de filtro; con memoización perezosa los cuatro entrarían a la vez y **los cuatro
calcularían**, además de bloquear el proceso entero durante los segundos de una métrica fría.

Secuencia obligatoria del mutador:

```
1. bajo lock : leer dataset_id, _manual_mask, condiciones; construir la lista candidata; soltar
2. SIN lock  : evaluar (etapa cara → va al JobService de la Etapa 1)
3. bajo lock : verificar que dataset_id y len(mask) siguen siendo los mismos
               · cambió (otra pestaña cargó otro archivo) → descartar, no-op
               · igual → push del snapshot, publicar, subir _filter_version
```

El paso 3 es imprescindible: hoy `apply_filter` no lo necesita porque es O(N) bajo lock; con
una evaluación de segundos, sin él se publicaría una máscara de longitud equivocada.

**Métodos nuevos** (los existentes conservan firma y semántica observable):

```python
def add_metric_filter(sensor, condition) -> MetricFilterResult
def remove_metric_filter(sensor, key: str) -> MetricFilterResult
def clear_metric_filters(sensor) -> MetricFilterResult
def get_metric_filters(sensor) -> tuple[MetricCondition, ...]
```

`MetricFilterResult` (en vez de `int`) porque hace falta un **canal de error que no sea una
excepción**: una métrica desconocida no puede convertirse en un HTTP 500.

## E.3 Evaluación — función de módulo, no método

```python
def evaluate_metric_conditions(cache, block, sensor_config, dataset_id, conditions)
        -> tuple[np.ndarray, str | None]
```

- **Una llamada a `get_or_compute_puntual` por `(metric_id, params)` distinto**, no una por
  condición. El ejemplo del requisito — `Vpp>=0.1 AND Vpp<=0.3 AND feq>=300e6 AND feq<=350e6` —
  son **2 llamadas, no 4**.
- Siempre con **`active_mask=None`** → clave canónica del conjunto completo → **acierta el
  caché en disco siempre**, y el invariante "el caché nunca almacena subconjuntos filtrados"
  se respeta sin tocar `cache/service.py`.
- Mapeo a índices globales: `valid_idx = np.where(block.valid_mask)[0]` + `scatter_to_full`.
- **Exactamente 1 evaluación por acción del usuario.** Los repintados posteriores de #1, #3,
  #4 y #5 leen la máscara ya materializada: 0 llamadas adicionales.

Va en `ui/state.py` porque la evaluación necesita `self.cache` **y** `self._dataset` a la vez, y
`AppState` es el único dueño de ambos. Descartadas: `metrics/filtering.py` (invertiría la capa
procesamiento→caché y crearía un ciclo de paquetes) y `viz/` (produce una máscara, no un
dataset de gráfica). El cálculo real sigue en `metrics/engine.py` y el álgebra en
`core/metric_filters.py`; aquí solo queda orquestación.

**Cálculo en frío** (decisión del usuario): la evaluación se lanza vía `JobService`. La interfaz
muestra "calculando *Frecuencia equivalente*…" y el filtro se aplica al terminar. La secuencia
"evaluar fuera del lock, verificar y publicar bajo lock" **ya es exactamente la que un hilo de
fondo necesita**. Atenuante: el warmup ya precalienta `rms`, `vpp`, `crest_factor`, `kurtosis`,
`energia_relativa` y `feq` por sensor — justo las candidatas naturales a filtro — así que el
caso normal es una lectura de ~3 ms.

## E.4 Interfaz — `ui/components/control_panel.py`

Bloque nuevo **antes** de la línea 251 (`html.H4("Filtrado")`), para que el filtrado declarativo
quede sobre el geométrico y sus botones de deshacer/rehacer, que sirven a los dos.

```python
dcc.Dropdown(id="metric-filter-metric",   options=_puntual_metric_axis_options())  # ← reutilizado tal cual
dcc.Dropdown(id="metric-filter-operator", options=[{"label":"≥ mayor o igual que","value":">="},
                                                   {"label":"≤ menor o igual que","value":"<="}])
dcc.Input(id="metric-filter-value", type="number", debounce=True)
html.Button("Añadir filtro",  id="btn-add-metric-filter")
html.Button("Quitar todos",   id="btn-clear-metric-filters")
html.Div(id="metric-filter-message")
html.Div(id="metric-filter-list")     # una fila por condición + botón «×»
```

`_puntual_metric_axis_options()` (`control_panel.py:35-45`) se reutiliza **sin tocarla**: su
`value` ya es el `metric_id` desnudo y ya ofrece solo régimen puntual — el catálogo elegido.

Ids planos y globales para los controles fijos (convención del repo). Id de patrón solo para lo
dinámico, con la **clave estable de la condición** como `index`, no su posición:

```python
id={"type": "btn-remove-metric-filter", "index": condition_key(c)}
```

Así la eliminación es idempotente y no hay ventana para que un clic en vuelo borre el filtro
equivocado tras un repintado.

## E.5 Callbacks

**Nuevo `_on_metric_filter_action`** — añadir / quitar uno / quitar todos en un solo callback:

```python
Output("filter-version", "data", allow_duplicate=True)   # ← obligatorio: ya es Output de _on_filter_action
Output("metric-filter-message", "children")
Input("btn-add-metric-filter", "n_clicks")
Input("btn-clear-metric-filters", "n_clicks")
Input({"type":"btn-remove-metric-filter","index":ALL}, "n_clicks")
State("metric-filter-metric"/"metric-filter-operator"/"metric-filter-value"/"page-sensor")
```

`allow_duplicate=True` en vez de fusionar con `_on_filter_action`: fusionar cambiaría la clave
`"filter-version.data"` en `callback_map` y rompería `tests/test_sensor_window_callbacks.py:695`
sin ganar nada.

**Guardarraíl del disparo espurio de `ALL`:** cuando la lista se repinta y aparece un botón
nuevo, Dash dispara el callback con `n_clicks=None`. Sin guarda, añadir un filtro borraría otro
de inmediato. Es un modo de fallo que el repo aún no ha encontrado porque todos sus usos de
`ALL` son sobre `clickData`/`selectedData`/`id`, nunca sobre `n_clicks`:

```python
if isinstance(ctx.triggered_id, dict) and not ctx.triggered[0]["value"]:
    raise PreventUpdate
```

**`_on_refresh_filter_status` gana un Output** (`metric-filter-list.children`): ya tiene
exactamente los Inputs correctos (`filter-version`, `dataset-version`) y el State `page-sensor`.

**Nada más.** `filter-version` ya es Input de `_on_refresh_timeseries`, `_on_refresh_metrics` y
`_on_refresh_maps`: la propagación es gratis por el mecanismo que ya existe.

## E.6 Conjunto vacío — tres huecos reales

Con filtros por métrica, llegar a 0 señales activas pasa de caso raro (hay que rodear todo con
un lazo) a **resultado normal de teclear un umbral estricto**.

| Gráfica | Hoy | Cambio |
|---|---|---|
| #4/#5 mapas | `NO_POINTS_MESSAGE` ya se muestra (`graph_map_common.py:44`) | ninguno — es el patrón a replicar |
| #3 métricas | figura **muda** sin trazas ni mensaje (`graph_metric.py:106-109`) | anotación `EMPTY_ACTIVE_SET_MESSAGE` centrada cuando `n_points == 0` |
| #1 serie temporal | figura sin envolvente **y sin traza ancla** → **desaparecen los botones de lazo del modebar** | anotación de estado vacío **obligatoria**; anclar `_build_selection_anchor_trace` a la primera señal *válida* aunque no haya ninguna activa |

El segundo punto de #1 toca directamente el invariante que `CLAUDE.md` marca como fácil de
romper: sin traza ancla, Plotly retira `select2d`/`lasso2d`. Recuperarse seguiría siendo posible
desde el panel ("Deshacer" o quitar el filtro de la lista), pero dejar la barra mutilada es
exactamente el fallo silencioso que ese invariante existe para prevenir.

## E.7 Casos límite

| Caso | Tratamiento |
|---|---|
| **Rango inválido** (`Vpp>=0.5 AND Vpp<=0.2`) | **No es un error**: es un conjunto vacío legítimo. Se aplica, y `detect_contradictions` (puro, sin tocar datos) añade el aviso "Rango vacío: Vpp ≥ 0,5 y Vpp ≤ 0,2 se contradicen". Rechazarlo sería peor: el usuario quiere ver el efecto de lo que pidió. |
| **Umbral permisivo** (fuera del rango, no excluye nada) | La máscara queda todo-`True` → `_sin_exclusiones` (`cache/service.py:26`) la normaliza a `None` → **el régimen de grupo sigue usando caché**. Coste extra: cero. Propiedad valiosa, hay que testearla. |
| **Umbral restrictivo** (0 activas) | §E.6 |
| **Métrica inexistente** | Doble barrera: al añadir se valida contra `list_metrics("puntual")`; al evaluar, `KeyError` capturado → operación **descartada entera**, estado intacto, `MetricFilterResult.error`. Nunca un 500. |
| **NaN / inf** | Excluidos explícitamente. |
| **Muchos filtros** | Sin tope. El coste escala con el número de **métricas distintas**, no de condiciones: 20 condiciones sobre 3 métricas = 3 lecturas de caché + 20 operaciones booleanas ≈ microsegundos. |
| **Duplicado exacto** | Misma `condition_key` → no-op sin subir `filter_version`. |
| **Sin dataset cargado** | No-op, igual que los métodos existentes. |
| **Señales con `valid_mask=False`** | Quedan inactivas. **Consecuencia visible a documentar**: `data/export.py:168` particiona por `active_masks` sin cruzar con `valid_mask`, así que tras cualquier filtro de métrica esas señales migran de "resultantes" a "filtradas". Es defendible; hay que documentarlo y cubrirlo con un test, no dejarlo como sorpresa. |

## E.8 Efecto sobre el bypass de caché *(el riesgo de rendimiento de esta funcionalidad)*

Cualquier exclusión no vacía hace que los dos regímenes de grupo recalculen sin tocar caché
(`cache/service.py:106-112`, `:158-161`). Eso no es nuevo, pero **cambia de frecuencia**: hasta
ahora el bypass se activaba tras un lazo, un gesto deliberado y ocasional; un filtro por métrica
es un predicado global que casi siempre excluye algo, así que **el bypass pasa de excepción a
caso normal**. El coste se paga por cada gráfica #3 de grupo en pantalla y por cada cambio de
filtro.

Por eso el **memo de proceso del punto B3 (Etapa 3) es una dependencia dura de esta
funcionalidad**, no un extra: cachear en memoria por `(cache_key_canónica, filter_version)`
respeta íntegro el invariante del caché en disco y elimina el recálculo repetido.

---

# F. Optimización de Dash y del servidor

## F.1 Qué significa exactamente el aviso

El mensaje viene de **Werkzeug**, el servidor de desarrollo que Flask arranca con `app.run()`.
Advierte sobre robustez y seguridad para exposición pública, **no sobre velocidad de cálculo**.

## F.2 ¿Está afectando al rendimiento? — **No de forma apreciable. Verificado.**

Tres hechos comprobados en este entorno, no supuestos:

1. **El servidor de desarrollo ya es multihilo.** Flask hace `options.setdefault("threaded", True)`
   (`.venv/Lib/site-packages/flask/app.py:655`), así que Werkzeug atiende un hilo por petición.
   La concurrencia **no** es el cuello de botella.
2. **La app es de un solo usuario.** El problema no es servir peticiones simultáneas, es que
   **una sola** petición tarda 26 s. Cambiar de servidor no acorta un `kurtosis`.
3. **El cuello de botella medido está en Python puro**, no en la capa HTTP: ingesta, métricas,
   FFT, pintado. Ningún WSGI toca eso.

**Conclusión honesta: cambiar de servidor no va a resolver el problema de rendimiento.** Lo que
lo resuelve es sacar el cómputo de los callbacks (Etapa 1). Hacerlo igualmente está justificado
por robustez y por el efecto real de la compresión, pero **no debe venderse como una mejora de
velocidad de cálculo**, y hay que medirlo A/B antes y después para no atribuirle mérito ajeno.

## F.3 ¿Afecta a grandes volúmenes u operaciones simultáneas?

Sí, en un punto concreto: el servidor de desarrollo hace *buffering* completo de la respuesta y
no comprime. Con figuras de 1–3 MB eso es latencia real y RAM transitoria. Lo arregla la
compresión (Etapa 4), **no** el cambio de servidor.

Y un punto negativo importante: `ui/app.py:92` (`main()`) usa **`debug=True`**, que activa el
hot-reloader y **duplica el proceso** — con ~1 GB de matriz eso es 2 GB. `scripts/run_dev_server.py`
ya usa `debug=False`, y es el que arranca `.claude/launch.json`, pero el entrypoint del módulo no.

## F.4 Configuración recomendada

Nuevo `scripts/run_server.py`:

```python
from waitress import serve
from ui.app import create_app
serve(create_app().server, host="127.0.0.1", port=8050, threads=8)
```

Y `Dash(..., compress=True)` en `ui/app.py`. Dependencias a fijar con versión exacta en
`requirements.txt`: `waitress` y `flask-compress` (vía `dash[compress]`).

## F.5 Qué servidor — y qué **no**

- **`waitress`** — recomendado. WSGI puro en Python, nativo de Windows (a diferencia de
  gunicorn), **un solo proceso con N hilos**. Es la única opción que preserva la arquitectura.
- **`gunicorn` / `uvicorn` con varios workers — PROHIBIDO en esta aplicación.** Cada worker es
  un proceso con su propio `AppState`, su propio `LoadedDataset` de ~1 GB y su propio
  `ui/reference_registry.py` / `ui/map_registry.py`. Peticiones consecutivas caerían en workers
  distintos: el usuario cargaría un dataset y la siguiente interacción vería "ningún archivo
  cargado", y los `Patch()` de mapas leerían registros vacíos. **La arquitectura de singleton de
  proceso hace del monoproceso un requisito, no una preferencia.** Si algún día se necesita
  multiproceso, primero hay que externalizar el estado (Redis/`diskcache`) — es otro proyecto.
- ASGI (`uvicorn`) no aporta nada: Dash es WSGI síncrono y los callbacks son CPU-bound.

---

# G. Escalabilidad

## G.1 Qué pasa al crecer N

| Operación | Complejidad | A 1M señales |
|---|---|---|
| Ingesta + `data[order]` | O(N·M) con **pico 2×** | **Imposible en RAM** (40 GB AE) |
| Matriz residente | O(N·M) | **Imposible** |
| Metadatos `(N,)` | O(N), ~25 B/señal | 25 MB — trivial |
| Métricas puntuales | O(N·M), ya por bloques acotados | Lineal, aceptable — pero solo si los bloques vienen de disco |
| `compute_spectrum` | O(N·M·log M) + 3 temporales | Lineal; los temporales son lo que hay que quitar (B9) |
| `resolve_groups_by_time` | **O(G·N)** | **Superlineal — el peor escalado del código** |
| Máscara y filtrado | O(N) vectorizado | Trivial |
| Payload gráfica #1 | O(N) → 3N puntos | **12 MB y pintado inviable** |
| Payload gráfica #3 | O(N) por métrica | ~8 MB por métrica |
| Mapa 3D | O(N) puntos, ya 1 075 ms con 12 k | **Inviable** |
| Pilas undo/redo | O(N) × nº de operaciones, **sin tope** | 1 MB por operación de filtrado; acotar la pila |

## G.2 Qué lo arregla

| Estrategia | Dónde | Etapa |
|---|---|---|
| **Streaming + índice de permutación** (no copiar la matriz para ordenar) | `data/ingest.py` | 5a |
| **Carga perezosa por bloques** (`CanonicalStore`, ya escrito y probado) | `data/storage.py` → `core/models.py` | 5b/5c |
| **Cálculo incremental por bloques** (ya existe en `compute_puntual`) | `metrics/engine.py` | 5c |
| **Diezmado exacto por píxel en presentación**, con umbral | `viz/decimation.py` + componentes | 5d |
| **Caché en disco** (ya existe) + **memo de proceso** para el régimen de grupo | `cache/` | 3 |
| **Índice de permutación y `searchsorted`** en vez de barridos O(G·N) | `core/grouping.py` | 3 |
| **Cota en las pilas undo/redo** (p. ej. 50 snapshots, o guardar `packbits`) | `ui/state.py` | 2 |

## G.3 ¿Se envían al frontend más datos de los necesarios? — **Sí**

- Gráfica #1: 3N puntos sin diezmar. A partir de ~100 k señales activas, indefendible.
- Gráfica #3: 1 punto por señal en **float64**; float32 basta para dibujar.
- Mapas: `customdata` int64 (~100 KB) que **nunca vuelve al servidor**.
- `State("graph-timeseries","figure")`: **1,0–1,3 MB navegador→servidor** para leer un `name`.
- Todo sin compresión HTTP.

---

# H. Riesgos y compatibilidad

| Cambio | Qué puede romper | Mitigación |
|---|---|---|
| **Composición de máscaras (E.2)** | Los 6 consumidores de `get_active_mask` | Su firma y semántica **no cambian**. `tests/test_state.py` debe quedar **intacto**: si algo de ahí se rompe, es la señal de que la composición se está filtrando hacia los consumidores. |
| **Traza ancla con 0 activas (E.6)** | `tests/test_graph_timeseries.py:290` afirma `len(fig.data) == 2` → pasa a 3 | Cambio **deliberado**: actualizar y renombrar el test. Documentarlo junto al invariante del modebar en `CLAUDE.md`. |
| **`allow_duplicate` sobre `filter-version`** | La clave `"filter-version.data"` en `callback_map` | Usar `allow_duplicate=True` en el callback **nuevo**, no fusionar con `_on_filter_action`: así la clave del existente no cambia y `test_sensor_window_callbacks.py:695` sigue verde. |
| **Disparo espurio de `ALL` con `n_clicks`** | Añadir un filtro borraría otro | Guardarraíl explícito + test de regresión (§E.5). |
| **Memo de proceso del régimen de grupo (B3)** | Servir resultados obsoletos | La clave incluye `filter_version` **y** la clave canónica de caché (que ya cubre `dataset_id`, versión de métrica y de normalización). LRU acotada por número de entradas. |
| **Diezmado de la envolvente (5d)** | El fallback `_resolve_points_fallback` mapea `pointNumber // 3` → señal | Desactivarlo cuando la envolvente venga diezmada. El camino principal (geometría en servidor contra los arrays completos) **no se ve afectado**, que es justo lo que hace viable el diezmado. |
| **Fuera de núcleo (Etapa 5)** | Es el cambio más invasivo: `SignalBlock` lo toca todo | Rama aparte. `CanonicalStore` ya tiene tests (`tests/test_storage.py`). Mantener el camino en-RAM como modo seleccionable hasta que la suite completa esté verde con el nuevo. |
| **`compress=True` y `waitress`** | Dependencias nuevas en un `requirements.txt` pinneado exacto | Fijar versión exacta, en su propio commit, sin tocar las demás. |
| **Truncar el espectro antes de la magnitud (B9)** | Valores de `feq` distintos por orden de operaciones | Es matemáticamente idéntico (se descartaban esas frecuencias igual). Verificar con `tests/test_spectral.py` y `tests/test_metrics_freq_domain.py` **antes** de tocar nada más. Si algún valor cambiase, subir la versión de la métrica invalida solo el caché. |
| **Warmup paralelo (B8)** | Saturar la CPU y empeorar la latencia de la interfaz | 2–3 hilos, no `cpu_count()`. La FFT ya usa `workers=-1` internamente. Medir la latencia de la interfaz durante el warmup, que es exactamente lo que `RESULTADO.md §2.2` ya midió una vez. |

---

# I. Prioridades y orden de ejecución

| Prioridad | Elemento | Etapa | Por qué |
|---|---|---|---|
| **Crítica** | Línea base medible + generador de datasets grandes | 0 | Con ruido de ±3×, sin esto no se puede afirmar ninguna mejora |
| **Crítica** | Servicio de trabajos en segundo plano (B1) | 1 | Arregla la peor experiencia (41 s congelado) y **desbloquea** el filtrado en frío que se pidió |
| **Crítica** | Filtrado por métricas (§E) | 2 | Es el requisito funcional |
| **Crítica** | Memo de proceso del régimen de grupo (B3) | 3 | **Dependencia dura** de la Etapa 2: sin él el filtro por métricas hace el bypass permanente |
| **Crítica** | Fuera de núcleo (B2) | 5 | Único camino a 250k–1M+; va al final por riesgo, no por importancia |
| **Alta** | `compress=True` (B5), `debounce` (B7), quitar el `State` de la figura (B6) | 3–4 | Horas de trabajo, efecto inmediato y medible |
| **Alta** | Espectro truncado antes de la magnitud (B9), warmup paralelo (B8) | 3 | Atacan las dos métricas más caras y la ventana de caché frío |
| **Alta** | Diezmado por escala en #1/#3/mapas (B4) | 5d | Crítico a escala; irrelevante hoy |
| **Media** | `resolve_groups` (B10), `relayoutData` (B11), `_on_refresh_metrics` con `Patch` (B12) | 3 | Mejoras claras, sin urgencia |
| **Media** | Vectorizar `rise_time`/`zcr` (B13), temporales de `teq`/`shannon_entropy` (B14) | 3 | Reales pero acotadas |
| **Media** | `waitress` + `scripts/run_server.py` (§F) | 6 | Robustez y quitar el aviso; **no** velocidad |
| **Baja** | Memoizar configuración (B15), compactación del caché (B16) | 3 | Higiene |

## Orden y razón

1. **Etapa 0 primero, sin excepción.** Este repo ya revirtió una optimización correcta
   (`hoverinfo="skip"`) porque la midió con una configuración que enmascaraba el efecto. Medir
   mal es peor que no medir.
2. **Etapa 1 antes que la 2** porque el usuario eligió evaluación en segundo plano, y porque el
   servicio de trabajos es la infraestructura que las etapas 2 y 5 dan por hecha.
3. **B3 dentro de la Etapa 2**, no después: entregar el filtro por métricas sin el memo de grupo
   es entregar una funcionalidad que degrada el rendimiento en cuanto se usa.
4. **Etapas 3 y 4 antes que la 5** porque son baratas, reversibles y suben la línea base contra
   la que se medirá el rediseño estructural.
5. **Etapa 5 al final y en rama aparte.** Toca `SignalBlock`, que toca todo. Con la suite verde y
   la línea base archivada, es un refactor grande pero controlado; sin ellas, es a ciegas.
6. **Etapa 6 en cualquier momento**, y sin atribuirle mejoras de cálculo que no produce.

---

# Verificación

**Por etapa, antes de continuar a la siguiente:**

```bash
.venv\Scripts\python.exe -m pytest tests/ -q
```

```bash
.venv\Scripts\python.exe -m mypy core data metrics cache ui viz utils
```

**Rendimiento — A/B consecutiva en el mismo proceso** (el único método fiable aquí, según
`MEJORA_GRAFICAS_ENTREGA.md §4`):

```bash
.venv\Scripts\python.exe -m benchmarks.run_benchmarks --dataset RUTA.hdf5 --repeats 25
```

**Verificación funcional del filtrado (Etapa 2), en el navegador:**

```bash
ANALIZADOR_PROFILING=1 .venv\Scripts\python.exe scripts/run_dev_server.py
```

En `http://127.0.0.1:8050/sensor/UHF`, con `med_5_ago_3.hdf5` cargado:
1. `Vpp >= 0.1` y `Vpp <= 0.3` → el contador baja y las cinco gráficas se propagan.
2. Añadir `feq >= 300e6` y `feq <= 350e6` → intersección de las cuatro condiciones.
3. Umbral imposible → mensaje de estado vacío en #1 y #3, **sin traza en el log de errores**, y
   los botones de lazo **siguen en el modebar** de la gráfica #1.
4. Quitar solo el filtro de `feq` → vuelven exactamente las señales que solo esa condición
   excluía, y **no** las excluidas con el lazo.
5. Dibujar un lazo con filtros activos → se combinan en AND; "Deshacer" restaura máscara **y**
   lista de condiciones.
6. En el log: `etapa=filtro.metricas n_condiciones=4 n_metricas=2` — **dos** llamadas de caché,
   no cuatro — y `etapa=cache.grupo_intrinseca cache=hit` en el segundo refresco con el mismo
   filtro (prueba de que el memo de B3 funciona).

**Verificación de escala (Etapa 5):** cargar el sintético de 1M señales y comprobar que el RSS
máximo se mantiene en el orden de los metadatos (~decenas de MB por sensor) más el bloque de
cálculo (`target_block_bytes`, 64 MB), no en el de la matriz completa.