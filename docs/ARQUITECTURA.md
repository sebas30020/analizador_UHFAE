# Arquitectura

Documento de arquitectura de la Fase 7. Describe el sistema tal como está construido, no
como se planeó: donde el diseño de la Fase 0 cambió durante la implementación, aquí queda
la versión vigente y la razón del cambio.

## 1. Capas

El flujo es estrictamente unidireccional: **datos → procesamiento → caché →
presentación**. Ninguna capa conoce los detalles internos de la inferior, y la de
presentación no contiene lógica de cálculo.

```
┌──────────────────────────────────────────────────────────────────────┐
│ PRESENTACIÓN            ui/app.py        ruteo y arranque            │
│                         ui/components/   3 tipos de gráfica, paneles │
│                         ui/callbacks/    envoltorios de Dash         │
│                         ui/state.py      fuente única de verdad      │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ pide "esta métrica de este sensor"
┌───────────────────────────────▼──────────────────────────────────────┐
│ CACHÉ                   cache/service.py  fachada: hit → devuelve     │
│                         cache/keys.py     clave determinista          │
│                         cache/backend.py  SQLite (índice) + HDF5      │
│                         cache/warmup.py   precalentamiento en hilo    │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ solo en caso de miss
┌───────────────────────────────▼──────────────────────────────────────┐
│ PROCESAMIENTO           metrics/engine.py    orquestación y regímenes │
│                         metrics/registry.py  registro por decorador   │
│                         metrics/spectral.py  FFT centralizada         │
│                         metrics/{time,freq}_domain/  una métrica = un │
│                                                      archivo          │
│                         core/grouping.py     ventanas de agrupamiento │
│                         core/normalization.py  x / vrange (versionada)│
└───────────────────────────────┬──────────────────────────────────────┘
                                │ opera sobre SignalBlock
┌───────────────────────────────▼──────────────────────────────────────┐
│ DATOS                   data/readers/   interfaz + lector HDF5        │
│                         data/ingest.py  chunks → matriz global        │
│                         data/storage.py persistencia canónica         │
│                         core/models.py  modelo de datos canónico      │
└──────────────────────────────────────────────────────────────────────┘

  transversal:  config/sensors.yaml   perfil por sensor, paralelismo,
                                       normalización, profiling
                utils/profiling.py    tiempos por etapa (§9.3)
                viz/decimation.py     diezmado min/max (gráfica #2)
```

## 2. El modelo de datos canónico

Todo aguas arriba de `data/ingest.py` opera sobre un único objeto por sensor,
`core.models.SignalBlock`:

| Campo | Forma | Significado |
|---|---|---|
| `data` | `(N, M)` float32 | matriz global de trazas, **ordenada cronológicamente** |
| `timestamps` | `(N,)` float64 | timestamp UNIX absoluto de cada señal |
| `trigger`, `vrange` | `(N,)` float64 | metadatos de captura por señal |
| `valid_mask` | `(N,)` bool | `False` si el metadato es inutilizable (`vrange=0`, `trigger` NaN) |
| `minmax` | `(N, 2)` float32 | mínimo y máximo por señal, calculados **una sola vez** en ingesta |

El concepto de *chunk* de la base de origen **desaparece en la ingesta**: es un detalle de
cómo se grabó el archivo, no del dominio. `ingest_sensor` concatena todos los lotes que
entrega el lector y **ordena explícitamente por timestamp** — no asume que el orden de
llegada sea cronológico, aunque en los datos reales coincida.

`minmax` se persiste porque es lo que dibuja la gráfica #1: recalcular el min/max de
20 000 trazas en cada render sería recomputar en tiempo de presentación algo que no
cambia nunca.

## 3. Normalización: una sola regla, versionada

`x_norm = x_raw / vrange` (`core/normalization.py`). Todas las métricas se calculan sobre
la señal normalizada; ninguna métrica normaliza por su cuenta. La versión de la regla
(`v1_divide_by_vrange`) entra en la clave de caché, así que cambiarla invalida
automáticamente todo lo calculado con la anterior, sin borrar nada a mano.

La gráfica #2 muestra por defecto la señal normalizada — la misma magnitud que miden
Vmax, RMS y compañía — con un interruptor explícito para ver la cruda.

## 4. Los dos regímenes de métrica

- **Puntual**: un valor por señal. Se calcula vectorizado sobre la matriz `(N, M)`
  completa, nunca señal por señal.
- **Grupo**: un valor por ventana de agrupamiento (`by_time` en segundos, o `by_count` en
  número de señales). Se subdivide en dos:
  - *Reducción* (`compute_group_reduction`): aplica una métrica puntual dentro del grupo
    y la reduce con mediana / media / percentil.
  - *Intrínseca* (`compute_group_intrinsic`): la métrica solo tiene sentido sobre el
    conjunto (tasa de pulsos, de energía, de ráfagas). `T_w` es siempre la duración
    **declarada** de la ventana, nunca inferida de los timestamps observados.

Las métricas que necesitan la secuencia global de pulsos (`delta_t`, `log_delta_t`) se
calculan una vez sobre todo el sensor y se recortan por grupo: reiniciarlas en cada
ventana daría Δt = 0 al inicio de cada grupo, que es un resultado incorrecto y no un
detalle de implementación.

## 5. Caché

