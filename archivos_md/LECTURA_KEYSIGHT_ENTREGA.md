# Lectura de bases de datos Keysight en memoria segmentada (entrega)

Rama `lectura_keysight`. Plan y reconocimiento previo del formato en
[archivos_md/PLAN_LECTURA_KEYSIGHT.md](PLAN_LECTURA_KEYSIGHT.md) — este documento resume
las decisiones tomadas, lo implementado y cómo se verificó, sin repetir lo que ya está ahí.

## 1. Qué se pidió

Leer, además de `med_5_ago_3.hdf5`, archivos `.h5` de osciloscopio Keysight en memoria
segmentada (antena UHF, trigger + memoria segmentada), con **todas** las funcionalidades
ya existentes — gráficas #1/#2/#3, navegación por clic, filtrado con deshacer/rehacer,
métricas puntuales y de grupo — sin alterar en absoluto el comportamiento sobre el
formato antiguo.

## 2. Decisiones cerradas con el usuario antes de implementar

Cuatro ambigüedades reales que cambiaban el diseño se consultaron antes de escribir
código (ver detalle y consecuencias en `PLAN_LECTURA_KEYSIGHT.md` §2):

- **Tercer sensor `UHF_KS`**, no una reutilización de `UHF`. Entrada propia en
  `config/sensors.yaml`, ruta propia `/sensor/UHF_KS`.
- **`vrange = YDispRange / 2`** (media escala vertical del osciloscopio, no el fondo de
  escala completo) — decisión explícita del usuario, distinta de lo que hubiera sido la
  extrapolación directa del criterio de `med_5_ago_3.hdf5`.
- **`freq_limit_hz = 1 GHz`** para las métricas espectrales de `UHF_KS` (el osciloscopio
  llega a 2,1 GHz de ancho de banda; se recorta a la banda útil de PD).
- **Primer canal en orden, con aviso** si un archivo trae más de uno (los 7 archivos de
  ejemplo traen solo uno).

Dos decisiones derivadas, no preguntadas por no cambiar el trabajo pero documentadas por
ser observables: el timestamp absoluto se ancla a `Frame.Date + SegmentedTimeTag` (hora
local, sin zona horaria en el archivo), y `trigger = 0.0` con
`SensorConfig.has_trigger_metadata = False` porque el formato no registra nivel de
disparo por señal — la interfaz dice "no registrado" en vez de mostrar un número que
parezca medido.

## 3. Qué se implementó

### Capa de datos (nueva, sin tocar nada aguas arriba de `metrics/`)

- [`data/readers/keysight_reader.py`](../data/readers/keysight_reader.py):
  `KeysightSegmentedReader`, implementación de `OriginReader` para el esquema
  documentado en [archivos_md/esquema_keysight_h5.md](esquema_keysight_h5.md). Resuelve
  la trampa real del formato (orden alfabético de segmentos ≠ orden cronológico)
  ordenando explícitamente por el número extraído del nombre del dataset, nunca
  confiando en el orden que entrega `h5py`.
- [`data/readers/factory.py`](../data/readers/factory.py): `open_reader(path)` elige el
  lector por *sniffing* de contenido (firma `FileType/KeysightH5FileType` vs. grupo raíz
  con subgrupos `chunk_*`), no por extensión — ambos formatos usan indistintamente
  `.h5`/`.hdf5`.
- `OriginReader` (`data/readers/base.py`) gana dos métodos: `available_sensors()` (qué
  sensores ofrece este origen) y `sensor_config_overrides()` (valores efectivos por
  archivo, con default `{}` — `HDF5Reader` no necesitó implementarlo).
- `core.models.SensorName` pasa a `Literal["UHF", "AE", "UHF_KS"]`; `SensorConfig` gana
  `decimate_full_view`/`has_trigger_metadata`, propiedades del origen físico que antes
  no existían porque solo había un sensor de cada tipo de comportamiento.
- `config/sensors.yaml`: entrada `UHF_KS` (nominal; el reader la sobreescribe por
  archivo) y los dos campos nuevos en las tres entradas.

### `ui/state.py`

`load_dataset` usa `open_reader` en vez de instanciar `HDF5Reader` a mano, ingiere solo
los sensores que `reader.available_sensors()` ofrece, y aplica
`reader.sensor_config_overrides()` sobre el perfil nominal antes de construir el
`LoadedDataset` — una sola vez por carga, no en cada cálculo.

### Presentación (cambios mínimos, tal como preveía el plan)

