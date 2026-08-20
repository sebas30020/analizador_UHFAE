# Plan — Lectura de bases de datos Keysight (memoria segmentada)

Rama: `lectura_keysight`. Objetivo: que el analizador lea **también** los archivos
`.h5` del osciloscopio Keysight en memoria segmentada
(`D:\data\data\main\ruido\test-*.h5`), con **todas** las funcionalidades que hoy existen
para `med_5_ago_3.hdf5` — gráficas #1/#2/#3, navegación por clic, filtrado con
deshacer/rehacer, métricas puntuales y de grupo, línea de referencia, suavizado — y sin
tocar en absoluto el comportamiento actual sobre el formato antiguo.

---

## 1. El formato nuevo, verificado sobre los 7 archivos de `ruido/`

```
test-1.h5
├── FileType/KeysightH5FileType     = b"Keysight Waveform"     ← firma para el sniffing
├── Frame/TheFrame                  = (Model=b"DSOS804A", Serial=b"MY60060103",
│                                      Date=b"19-Aug-2026 16:03:40")
└── Waveforms/                      attrs: NumWaveforms = 144
      └── "Channel 4"/              attrs: NumSegments=144, NumPoints=20000,
            │                              XInc=5e-11, XOrg=-1.9696e-07,
            │                              YInc=1.31737e-05, YOrg=0.0132791,
            │                              YDispRange=0.8, YUnits=b"Volt",
            │                              MaxBandwidth=2.1008e9
            ├── "Channel 4 Seg1Data"     int16 (20000,)  attrs: SegmentedTimeTag=0.0, ...
            ├── "Channel 4 Seg2Data"     int16 (20000,)  attrs: SegmentedTimeTag=0.4747, ...
            └── ...                                      (un dataset por segmento)
```

Hechos comprobados (los 7 archivos comparten `XInc`, `NumPoints`, `YInc`, `YOrg`,
`YDispRange`; solo cambia el número de segmentos y la duración):

| | valor |
|---|---|
| fs | `1/XInc` = **20 GS/s** |
| muestras por traza | **20 000** → ventana de **1 µs** (igual duración que el UHF actual, 6,7× más resolución) |
| amplitud | `int16` crudo → voltios con `v = raw·YInc + YOrg` |
| nº de señales | 144 (test-1) … 2931 (test-6) |
| duración de la adquisición | 42 s (test-7) … 252 s (test-6) |
| separación entre disparos | mediana 5 µs (test-6, ráfaga) … 0,31 s (test-1) |
| ambientales / eventos | **no existen** en este formato |
| trigger por señal | **no se registra** en el archivo |

**Trampas confirmadas del formato** (cada una tiene su prueba en §6):

1. El orden alfabético de los datasets **no** es el cronológico: `Seg100Data` aparece
   antes que `Seg2Data`. Hay que ordenar por el número de segmento extraído del nombre, y
   además ordenar por timestamp en la ingesta (el invariante de `ingest_sensor` ya lo
   cubre, pero el reader no debe entregar basura confiando en `sorted(keys())`).
2. `SegmentedTimeTag` es **relativo** al primer segmento (empieza en 0,0), no un UNIX
   absoluto.
3. Los datos son enteros con escala; leerlos sin aplicar `YInc`/`YOrg` daría métricas en
   códigos de ADC, no en voltios.

Este esquema se documentará aparte, con el mismo formato que
`archivos_md/esquema_med_5_ago_3.md`, en `archivos_md/esquema_keysight_h5.md`.

---

## 2. Decisiones tomadas (cerradas con el usuario)

| # | Decisión | Consecuencia |
|---|---|---|
| D1 | **Tercer sensor `UHF_KS`**, con su propia entrada en `config/sensors.yaml` y su ruta `/sensor/UHF_KS` | Es la vía de extensión que ya documenta `docs/ARQUITECTURA.md` §8.2. Cada perfil sigue diciendo la verdad sobre su cadena de adquisición. |
| D2 | **`vrange = YDispRange / 2 = 0,4 V`** (media escala vertical del osciloscopio) | Normalización `x/0,4`. Se deriva del atributo del archivo, no se cablea el 0,4: si un archivo trae otro `YDispRange`, se divide por su mitad. |
| D3 | **`freq_limit_hz = 1 GHz`** | Recorte espectral de `F_eq`/`F_aprox`. Con fs=20 GS/s y 20 000 muestras, la FFT deja ~1000 bins tras el recorte. |
| D4 | **Primer canal, avisando** | Se ingiere el primer canal en orden y se deja constancia (log + etiqueta de la interfaz). Con varios canales no se falla ni se pregunta. |

