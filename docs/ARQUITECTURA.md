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
│                         ui/components/   5 tipos de gráfica, paneles │
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
                viz/maps.py           ejes de los mapas #4/#5 (§6.1)
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
- Régimen grupo: **bypass total** del caché cuando la máscara excluye al menos una señal. El
  agregado de un grupo sí cambia al excluir una señal, así que se recalcula; escribir ese
  resultado filtrado en el caché lo envenenaría para la vista sin filtro. Cuando la máscara no
  excluye ninguna señal (o es `None`), el régimen de grupo consulta y persiste en el caché con
  normalidad, aprovechando el precalentamiento.

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

### 6.1 Mapas de separación #4 (2D) y #5 (3D)

Un punto = una señal, ubicada por dos o tres **métricas puntuales** cualesquiera. Solo el
régimen puntual: es el único que produce un escalar por señal. `viz/maps.py` no calcula
nada — pide cada eje a `cache/service.py::get_or_compute_puntual`, el mismo camino que
las gráficas #3, así que un punto del mapa usa exactamente el valor que el usuario ya ve
graficado en el tiempo.

Los ejes quedan alineados **por construcción**, sin emparejar por timestamp:
`get_or_compute_puntual` siempre consulta el caché sobre el conjunto completo y aplica
`active_mask` como recorte posterior, así que el orden resultante es siempre
`np.where(valid_mask & active_mask)[0]`. Ese mismo array es el índice global de señal de
cada punto, y es lo que resuelve el clic, el tooltip y el lazo.

`maps-container` es **hermano y posterior** a `metrics-graphs-container` en el layout: que
los mapas queden siempre debajo de las métricas es una propiedad del árbol de
componentes, no lógica de un callback.

El resaltado de la señal activa se mueve con un `dash.Patch` sobre una traza dedicada
(siempre la índice 1, vacía cuando no aplica), leyendo el `MapDataset` de un registro de
proceso (`ui/map_registry.py`, mismo patrón que `ui/reference_registry.py`). Navegar entre
señales no reconstruye la nube de puntos.

El mapa 3D no alimenta el filtrado: Plotly no ofrece lazo ni caja de selección dentro de
una escena `scene`. Sí responde al clic, igual que el 2D.

### 6.2 Gráficas de métricas #3: renderizado adaptativo SVG / WebGL

Las gráficas de evolución de métricas (`ui/components/graph_metric.py`) adaptan dinámicamente
el motor de renderizado según el volumen de datos y el régimen:
- **Régimen de grupo o $N \le 5\,000$ puntos (`go.Scatter` - SVG):** Ofrece trazado vectorial
  continuo, nítido y sin artefactos. Mantiene bajo el consumo de contextos WebGL.
- **Régimen puntual masivo $> 5\,000$ puntos (`go.Scattergl` - WebGL):** Conmuta a WebGL
  para prevenir la sobrecarga del árbol DOM con decenas de miles de elementos SVG,
  garantizando 60 fps en paneo y zoom.
- **Presupuesto de contextos WebGL:** Los navegadores imponen un límite de 8 a 16 contextos
  WebGL activos por pestaña. Si cada gráfica de métrica usara WebGL indiscriminadamente,
  abrir múltiples métricas provocaría pérdida de contexto (*context loss*). La conmutación
  adaptativa reserva WebGL exclusivamente para cuando el volumen de puntos lo exige.
- **Preservación de estilo:** Ambos motores reciben y respetan los colores por punto
  `is_partial` (`#4A7BB0` estándar / `#C2A83E` parcial) y la opacidad atenuada
  (`RAW_POINT_OPACITY_DIMMED = 0.30`) con suavizado de tendencia activo.

## 7. Diezmado: dónde sí y dónde no

`viz/decimation.py` agrega min/max por bin de píxel, de forma **exacta** (mínimo de los
mínimos, máximo de los máximos), nunca por submuestreo.

