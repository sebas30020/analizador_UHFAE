# Exportación de datos filtrados (entrega)

Rama `claude/filtrar-data-base-dq1u4g`. Botón "Exportar datos filtrados…" en el panel de
control: escribe a un archivo `.hdf5` nuevo las señales **resultantes** (activas tras el
filtrado interactivo, Fase 6) y las **filtradas** (excluidas) de todos los sensores del
dataset cargado, en particiones separadas dentro del mismo archivo, conservando
metadatos, ambientales y eventos. El archivo resultante se puede reabrir con
"Seleccionar base de datos…" como un tercer formato de origen más.

## 1. Decisiones (confirmadas con el usuario)

1. **Dos conjuntos separados, no uno con columna de estado**: "resultantes" y
   "filtradas" son grupos HDF5 distintos dentro del mismo archivo, cada uno con su
   propia matriz de trazas crudas — auditar qué se descartó no exige leer una columna
   booleana aparte.
2. **Formato**: HDF5, reutilizando el mismo esquema de chunking+gzip de
   `data/storage.py::write_canonical` — único formato viable para trazas crudas de
   `UHF_KS` (~1,6 GB) sin salirse de las dependencias ya pinneadas.
3. **Contenido**: trazas crudas + metadatos por señal (timestamps, trigger, vrange,
   valid_mask, minmax) + ambientales/eventos del experimento. Las métricas ya calculadas
   **no** se exportan: son un derivado versionado que se reconstruye del caché al
   reabrir, exportarlas las duplicaría sin necesidad.
4. **Reapertura limpia y de las tres formas**: el archivo exportado se puede volver a
   abrir con solo las señales resultantes, solo las filtradas, o ambas recombinadas (el
   conjunto original completo).
5. **Disparo**: botón en el panel lateral, con el diálogo nativo "Guardar como" — mismo
   patrón que "Seleccionar base de datos…".

## 2. Qué cambió

- **`data/export.py`** (nuevo): `export_filtered(...)` — capa de datos pura, sin Dash ni
  caché. Escribe `/resultantes/<SENSOR>/` y `/filtradas/<SENSOR>/` con la partición de
  cada sensor según su máscara activa, más `/environmental/` y `/events/`. Cada partición
  guarda también `source_index`: el índice cronológico global original de cada señal, así
  que partir el conjunto no pierde trazabilidad contra el dataset de origen. La firma de
  reconocimiento (`file_type` en los atributos raíz) es el mismo mecanismo de *sniffing*
  por contenido que ya usan los otros dos formatos, no una extensión de archivo.
- **`data/readers/filtered_export_reader.py`** (nuevo): `FilteredExportReader`, una
  implementación más de `OriginReader` (docs/ARQUITECTURA.md §8.3, prueba de fuego #3:
  "cambiar el motor de origen no toca nada aguas arriba de la capa de datos"). Acepta
  `partition` (`"resultantes"` / `"filtradas"` / `"ambas"`) al abrir; con `"ambas"` emite
  los lotes de los dos grupos y deja que `data/ingest.py::ingest_sensor` los reordene por
  timestamp, reconstruyendo el conjunto original sin código adicional.
- **`data/readers/factory.py`**: `open_reader` gana el *sniffing* del nuevo formato y un
  parámetro `partition` (inerte para los otros dos formatos).
- **`ui/state.py`**: `AppState.load_dataset` acepta `partition`; `ExportSnapshot` +
  `AppState.export_snapshot()` toman una copia barata (máscaras copiadas, bloques por
  referencia — un dataset cargado nunca se muta en sitio) bajo el lock del proceso, para
  que la escritura a disco no lo retenga. `start_export_status`/`finish_export_status`/
  `export_status` publican el progreso para que un hilo de fondo se lo comunique al
  callback de Dash, que no puede escribir directamente en un `Output` desde otro hilo.
- **`ui/callbacks/sensor_window_callbacks.py`**: botón → diálogo "Guardar como" (tkinter,
  igual que `_open_file_dialog`) → hilo daemon (`_launch_export_thread`, mismo patrón que
  `cache/warmup.py::start_background_warmup`) → `data.export.export_filtered`. Un
  `dcc.Interval` deshabilitado por defecto sondea `AppState.export_status` mientras dura
  la escritura, y se apaga solo al terminar.
- **`ui/components/control_panel.py`**: botón "Exportar datos filtrados…" + etiqueta de
  estado junto a los controles de filtrado; selector "Partición visible"
  (`load-partition`) junto a "Seleccionar base de datos…".
- **`ui/components/sensor_window.py`**: `dcc.Interval` de sondeo del estado de export.
- **`ui/callbacks/helpers.py`**: `default_export_filename`, `format_export_starting_message`,
  `format_export_done_message`, `format_export_error_message` — lógica pura, testeable
  sin Dash, igual que el resto del módulo.

Cero cambios en `metrics/`, `cache/`, `core/normalization.py`, `core/grouping.py` ni en
los dos readers existentes — el caché nunca interviene en la exportación (docs/
ARQUITECTURA.md §5: "el caché almacena resultados sobre el conjunto completo de señales,
nunca un subconjunto filtrado" — esta exportación tampoco lo lee ni lo escribe, solo
particiona arrays ya en memoria).

## 2.1 Corrección: el selector de partición no hacía nada (encontrado en uso)