Decisiones derivadas que **no** se preguntaron por no cambiar el trabajo, pero que quedan
documentadas por ser observables:

- **D5 — Timestamp absoluto** = `epoch(Frame.Date, hora local) + SegmentedTimeTag`.
  `Frame.Date` no lleva zona horaria; se interpreta como hora local de la máquina de
  adquisición. Las gráficas #1 y #3 usan minutos transcurridos desde `t0`
  (`ui/state.py::compute_t0`), así que **ningún gráfico depende de este origen**; solo la
  etiqueta de reloj del panel de metadatos, que además puede quedar desplazada respecto al
  instante real de disparo porque `Frame.Date` es la marca del *frame* guardado, no del
  primer segmento.
- **D6 — Trigger no disponible.** El formato no lo registra. Se ingiere `trigger = 0.0`
  (finito, para no marcar todas las señales inválidas en `compute_valid_mask`) y el panel
  de metadatos mostrará «Trigger: no registrado» para los sensores cuyo perfil declare
  `has_trigger_metadata: false`. No se inventa un número que parezca medido.

---

## 3. Qué NO se toca

Restricción dura del trabajo: el camino de `med_5_ago_3.hdf5` debe quedar
bit-a-bit equivalente. En concreto:

- Ningún cambio en `metrics/` (motor, registro, ninguna métrica individual). El motor ya
  lee `fs_hz`, `freq_limit_hz`, `n_samples` y el presupuesto de bloque desde
  `SensorConfig`; el sensor nuevo entra por ahí sin tocar una línea de cálculo.
- Ningún cambio en `core/normalization.py`. Se sigue aplicando la regla única
  `x_norm = x_raw / vrange`, versionada — el sensor nuevo solo aporta un `vrange`
  distinto, que es exactamente para lo que existe el campo.
- Ningún cambio en `core/grouping.py`, `cache/keys.py` ni el esquema de caché.
- Ninguna subida de versión de métrica ni de normalización: nada de lo que ya está
  calculado cambia de valor, así que invalidar el caché existente sería gratuito.

**Nota sobre el invariante de clave de caché** (`CLAUDE.md`): `fs_hz`/`n_samples` no
entran en la clave y ahora pasan a variar *por archivo* (§4.3). No hay riesgo de servir
un resultado viejo porque `dataset_id` = `ruta:tamaño:mtime_ns` identifica el archivo
1 a 1: dos archivos con distinta configuración de adquisición nunca comparten clave. Esto
queda escrito en el docstring de `cache/keys.py` para que no se lea como un descuido.

---

## 4. Cambios por capa

### 4.1 `data/readers/keysight_reader.py` (nuevo) — el grueso del trabajo

`class KeysightSegmentedReader(OriginReader)`, solo lectura (`mode="r"`), misma disciplina
que `HDF5Reader`: es la única capa que sabe cómo es el archivo por dentro.

- `list_experiments()` → un único pseudo-experimento por archivo, nombrado con
  `Model + Date` del `Frame` (p. ej. `"DSOS804A 19-Aug-2026 16:03:40"`). Mantiene el
  supuesto «un archivo = un experimento» que ya rige en el formato antiguo.
- `get_experiment_attrs()` → modelo, serie, fecha, canal elegido, nº de segmentos,
  `fs_hz`, `n_samples`, `YInc`, `YOrg`, `YDispRange`, `MaxBandwidth`. Es lo que alimenta la
  etiqueta informativa de la interfaz y el aviso de canal (D4).
- `iter_signal_batches(exp, "UHF_KS")` → lotes de `RawSignalBatch` de tamaño
  `SensorConfig.block_n_signals` (mismo presupuesto de E/S que el resto; con 20 000
  muestras son ~838 señales por lote):
  - segmentos ordenados por el **número** extraído de `Seg(\d+)Data`, no por `sorted()`;
  - `data` = `raw.astype(float32) * YInc + YOrg` (voltios);
  - `timestamps` = `epoch(Frame.Date) + SegmentedTimeTag` (D5);
  - `trigger` = `0.0` (D6);
  - `vrange` = `YDispRange / 2` (D2).
  - Para cualquier otro sensor: no entrega nada.
- `get_environmental()` / `get_events()` → series vacías (el formato no las tiene).
- `dataset_id` → mismo esquema `ruta:tamaño:mtime_ns`; se extrae a un helper compartido
  para no duplicarlo entre los dos readers.