- **Gráfica #1**: no diezma. Dibuja un segmento vertical por cada señal activa, aunque sean
  decenas de miles — el requisito es ver el conjunto completo sin perder eventos breves.
  La traza es `Scattergl` (WebGL) con `mode="lines"` y los datos se emiten como arrays
  `np.float32` con separadores `NaN` (`build_vertical_segments`). Plotly.py serializa estos
  arrays como buffers binarios base64 tipados (`f4`), reduciendo un 50% el buffer de transporte
  y ~44% el JSON total sin alterar la precisión analítica de 64 bits del motor de cálculo.
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
`freq_limit_hz`, unidad del eje, presupuesto de bloque, `decimate_full_view`,
`has_trigger_metadata`). Ninguna dimensión temporal está cableada en el código: todo
módulo que necesite `fs`/`M` lo lee de `SensorConfig`. La ventana de sensor está
parametrizada por nombre de sensor y los callbacks se registran una sola vez, así que un
tercer sensor no duplica lógica de ventana — necesita su entrada en el YAML y su grupo
correspondiente en el archivo de origen.

Ya no es hipotético: la rama `lectura_keysight` (`archivos_md/PLAN_LECTURA_KEYSIGHT.md`)
agregó `UHF_KS` (osciloscopio Keysight en memoria segmentada, ver
`archivos_md/esquema_keysight_h5.md`) sin tocar `metrics/`, `core/normalization.py` ni
`core/grouping.py`. Lo que sí hizo falta tocar, además del YAML:

- `VALID_SENSORS` en `ui/callbacks/helpers.py` — la única lista cerrada de sensores que
  queda, para el ruteo `/sensor/<nombre>`.
- El literal de diezmado de la gráfica #2 (`ui/components/graph_signal.py`), que antes
  comparaba `sensor_config.name == "AE"` — ahora lee `SensorConfig.decimate_full_view`,
  porque un tercer sensor con más muestras que píxeles necesita el mismo tratamiento que
  AE sin que el nombre "AE" quede cableado en la capa de presentación.
- Un aviso en la ventana de sensor cuando el dataset cargado no trae señales de ese
  sensor (`ui/callbacks/helpers.py::resolve_sensor_availability_notice`) — con tres
  sensores posibles y cada origen entregando solo un subconjunto, una ventana vacía sin
  explicación es indistinguible de un error.

Lo que **no** cambió: ningún callback de navegación, filtrado, agrupamiento, línea de
referencia o suavizado — todos resuelven el sensor en tiempo de render leyendo el Store
`page-sensor`, así que un sensor nuevo los hereda sin código adicional.

### 8.3 Cambiar el motor de la base de datos origen → un lector nuevo

`data/readers/base.py` define la interfaz `OriginReader` (iterar lotes de señales,
ambientales, eventos, atributos del experimento, `dataset_id`, y desde la rama
`lectura_keysight` también `available_sensors()` y `sensor_config_overrides()`).
`HDF5Reader` y `KeysightSegmentedReader` son dos implementaciones con esquemas de origen
completamente distintos (una con chunks/ambientales/eventos, la otra memoria segmentada
sin ninguno de los dos). Un origen distinto (Parquet, TDMS, un servicio remoto) implementa
la misma interfaz y se inyecta en `ingest_experiment`, que solo habla con la abstracción.
**Cero cambios** aguas arriba de la capa de datos.

`data/readers/factory.py::open_reader` es la pieza nueva que faltaba para que esto
funcionara con **dos orígenes simultáneos** en el mismo proceso: ambos formatos usan
indistintamente `.h5`/`.hdf5`, así que la elección de lector es por *sniffing* de
contenido (¿existe `FileType/KeysightH5FileType`? ¿hay un grupo raíz con subgrupos
`chunk_*`?), no por extensión. Es el único punto de la capa de presentación que sabe que
existe más de un formato de origen — `ui/state.py::load_dataset` llama a `open_reader` y
a partir de ahí solo habla con `OriginReader`.

Dos consecuencias de tener sensores con `fs_hz`/`n_samples` que varían por archivo (algo
que no existía antes de esta rama): `available_sensors()` evita que `ingest_experiment`
pida lotes a un sensor que el origen no tiene, y `sensor_config_overrides()` permite que
el archivo aporte el valor efectivo por encima del nominal del YAML — resuelto una sola
vez al cargar, nunca en cada cálculo. `dataset_id` (`ruta:tamaño:mtime_ns`) sigue
identificando el archivo 1 a 1, así que no hay riesgo de que la clave de caché mezcle
resultados de dos configuraciones distintas aunque `fs_hz`/`n_samples` no formen parte de
esa clave.