- `VALID_SENSORS` ahora incluye `UHF_KS`.
- El literal `sensor_config.name == "AE"` que decidía el diezmado de la gráfica #2
  (`ui/components/graph_signal.py`) se reemplazó por `SensorConfig.decimate_full_view`.
- Aviso ("El archivo cargado no contiene señales del sensor X") cuando la ventana de un
  sensor no tiene datos en el dataset cargado — lógica pura en
  `ui/callbacks/helpers.py::resolve_sensor_availability_notice`, sembrada en el layout
  inicial y refrescada por un callback sobre `dataset-version`, mismo patrón que el resto
  de refrescos de la ventana.
- Panel de metadatos: "Trigger: no registrado" cuando `has_trigger_metadata=False`.

**Cero cambios** en `metrics/`, `core/normalization.py`, `core/grouping.py`,
`cache/keys.py` ni en ningún callback de navegación, filtrado, línea de referencia o
suavizado — todos ya resolvían el sensor en tiempo de render, así que el sensor nuevo los
hereda sin código adicional.

## 4. Verificación

- **315 pruebas pasan** (`pytest tests/ -q`), 22 nuevas: `test_keysight_reader.py` (17,
  incluida la trampa de orden alfabético/cronológico y la conversión exacta int16→V),
  `test_reader_factory.py` (3), `test_graph_signal.py` (4, no-regresión del diezmado de
  UHF/AE + comportamiento de `UHF_KS`), y ampliaciones de `test_state.py` (5) y
  `test_models.py`. Una prueba de integración nueva
  (`test_integration_real_data_keysight.py`) corre contra el archivo real
  `D:\data\data\main\ruido\test-1.h5` (144 señales × 20 000 muestras, `vrange=0,4`,
  duración 82,91 s — cifras verificadas manualmente).
- `mypy core data metrics cache ui viz utils`: 0 errores de tipos reales (el conteo de
  "errores" que reporta pasó de 18 a 20 solo por el ruido preexistente de `import-untyped`
  de h5py en los dos archivos nuevos — mismo patrón que ya afectaba a `hdf5_reader.py`,
  `cache/backend.py`, etc.; no hay stubs de h5py instalados en el proyecto).
- **Verificación funcional end-to-end contra el archivo real**: con el servidor de
  desarrollo arriba se confirmó que `/sensor/UHF_KS` renderiza sin errores (consola y
  logs del servidor limpios) y que `/sensor/UHF`/`/sensor/AE` no se vieron afectados. El
  clic real sobre el botón "Seleccionar base de datos…" abre un diálogo nativo de
  `tkinter` que corre en el proceso del servidor — el navegador automatizado no puede
  pilotarlo (es una limitación de la herramienta de verificación, no del programa). Para
  cubrir exactamente esa ruta se ejecutó `state.load_dataset(path)` directamente (la
  misma llamada que el callback hace tras recibir la ruta del diálogo) contra
  `test-1.h5`, y a partir de ahí se ejercitaron las funciones reales de construcción de
  gráficas y estado:
  - Gráfica #1 (`build_timeseries_figure`) y #2 (`build_signal_figure`, confirmando
    diezmado en vista completa con las 20 000 muestras).
  - Panel de metadatos con "Trigger: no registrado".
  - Métrica puntual (`vmax`, 144 valores) y gráfica #3.
  - Agrupamiento por ventana temporal (30 s → 3 grupos), reducción por mediana e
    intrínseca (`tasa_pulsos`).
  - Filtrado: aplicar, deshacer, rehacer, restablecer (conteos exactos verificados en
    cada paso).
  - Navegación por clic (`nearest_index_for_timestamp`).

  Las 15 comprobaciones dieron resultado correcto contra los datos reales.

## 5. Limitaciones documentadas (no bloqueantes, ver `PLAN_LECTURA_KEYSIGHT.md` §8)

- Cuantización de timestamp: con `float64`, delta_t/tasa_rafagas a escala de pocos
  microsegundos (ráfagas como `test-6.h5`) arrastran ~5 % de error por el ulp del
  timestamp UNIX absoluto — limitación del modelo canónico, no de este formato.
- El Δt mínimo observado en memoria segmentada es el rearme del osciloscopio, no física
  de la descarga — advertencia de interpretación, no de software.
- Defaults de agrupamiento (60 s) y línea de referencia (10 min) siguen calibrados para
  un experimento de horas; sobre una adquisición Keysight de 42–250 s dan pocos grupos.
  Funciona, requiere ajustar dos campos a mano — no se hizo por sensor para no ampliar el
  alcance sin pedirlo.