- `available_sensors()` → `["UHF_KS"]` (ver §4.2).
- `sensor_config_overrides()` → `{"fs_hz": 1/XInc, "n_samples": NumPoints}` (ver §4.3).
- Canal: se elige el primero en orden; si `Waveforms` tiene más de uno, se registra
  `logging.warning` y el canal elegido queda en los atributos del experimento (D4).

### 4.2 `data/readers/base.py` y `factory.py` — elegir el lector correcto

- `OriginReader` gana un método abstracto `available_sensors() -> list[SensorName]`:
  qué sensores ofrece **este** origen. `HDF5Reader` devuelve `["UHF", "AE"]`;
  `KeysightSegmentedReader`, `["UHF_KS"]`. Sin esto, cargar un Keysight intentaría ingerir
  UHF y AE (bloques vacíos inútiles) y viceversa.
- `data/readers/factory.py` (nuevo): `open_reader(path) -> OriginReader`, por *sniffing*
  del contenido, no por extensión (ambos formatos usan `.h5`/`.hdf5` indistintamente):
  1. ¿existe `FileType/KeysightH5FileType`? → `KeysightSegmentedReader`;
  2. ¿hay un grupo raíz con subgrupos `chunk_*`? → `HDF5Reader`;
  3. si no → `ValueError` con un mensaje que diga qué se esperaba encontrar.

### 4.3 Perfil de sensor por dataset

`config/sensors.yaml` gana la entrada `UHF_KS` (`hdf5_group: "Waveforms"`, `fs_hz: 2.0e10`,
`n_samples: 20000`, `freq_limit_hz: 1.0e9`, `axis_unit: "µs"`, `axis_scale: 1.0e6`,
`target_block_bytes: 67108864`) más dos campos nuevos, con valor por defecto para los
sensores existentes:

- `decimate_full_view` (UHF `false`, AE `true`, UHF_KS `true`) — sustituye al literal
  `sensor_config.name == "AE"` de `ui/components/graph_signal.py:47`. Con 20 000 muestras
  la gráfica #2 **debe** diezmar en vista completa, igual que AE; sin este campo habría que
  cablear otro nombre de sensor en la capa de presentación.
- `has_trigger_metadata` (UHF/AE `true`, UHF_KS `false`) — D6.

Además, `OriginReader.sensor_config_overrides(experiment, sensor) -> dict` (por defecto
`{}`) permite que el YAML aporte lo nominal y el archivo lo efectivo: `ui/state.py` aplica
`dataclasses.replace(config, **overrides)` al cargar. Así una adquisición futura con otra
ventana temporal (p. ej. 10 µs → `NumPoints` distinto) se lee sin editar el YAML, y el
invariante «ninguna dimensión temporal cableada» se mantiene. Si los valores efectivos
difieren de los nominales, se registra una línea de log.

`SensorName` pasa a `Literal["UHF", "AE", "UHF_KS"]` (`core/models.py`) y
`data/storage.py::_ALL_SENSORS` deja de ser una tupla literal para derivarse de los
perfiles cargados.

### 4.4 `ui/state.py` — carga

`load_dataset` deja de instanciar `HDF5Reader` a mano:

```
with open_reader(path) as reader:
    experiment = reader.list_experiments()[0]
    sensores = [s for s in sensor_configs if s in reader.available_sensors()]
    result = ingest_experiment(reader, experiment, sensores)
    sensor_configs = {s: replace(sensor_configs[s], **reader.sensor_config_overrides(experiment, s)) ...}
```

El resto (`t0`, máscaras, índice activo, `dataset_version`, precalentamiento de caché) ya
es genérico por sensor y no cambia. `compute_t0` seguirá funcionando con ambientales y
eventos vacíos (solo aporta el mínimo de los timestamps de señal).

### 4.5 Presentación

- `ui/callbacks/helpers.py`: `VALID_SENSORS = ("UHF", "AE", "UHF_KS")`. Es la única lista
  cerrada de sensores que queda en el proyecto (ya señalada en `ARQUITECTURA.md` §8.2).
- `ui/components/sensor_window.py`: los enlaces a ventanas gemelas pasan a ser dos. Cuando
  el dataset cargado no tiene señales del sensor de esa ventana, se muestra un aviso breve
  («El archivo cargado no contiene señales de este sensor») en vez de tres gráficas vacías
  sin explicación.
- `ui/components/graph_signal.py`: la condición de diezmado pasa a leer
  `sensor_config.decimate_full_view`. Cero cambio de comportamiento para UHF y AE.