En la primera versión el selector solo se leía como `State` dentro de `_on_select_db`,
es decir **únicamente al pulsar "Seleccionar base de datos…"**. Moverlo con un archivo ya
cargado no disparaba ningún callback: las gráficas seguían mostrando la partición
anterior y no había ninguna señal en pantalla de por qué. El síntoma reportado fue
exacto: *"no pasa absolutamente nada, no cambian las señales, quedan cargadas solo las
que quedan después del filtro"*.

El fallo no era del formato ni del reader — ambos particionaban bien desde el principio,
como probaba `tests/test_export.py` — sino de la interfaz: un control cuya etiqueta
("Partición a cargar") prometía elegir qué se ve, colocado junto al botón de abrir, se
comporta a ojos del usuario como un conmutador de vista, no como un parámetro diferido
hasta la próxima apertura de archivo. Las pruebas no lo detectaron porque cubrían la
lógica pura y el round-trip de datos, no el cableado de Dash.

La corrección:

- **`_on_change_partition`** (`ui/callbacks/sensor_window_callbacks.py`): callback nuevo
  con `Input("load-partition", "value")` que recarga el archivo ya abierto con la
  partición pedida y sube `dataset-version`, lo que repinta todas las gráficas por el
  mecanismo que ya existía. Usa `allow_duplicate=True` porque `dataset-version` y
  `db-path-label` ya son salidas de `_on_select_db`.
- **Recargar es lo correcto, no un atajo**: dos particiones son conjuntos de señales
  distintos, con `dataset_id` distinto (§7), no una vista filtrada del mismo conjunto —
  no se pueden intercambiar en memoria sin releer.
- **`LoadedDataset.partition`**: la partición efectivamente cargada, o `None` si el
  origen no es una exportación filtrada. Es lo que permite que el selector siga siendo
  inerte (silenciosamente) para los otros dos formatos, y que re-seleccionar la
  partición ya visible no dispare una recarga completa
  (`helpers.py::resolve_partition_change`).
- **Retroalimentación visible** (`helpers.py::format_db_path_label`): la etiqueta bajo el
  botón ahora dice qué partición está cargada. Sin eso, elegir una partición vacía es
  indistinguible de "no pasó nada" — que es justamente cómo se veía el fallo.
- **Semilla al abrir pestaña** (`ui/components/sensor_window.py`): el radio se siembra
  con la partición realmente cargada, no con el valor por defecto; si no, una pestaña
  nueva diría "resultantes" mientras se ven las filtradas.

Se renombró la etiqueta a **"Partición visible"**: describe lo que el control hace ahora.

## 3. Rendimiento

- **Escritura por bloques** (`SensorConfig.block_n_signals`, el mismo presupuesto de
  E/S que usa la ingesta): nunca se materializa una copia completa de la matriz filtrada
  en RAM antes de escribirla — con `UHF_KS` completo eso serían ~1,6 GB extra de golpe.
- **Hilo daemon**: la escritura corre fuera del hilo del callback de Dash, que devuelve
  el control de inmediato. Con un único proceso Dash sirviendo todas las pestañas
  (docs/ARQUITECTURA.md §6), bloquear ese hilo durante la exportación habría congelado
  la interfaz para cualquier ventana abierta, no solo la que exporta.
- **Cero contacto con el camino caliente**: no toca `cache/`, no invalida ninguna clave,
  no participa de ningún render de gráfica. El rendimiento habitual del programa es
  idéntico cuando no se exporta.
- Instrumentado con `utils.profiling.stage("export.filtrado", ...)`, como el resto del
  pipeline (`ANALIZADOR_PROFILING=1`).

## 4. Verificación

- **`tests/test_export.py`** (nuevo): la partición escrita coincide exactamente con la
  máscara (fila a fila, no solo en conteo); `"ambas"` reconstruye el conjunto original
  completo; `dataset_id` difiere entre las tres particiones (docs/ARQUITECTURA.md §7:
  no pueden compartir clave de caché); el perfil efectivo por sensor y los
  ambientales/eventos sobreviven el viaje de ida y vuelta; un sensor completamente
  excluido por el filtro exporta una partición "resultantes" vacía sin fallar; el
  *sniffing* en `open_reader` reconoce el nuevo formato.
- **`tests/test_reader_factory.py`** / **`tests/test_ui_callback_helpers.py`**: sniffing
  del formato exportado, las cuatro funciones puras de mensajes/nombre de archivo, y la
  lógica del selector de partición (§2.1): recarga ante un cambio real, inercia con un
  origen sin particiones, inercia al re-seleccionar la ya visible, y la etiqueta con y
  sin partición.
- **414/414 pruebas** de la suite completa pasan (17 nuevas); `mypy core data metrics
  cache ui viz utils` sin errores de tipos reales (mismo ruido preexistente de
  `import-untyped` por falta de *stubs* de `h5py`/`plotly`/`scipy`/`joblib`).
- **Prueba de humo de punta a punta** (fuera de pytest, con `AppState` real): cargar un
  dataset sintético, aplicar un filtro real con `apply_filter`, exportar con
  `export_snapshot` + `export_filtered`, y reabrir las tres particiones con
  `AppState.load_dataset(..., partition=...)` — los conteos de señales por partición
  coinciden exactamente con lo esperado en los tres casos, sin errores de memoria ni de
  ejecución.