La clave es determinista y cubre todo lo que puede cambiar el resultado: dataset,
sensor, métrica, versión de la métrica, versión de normalización, parámetros y
especificación de agrupamiento. El índice va en SQLite y los arrays en HDF5.

El caché siempre almacena resultados **sobre el conjunto completo de señales**, nunca
sobre un subconjunto filtrado. El filtrado interactivo del usuario se aplica encima:

- Régimen puntual: filtro posterior en memoria sobre el resultado cacheado. Excluir una
  señal no cambia el valor de las demás, así que nunca hace falta recalcular.
- Régimen grupo: **bypass total** del caché. El agregado de un grupo sí cambia al excluir
  una señal, así que se recalcula; escribir ese resultado filtrado en el caché lo
  envenenaría para la vista sin filtro.

## 6. Presentación

Un único proceso Dash sirve todas las ventanas. `/sensor/UHF` y `/sensor/AE` son la misma
página con distinto sensor activo; los callbacks se registran **una sola vez** y resuelven
el sensor en tiempo de render leyendo el Store `page-sensor`. Las "ventanas gemelas" del
requisito son pestañas del navegador, no dos layouts montados a la vez.

`ui/state.py::AppState` es la fuente única de verdad: dataset cargado, índice de señal
activa por sensor y **máscara de filtrado por sensor** con sus pilas de deshacer/rehacer.
Las gráficas son vistas derivadas; ninguna guarda su propio estado de filtrado.

Dos contadores monotónicos (`dataset-version`, `filter-version`) actúan como señal de
"hay que repintar": los callbacks de refresco los toman como `Input` en vez de inventar
un mecanismo propio de notificación.

## 7. Diezmado: dónde sí y dónde no

`viz/decimation.py` agrega min/max por bin de píxel, de forma **exacta** (mínimo de los
mínimos, máximo de los máximos), nunca por submuestreo.

- **Gráfica #1**: no diezma. Desde la Fase 7 dibuja un segmento vertical por cada señal
  activa, aunque sean decenas de miles — el requisito es ver el conjunto completo. Para
  que eso sea viable la traza es `Scattergl` (WebGL) y los datos van como arrays de
  numpy con separador `NaN`, no como listas de Python.
- **Gráfica #2 (AE)**: sí diezma la traza cruda de 10 000 muestras cuando se ve completa,
  y restaura resolución total al hacer zoom. La barra de metadatos siempre dice cuál de
  las dos vistas está en pantalla.

## 8. Las tres pruebas de fuego (PROMPT §10.3)

### 8.1 Agregar una métrica nueva → un archivo, cero cambios en el núcleo

Crear `metrics/time_domain/mi_metrica.py` con una función decorada con `@metric(...)`.
`discover_metrics()` la encuentra por introspección del paquete al arrancar; el selector
de la interfaz se puebla desde el mismo registro. **Cero cambios** en `engine.py`,
`service.py`, `registry.py` o la UI. Paso a paso en
[COMO_AGREGAR_UNA_METRICA.md](COMO_AGREGAR_UNA_METRICA.md).

### 8.2 Agregar un tercer sensor → una entrada en el YAML

Los perfiles de sensor viven en `config/sensors.yaml` (`fs_hz`, `n_samples`,
`freq_limit_hz`, unidad del eje, presupuesto de bloque). Ninguna dimensión temporal está
cableada en el código: todo módulo que necesite `fs`/`M` lo lee de `SensorConfig`. La
ventana de sensor está parametrizada por nombre de sensor y los callbacks se registran una
sola vez, así que un tercer sensor no duplica lógica de ventana — necesita su entrada en
el YAML y su grupo correspondiente en el archivo de origen.

Lo que **sí** haría falta tocar: `VALID_SENSORS` en `ui/callbacks/helpers.py`, que hoy
enumera los sensores válidos para el ruteo. Es la única lista cerrada que queda.

### 8.3 Cambiar el motor de la base de datos origen → un lector nuevo

`data/readers/base.py` define la interfaz `OriginReader` (iterar lotes de señales,
ambientales, eventos, atributos del experimento, `dataset_id`). `HDF5Reader` es una
implementación. Un origen distinto (Parquet, TDMS, un servicio remoto) implementa esa
interfaz y se inyecta en `ingest_experiment`, que solo habla con la abstracción. **Cero
cambios** aguas arriba de la capa de datos.

## 9. Decisiones que conviene conocer antes de tocar el código

- **Paralelismo medido, no supuesto.** `compute_puntual` acepta `n_workers`, pero el
  valor por defecto es serial: con estos tamaños, arrancar procesos `loky` en Windows
  cuesta más que el cálculo vectorizado que ahorra (RMS sobre AE: ~3,4 s serial contra
  ~39 s con 8 procesos). Queda implementado y probado para cuando un caso más grande lo
  justifique.
- **`add_vline` no se usa.** Las líneas de evento se construyen como una lista de shapes
  y se asignan de una vez: `add_vline` revalida la figura entera en cada llamada y con 98
  eventos costaba ~4,8 s por gráfica.
- **La matriz de señales de un grupo se materializa de forma perezosa.** `MetricContext`
  la construye solo si la métrica llega a leerla; `tasa_pulsos` y `tasa_rafagas` no la
  leen nunca.
- **El estado es de proceso, no de pestaña.** Cargar un dataset nuevo no refresca las
  pestañas ya abiertas: se enteran al recargar.