- `ui/components/metadata_panel.py`: «Trigger: no registrado» cuando el perfil declara
  `has_trigger_metadata: false` (D6). El resto del panel (índice, timestamp, escala
  vertical, aviso de diezmado) es idéntico.
- `ui/components/control_panel.py` *(opcional, §8)*: valor por defecto de agrupamiento y de
  la línea de referencia por perfil de sensor. Los defaults actuales (grupo de 60 s,
  referencia de 10 min) están calibrados para un experimento de ~6 h; sobre una adquisición
  de 42–250 s dan 1–4 grupos y una referencia que abarca todo el archivo. Funciona, pero
  obliga a ajustar dos campos a mano en cada carga.

**Nada de esto toca los callbacks**: navegación por clic en #1 y #3, filtrado con
deshacer/rehacer, apilado de gráficas #3, línea de referencia y suavizado ya resuelven el
sensor en tiempo de render leyendo el Store `page-sensor`. Esa es exactamente la propiedad
que el diseño prometía y que aquí se cobra.

---

## 5. Orden de trabajo

| Fase | Contenido | Se puede probar sola |
|---|---|---|
| **F0** | Rama `lectura_keysight` (hecha) + `archivos_md/esquema_keysight_h5.md` | — |
| **F1** | Capa de datos: reader nuevo, `factory`, `available_sensors`, `sensor_config_overrides`, entrada YAML, `SensorName`, `storage` | sí: pruebas unitarias con fixture sintético |
| **F2** | `ui/state.py` + ingesta por sensores disponibles + perfil efectivo por dataset | sí: `test_state.py` carga un fixture Keysight |
| **F3** | Presentación: `VALID_SENSORS`, ventana informativa, diezmado por config, panel de metadatos | sí: servidor de desarrollo contra `test-1.h5` |
| **F4** | Verificación funcional completa sobre datos reales (`test-1.h5` y `test-6.h5`) + medición de ingesta/cálculo | sí |
| **F5** | Documentación: `README.md`, `docs/ARQUITECTURA.md`, `CLAUDE.md`, `archivos_md/LECTURA_KEYSIGHT_ENTREGA.md` | — |

F1 y F2 son donde está el riesgo; F3 debería ser mecánica si F1/F2 quedaron bien.

---

## 6. Pruebas

**Fixture sintético nuevo** en `tests/conftest.py` (`synthetic_keysight_h5`), en la misma
línea que el existente: pequeño, rápido, y con los casos borde puestos a propósito donde el
archivo real no los garantiza —

- segmentos numerados de forma que el orden alfabético **contradiga** el cronológico
  (`Seg2` vs `Seg10`);
- un archivo con **dos** canales (para D4);
- valores `int16` conocidos, para verificar la conversión a voltios exacta;
- `Frame.Date` presente y otro fixture sin él (fallback de timestamp).

**Pruebas nuevas**

- `tests/test_keysight_reader.py`: conversión `int16 → V`; orden cronológico correcto pese
  al orden alfabético; `timestamps` absolutos y monótonos; `vrange == YDispRange/2`;
  `trigger` finito; `available_sensors()`; lotes del tamaño esperado; ambientales y eventos
  vacíos; aviso con dos canales; `dataset_id` estable entre aperturas.
- `tests/test_reader_factory.py`: reconoce ambos formatos y rechaza un `.h5` ajeno con
  mensaje claro.
- `tests/test_state.py` (ampliación): cargar un Keysight deja bloque `UHF_KS`, máscara e
  índice reseteados, y `SensorConfig` efectivo con `fs_hz`/`n_samples` del archivo.
- `tests/test_decimation.py` o `test_graph_*`: `UHF_KS` diezma en vista completa; UHF sigue
  sin diezmar (prueba de no regresión del literal que se elimina).
- `tests/test_integration_real_data.py` (ampliación): caso contra
  `D:\data\data\main\ruido\test-1.h5` con cifras duras verificadas hoy — 144 señales ×
  20 000 muestras, `vrange = 0,4`, duración 82,91 s — y `@skipif` si el archivo no está,
  igual que el caso existente.

**No regresión**: la suite completa (`pytest tests/ -q`) debe pasar sin modificar ninguna
prueba existente salvo las que hoy afirman `VALID_SENSORS == ("UHF","AE")` o el literal de
diezmado. Se mantiene la prueba de integración contra `med_5_ago_3.hdf5` intacta.
`mypy core data metrics cache ui viz utils` limpio.

