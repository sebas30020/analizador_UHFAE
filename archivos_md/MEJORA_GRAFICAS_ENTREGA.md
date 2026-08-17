
# Mejora de visualización de las gráficas #1 y #3 (entrega)

Implementación de `archivos_md/prompt-mejora-graficas.md`: control de visibilidad de
eventos y unión/suavizado de los puntos de métrica. Alcance real: gráficas tipo **#1**
(serie temporal global) y tipo **#3** (evolución de métricas) — ver §1 para la
corrección de numeración respecto del enunciado original.

## 1. Decisiones de diseño ante ambigüedades del enunciado

- **Numeración de gráficas: el enunciado dice "#1 y #2", se implementó "#1 y #3".**
  La gráfica tipo #2 (`ui/components/graph_signal.py`) es la traza intra-señal en
  µs/ms de una sola señal: no dibuja métricas, no recibe `events`/`t0` en su firma, y
  no tiene ningún concepto de "métrica individual" ni "métrica de grupo". Las dos
  gráficas donde conviven eventos y métricas —las que el enunciado describe— son la
  #1 (envolvente global) y la #3 (evolución de métricas). Decisión confirmada con el
  usuario antes de implementar: se interviene #1 y #3, la #2 queda sin cambios
  funcionales.
- **Contradicción con `PROMPT_Analizador_Señales_UHF_AE.md` §5.3.** Esa sección
  prohibía literalmente "dibujar líneas de tendencia, ajustes, regresiones, medias
  móviles o cualquier elemento derivado" en la gráfica de métricas, y el código lo
  citaba como justificación del scatter puro. El requerimiento nuevo exige
  exactamente lo contrario. Decisión confirmada con el usuario: el requerimiento más
  reciente prevalece; el PROMPT maestro no se edita (queda como bitácora de la
  decisión anterior), y `ui/components/graph_metric.py` documenta en su docstring que
  ese criterio quedó superado.
- **Toggle de eventos sin recálculo, resuelto con `dash.Patch`.** `AppState.
  get_active_mask()` (`ui/state.py`) nunca devuelve `None`, así que en
  `_on_refresh_metrics` la máscara activa siempre está presente y las fachadas de
  grupo (`cache/service.py`) siempre saltan el caché (§8.2: bypass total con máscara
  activa). Colgar el control "Mostrar eventos" de ese callback como `Input` habría
  recalculado todas las métricas de grupo en cada conmutación, violando el requisito
  explícito de no recálculo. Se resolvió con un callback aparte
  (`_on_toggle_events`) que solo parchea `layout.shapes` de las figuras ya
  construidas vía `dash.Patch`, y los callbacks que sí reconstruyen la figura reciben
  el estado del control como `State`, nunca como `Input`.
- **`gap-threshold` se trató como `Input`, no como control pasivo.** El enunciado
  menciona "cuatro controles de suavizado" como `Input`, pero dejar el umbral de
  corte de huecos como no-reactivo violaría el requisito de reflejo inmediato sin
  botón "Aplicar" (§4) y el criterio de aceptación 7. Se incluyó como quinto `Input`
  de `_on_refresh_metrics`.

## 2. Estrategia de suavizado

Módulo nuevo y puro: `viz/smoothing.py`, sin dependencia de Plotly ni del motor de
métricas — reutilizado sin duplicación por régimen puntual y de grupo, y por las dos
gráficas que lo necesitan.

- **Método por defecto: media móvil centrada de ventana temporal**
  (`"media_movil_temporal"`), vectorizada con dos sumas de prefijos (`cumsum`) y dos
  `searchsorted` por lote de abscisas de consulta — sin bucles punto a punto.
- **Por qué ventana temporal y no por número de puntos.** El eje X del proyecto no es
  equiespaciado: hay huecos de adquisición estructurales
  (`archivos_md/esquema_med_5_ago_3.md`: no todos los chunks tienen señales de ambos
  sensores; el experimento cubre ~5 h 52 min con solo 766 chunks de 10 s). Con
  espaciado irregular, una ventana por conteo de puntos abarca un intervalo de tiempo
  distinto según la densidad local de la serie, lo que distorsiona la tendencia. Por
  la misma razón se descartaron Savitzky-Golay (exige muestreo uniforme) y LOWESS
  (además, costo prohibitivo con 10⁴-10⁵ puntos).