Un cuarto origen ya se implementó con este mismo mecanismo, esta vez generado por la
propia herramienta: `data/export.py::export_filtered` escribe un archivo con las señales
resultantes/filtradas del filtrado interactivo (Fase 6, `archivos_md/
EXPORTACION_FILTRADA_ENTREGA.md`), y `data/readers/filtered_export_reader.py::
FilteredExportReader` lo relee como un `OriginReader` más — `open_reader` lo distingue
de los otros dos por *sniffing* del atributo raíz `file_type`, igual que distingue
Keysight de HDF5 con chunks. **Cero cambios** en `data/ingest.py`, `metrics/`, `cache/`
ni en los otros dos readers.

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
- **`customdata` NO llega al servidor. Identifica los puntos por posición.** plotly.py 6.x
  serializa los arrays de numpy como *typed arrays* en base64 (`{"dtype", "bdata"}`) en
  vez de listas, y el `filterEventData` de `dcc.Graph` reconstruye el `customdata` de cada
  punto haciendo `gd.data[curveNumber].customdata[pointNumber]` — indexar ese objeto con
  un entero da `undefined`, así que el punto llega **sin** `customdata`. El tooltip sí lo
  muestra porque lo renderiza Plotly desde `_fullData`, ya decodificado, sin pasar por
  Dash: la funcionalidad *parece* correcta mientras los callbacks no hacen nada. Fue un
  bug real de los mapas #4/#5, encontrado en uso y no por las pruebas, que lo daban por
  bueno con un payload inventado. Lo que sí sobrevive el filtro son los números planos del
  evento (`curveNumber`, `pointNumber`, `x`, `y`, `z`), y por eso toda identificación de
  punto se resuelve por posición contra el array con el que se dibujó la traza.
- **Un punto de Plotly no siempre es una señal; la conversión es aritmética y complementaria.**
  La gráfica #1 dibuja cada señal como un segmento vertical de tres entradas
  (mínimo, máximo, separador `NaN`, `viz/decimation.py::ENTRIES_PER_SEGMENT`), por lo que la
  señal del punto `pointNumber` es `pointNumber // 3` (utilizado como fallback de selección
  cuando el payload no incluye coordenadas geométricas).
- **Resolución geométrica de selecciones en el servidor (`ui/callbacks/filtering.py`).**
  A partir de la Fase 2, la selección por lazo (`lassoPoints`) y caja (`range`) en la gráfica #1 se
  resuelve en el servidor de forma puramente geométrica y vectorizada en NumPy. Se evalúa
  la intersección exacta del segmento vertical `[y_min, y_max]` con el polígono del lazo (vía
  ray casting hacia $+Y$ y comprobación de cortes de aristas) o con el rectángulo de la caja.
  Esto garantiza que cualquier segmento que cruce el área seleccionada sea detectado exactamente,
  incluso si ninguno de sus extremos queda dentro del polígono trazado.
- **La envolvente no participa del hover (`hoverinfo="skip"`).** Es lo que libera al navegador
  del escaneo lineal $O(N)$ sobre el hilo principal: por debajo de `TOO_MANY_POINTS` (`1e5`)
  Plotly no construye el kd-tree de `scattergl` y recorre los 61 722 puntos de la envolvente
  cada `HOVERMINTIME` (50 ms). Medido: 22,5 ms contra 3,3 ms por `mousemove`
  (`docs/RENDIMIENTO.md` §1.2). El precio es el tooltip de la envolvente; la navegación por
  clic la cubre `clickanywhere`, y las series ambientales conservan el suyo.
- **Traza ancla del lazo.** La envolvente va en `mode="lines"` sin marcadores, y también las
  ambientales y los eventos. Plotly solo ofrece `select2d`/`lasso2d` si **alguna** traza tiene
  marcadores o texto (`isSelectable`, `components/modebar/manage.js`), así que sin ninguna la
  barra se queda sin los botones y el lazo deja de ser alcanzable desde la interfaz — aunque
  toda la resolución del servidor siga intacta y con sus pruebas en verde.
  `_build_selection_anchor_trace` añade por eso un único punto invisible con marcador
  (`opacity=0`, `hoverinfo="skip"`, sin leyenda), colocado sobre la primera señal activa para
  no arrastrar el autorango y **siempre en última posición** para no desplazar los
  `curveNumber` de los que depende `ui/callbacks/filtering.py`. Forzar los botones con
  `modeBarButtonsToAdd` no funciona: Plotly los filtra igual.