**Verificación manual** (F4), con `scripts/run_dev_server.py` y `test-1.h5` / `test-6.h5`,
recorriendo la lista de aceptación de §7 y guardando capturas para el documento de entrega.

---

## 7. Criterios de aceptación

Sobre `D:\data\data\main\ruido\test-1.h5` (y repetido en `test-6.h5`, el más pesado):

1. El selector de base de datos abre el archivo sin errores y la etiqueta muestra la ruta.
2. **Gráfica #1**: envolvente min/max de las 144 señales en el eje de minutos transcurridos,
   una señal por segmento en su instante real. Sin ambientales ni eventos (ejes secundarios
   vacíos, sin excepción ni gráfica en blanco).
3. **Gráfica #2**: la señal seleccionada, eje en µs (0–1 µs), diezmada en vista completa y a
   resolución total al hacer zoom, con el aviso correspondiente; interruptor cruda/normalizada
   funcionando (normalizada = `v/0,4`).
4. **Gráfica #3**: métricas puntuales (Vmax, RMS, kurtosis, F_eq…) y de grupo (reducción e
   intrínsecas) calculadas y dibujadas, con caché frío y caliente.
5. Clic en la gráfica #1 → la #2 muestra esa señal. Clic en un punto de la #3 → ídem.
6. Navegación anterior/siguiente/índice y campo de comparación superpuesta.
7. Filtrado por selección con deshacer/rehacer/restablecer y contador de señales activas;
   las gráficas #1 y #3 se refrescan y la máscara de `UHF_KS` es independiente de UHF/AE.
8. Línea de referencia y suavizado operativos en la gráfica #3.
9. Cargar después `med_5_ago_3.hdf5` en la misma sesión funciona exactamente como antes, con
   sus ventanas UHF y AE.

---

## 8. Riesgos y puntos abiertos

1. **Resolución del timestamp absoluto.** Un UNIX epoch en `float64` tiene un ulp de
   ~0,24 µs. En `test-6.h5` la mediana entre disparos es de 5 µs, así que `delta_t`,
   `log_delta_t` y `tasa_rafagas` arrastran ahí una cuantización del orden del 5 %. Es una
   limitación del modelo canónico (`SignalBlock.timestamps` absolutos), no de este formato;
   se documenta. Si el análisis de ráfagas a escala de µs resulta central, la solución sería
   un vector de tiempo relativo de alta precisión en `SignalBlock` — cambio invasivo, fuera
   de alcance salvo que se pida.
2. **Tiempo muerto del osciloscopio.** En memoria segmentada, el mínimo Δt observado (~4 µs)
   es el rearme del instrumento, no una propiedad de la descarga: las tasas de pulsos en
   ráfaga están limitadas por el equipo. Es una advertencia de interpretación para el
   documento de entrega, no un problema de software.
3. **Ambientales y eventos vacíos.** El código los soporta por construcción (hay chunks sin
   ellos en el formato antiguo), pero un dataset con la serie **completamente** vacía no está
   ejercitado hoy. Es lo primero a verificar en F3.
4. **Memoria en el archivo grande.** `test-6.h5` son 2931 × 20 000 → 234 MB en `float32`, y
   `ingest_sensor` reordena con una copia (~470 MB de pico). Entra holgado en el supuesto de
   ≥16 GB, pero se medirá en F4; la lectura pura de test-1 tarda 16 ms, así que la ingesta no
   pinta como cuello de botella.
5. **Defaults de agrupamiento y línea de referencia** (§4.5, opcional). Decisión pendiente:
   dejarlos globales y documentarlo, o hacerlos por perfil de sensor. Recomiendo lo segundo
   (dos claves más en el YAML, cero lógica nueva), pero no bloquea nada.

---

## 9. Documentación a actualizar al cerrar

- `archivos_md/esquema_keysight_h5.md` — esquema del formato nuevo (F0).
- `docs/ARQUITECTURA.md` — §8.2 (ya hay un tercer sensor de verdad) y §8.3 (la prueba de
  fuego «cambiar el motor de origen» pasa de hipótesis a hecho, con el `factory` como pieza
  nueva).
- `README.md` — qué formatos se pueden abrir y cómo se reconocen.
- `CLAUDE.md` — la fila de invariantes sobre normalización y clave de caché gana la nota de
  §3 (perfil efectivo por dataset ligado a `dataset_id`).
- `archivos_md/LECTURA_KEYSIGHT_ENTREGA.md` — entrega, en la línea de los `FASE*_ENTREGA.md`.