- **Mediana móvil por número de puntos** (`"mediana_movil_puntos"`) se ofrece como
  alternativa seleccionable, robusta ante los picos espurios típicos de descargas
  parciales; acotada a una ventana máxima (`MAX_MEDIAN_WINDOW_POINTS = 501`) para que
  `sliding_window_view` + `nanmedian` no crezca sin control.
- **Bordes:** la ventana simplemente se encoge donde falta un lado (no hay tantos
  vecinos), sin relleno ni recorte artificial — ni la media temporal ni la mediana
  por puntos necesitan tratamiento especial en los extremos.
- **Evaluación en `x_query` reducido.** La media temporal puede evaluarse en
  cualquier conjunto de abscisas, no solo en los puntos de entrada. En régimen
  puntual (12 484 / 20 574 puntos) se evalúa sobre un máximo de 2000 abscisas
  equiespaciadas (`DEFAULT_MAX_QUERY_POINTS`): la curva se ve idéntica y el JSON que
  viaja al navegador no se duplica. En régimen de grupo (109-233 puntos con el
  agrupamiento por defecto) se evalúa sobre todos.
- **Sin caché de suavizado.** Medido (ver §4): 1-2 ms sobre el dataset completo —
  añadir una caché habría costado más complejidad que el tiempo que ahorra.

## 3. Manejo de huecos y de valores nulos

- **Huecos: se corta la línea, nunca se une a través de un hueco de adquisición
  real.** `split_on_gaps` inserta un separador `NaN` donde `diff(x)` supera el
  umbral (automático: 5× la mediana de los incrementos positivos, o el valor que
  ingrese el usuario en "Cortar la línea en huecos mayores a"). Es el mismo idioma
  que el proyecto ya usa en `viz/decimation.py::build_vertical_segments` (`NaN` como
  separador que Plotly no conecta con `connectgaps=False`, el valor por omisión).
- **Nulos: se excluyen del promedio, nunca se interpolan ni se fabrica un cero.** Las
  métricas puntuales devuelven `NaN` legítimos en casos degenerados (varianza cero en
  `kurtosis`/`skewness`, etc. — `docs/COMO_AGREGAR_UNA_METRICA.md`: "Devuelve NaN, no
  excepciones"). La media móvil temporal cuenta los valores finitos por separado del
  acumulador de suma; si una ventana no contiene ningún valor finito, el resultado en
  ese punto es `NaN` — coherente con la doctrina ya establecida del proyecto
  ("bins vacíos se omiten, no se rellenan con 0", `viz/decimation.py`).
- **Orden:** ya garantizado aguas arriba (`data/ingest.py` ordena con `argsort`
  estable; los centros de grupo son monótonos por construcción en
  `core/grouping.py`) — la capa de presentación no reordena nada.

## 4. Rendimiento

**Metodología.** `docs/RENDIMIENTO.md` advierte que dos corridas no consecutivas del
mismo benchmark pueden diferir hasta 4× en esta máquina por carga externa, y que sus
números "sirven para detectar regresiones de orden de magnitud, no para comparar
diferencias del 20 %". La primera comparación de este trabajo (corrida completa de
`benchmarks/run_benchmarks.py`, antes vs. después, no consecutivas) mostró justamente
ese efecto: **incluso el código sin modificar** repitió con "Filtro + propagación"
por encima del umbral de 200 ms en una corrida posterior (219 ms AE), y rutas
totalmente ajenas a este trabajo (ingesta, lectura de caché, render de la gráfica #1
sin eventos) también se ralentizaron 20-40 %. Por eso la comparación válida no es esa
corrida única, sino la de abajo: mismo proceso, mismas condiciones, alta repetición.

**Comparación aislada y controlada** (`git stash` de los archivos modificados,
25 repeticiones con 3 de calentamiento, antes y después ejecutados de forma
consecutiva en la misma sesión de máquina, midiendo el cierre exacto de "Filtro +
propagación": envolvente #1 + métrica puntual `rms` + métrica de grupo intrínseca
`tasa_pulsos`, con `active_mask` activo — bypass de caché real en ambos casos):

| Sensor | Antes (mediana / min / max, n=25) | Después (mediana / min / max, n=25) | Diferencia |
|---|---:|---:|---:|
| UHF | 55.8 / 52.9 / 96.9 ms | 57.6 / 52.6 / 106.0 ms | +1.8 ms mediana (+3 %), mínimos iguales |
| AE  | 66.7 / 59.1 / 79.3 ms | 64.6 / 61.2 / 78.0 ms | −2.1 ms mediana (más rápido) |

Ambas diferencias de mediana están dentro del ruido de medición de la máquina. Esta
ruta de "Filtro + propagación" usa parámetros por defecto de `build_metric_figure`
(sin suavizado, sin unión), así que el resultado esperado —y confirmado— es "sin
cambio real": ninguno de los cambios de esta tarea se ejecuta en su código.

**Costo marginal de activar el suavizado** (dataset completo, `n=20` repeticiones,
mismo proceso, midiendo `build_metric_figure` + `plotly.io.to_json` sin recalcular la
métrica):

| Sensor | Régimen | N puntos | Sin suavizado | Con suavizado (ventana 5 min) | Costo añadido |
|---|---|---:|---:|---:|---:|
| UHF | puntual (`rms`) | 12 484 | 13.1 ms | 14.9 ms | +1.8 ms |
| UHF | grupo (`tasa_pulsos`, con unión) | 109 | 14.5 ms | 15.1 ms | +0.6 ms |
| AE  | puntual (`rms`) | 20 574 | 14.7 ms | 16.0 ms | +1.3 ms |
| AE  | grupo (`tasa_pulsos`, con unión) | 233 | 16.0 ms | 17.1 ms | +1.1 ms |

Costo añadido de 1-2 ms sobre el dataset completo en el peor caso, muy por debajo de
cualquier umbral del §9.2 (200-2000 ms según la operación). El control "Suavizar
métricas individuales" viene activado por defecto en la GUI, así que este es el costo
real que paga el usuario al abrir la aplicación por primera vez con datos cargados.

**Indicadores nuevos incorporados a `benchmarks/run_benchmarks.py`**: "Render gráfica
#3 (puntual, dataset completo)" y "Render gráfica #3 (grupo, by_time 60 s)" — hasta
ahora la #3 solo se medía embebida dentro de "Filtro + propagación". Quedan sin
umbral (`objetivo_ms=None`, "reportar"), igual que el resto de indicadores sin
objetivo explícito en el §9.2 original.

**Conclusión respecto al requisito duro del §5**: con los controles en su estado por
defecto, el sobrecosto es despreciable (1-2 ms sobre miles de puntos); la ruta de
cálculo de métricas y de agrupamiento no se tocó, y "Filtro + propagación" no
muestra una diferencia real atribuible a este trabajo.

## 5. Auditoría de texto de interfaz (§4.1)

Correcciones aplicadas:

| Ubicación | Antes | Después | Motivo |
|---|---|---|---|
| `control_panel.py` (H4) | `Métricas (gráficas tipo #3)` | `Métricas` | numeración interna de diseño, no vocabulario de usuario |
| `control_panel.py` (H4) | `Filtrado (§7)` | `Filtrado` | referencia a sección del PROMPT maestro |
| `control_panel.py` (placeholder) | `Elegí una o más métricas para graficar...` | `Seleccionar una o más métricas` | voseo informal → infinitivo, registro formal |
| `control_panel.py` (label) | `Reductor (solo aplica a reducción de métrica puntual)` | `Reductor de grupo` + tooltip | el matiz pasa al tooltip, la etiqueta queda corta |
| `control_panel.py` | *(input sin etiqueta)* | `Percentil (%)` | control numérico sin etiqueta |
| `control_panel.py` | *(input sin etiqueta)* | `Ventana de agrupamiento` + tooltip | control numérico sin etiqueta ni unidad visible |
| `control_panel.py` (botón) | `Seleccionar base de datos...` | `Seleccionar base de datos…` | elipsis tipográfica, ya usada en el resto de la interfaz |
| `sensor_window.py` (enlace) | `Abrir ventana {sensor}  ↗` (doble espacio) | espacio simple | error de formato |
| `metadata_panel.py` | `Timestamp:` | `Marca de tiempo:` | anglicismo evitable conviviendo con etiquetas en español |
| `config/sensors.yaml` / eje de la gráfica #2 (UHF) | `Tiempo (us)` | `Tiempo (µs)` | símbolo correcto de microsegundos |
| `metrics/time_domain/teq.py` (unidad) | `us` | `µs` | idem, en el eje Y de la métrica "Tiempo Eq." |
| `metrics/time_domain/rise_time.py` (label) | `Rise Time` | `Tiempo de subida` | anglicismo sin razón técnica para conservarlo |

Se conservan deliberadamente `Trigger` (nombre del campo de origen en el HDF5 y
término estándar de instrumentación), y `Kurtosis`, `Skewness`, `ZCR`, `Delta T`
(nomenclatura estándar de la literatura técnica de descargas parciales, usada igual
en español). Cambiar `label` no afecta ninguna clave de caché: `cache/keys.py`
construye la clave con `metric_id`, no con la etiqueta.

Todas las demás referencias a `PROMPT`, `§`, `Fase N` en el código (55+ ocurrencias)
están en docstrings y comentarios internos, no llegan a la interfaz, y se dejaron
intactas por ser documentación legítima del repositorio.

## 6. Controles nuevos

Bloque "Opciones de visualización" en el panel lateral (`ui/components/
control_panel.py`), con el mismo estilo de los controles ya existentes:

| Control | Tipo | Por defecto | Efecto |
|---|---|---|---|
| Mostrar eventos | Checklist | activado | visibilidad de líneas de evento en #1 y #3, sin recálculo |
| Suavizar métricas individuales | Checklist | activado | línea de tendencia sobre marcadores atenuados, régimen puntual |
| Suavizar tendencia agrupada | Checklist | **desactivado** | suavizado adicional sobre la línea directa de grupo |
| Método de suavizado | Dropdown | Media móvil (ventana temporal) | alterna la implementación reutilizada de `viz/smoothing.py` |
| Ventana de suavizado | Input numérico | 5 min (o puntos, según método) | intensidad del suavizado |
| Cortar la línea en huecos mayores a | Input numérico | vacío = automático | umbral de corte de huecos |

Los tres últimos controles se atenúan (`disabled`) cuando ambos suavizados están
apagados. "Mostrar eventos" se deshabilita y cambia su etiqueta cuando el dataset
cargado no tiene eventos.

## 7. Verificación

- `pytest tests/ -q`: **258 pruebas en verde** (214 preexistentes + 44 nuevas):
  `tests/test_smoothing.py` (15, módulo de suavizado puro), `tests/test_graph_metric.py`
  (8, gráfica #3 sin depender de Dash), `tests/test_graph_timeseries.py` (+2 casos:
  `show_events` y `uirevision`), `tests/test_sensor_window_callbacks.py` (19, los
  callbacks reales registrados en `app.callback_map`, contra el fixture
  `synthetic_hdf5` de `tests/conftest.py`).
- `mypy core data metrics cache ui viz utils`: mismos 18 avisos preexistentes de
  stubs faltantes de terceros (h5py/scipy/plotly/joblib); ningún error nuevo.
- **Cobertura específica del mecanismo `dash.Patch`** (el punto más delicado de la
  Tarea 1, criterio de aceptación 2): `tests/test_sensor_window_callbacks.py`
  invoca `_on_toggle_events` directamente vía `app.callback_map[...]["callback"].
  __wrapped__` (la función pura que Dash envuelve con `functools.wraps` antes de
  exigir el contexto de request HTTP) e inspecciona `Patch.to_plotly_json()` para
  confirmar que la única operación es `Assign` sobre `["layout", "shapes"]`, con
  `value=[]` al ocultar y `value=<shapes precalculadas>` al mostrar — sin tocar
  ningún otro campo de la figura, y cubriendo también el caso de múltiples gráficas
  de métrica simultáneas vía `ALL`. La misma técnica cubre `event-shapes` (una shape
  por evento, vacío sin dataset), `show-events.options` (deshabilitado sin eventos),
  `graph-timeseries.figure` y `metrics-graphs-container.children` (`uirevision`
  estable, régimen puntual nunca conecta puntos crudos, régimen de grupo siempre los
  une, `smooth-grupo` apagado por defecto no agrega línea de tendencia, ventana de
  agrupamiento inválida omite la gráfica en silencio) y los dos callbacks de
  `disabled`/etiqueta dinámica del bloque de suavizado.
- Un primer intento de verificación headless contra el dataset real
  `med_5_ago_3.hdf5` (invocando los callbacks fuera de pytest) quedó colgado: al usar
  el `AppState` global por defecto (`warmup_on_load=True`) sobre 33 058 señales
  reales, disparó el precalentamiento de caché en un hilo daemon compitiendo por CPU
  con el cómputo en primer plano. Se abandonó ese script en favor de la suite de
  pytest anterior, más rápida (3 s los 19 casos nuevos), determinista y que queda
  como cobertura permanente del repositorio en lugar de un script de un solo uso.
- **No se pudo hacer verificación visual en navegador real** (la extensión Chrome no
  está conectada en este entorno de ejecución) — sustituida por la cobertura
  automatizada anterior, que ejercita las mismas funciones que Dash invoca en
  producción con los mismos argumentos posicionales.
