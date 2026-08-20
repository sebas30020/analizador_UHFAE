# Esquema de los archivos Keysight en memoria segmentada (`ruido/test-*.h5`)

**Ruta de ejemplo:** `D:\data\data\main\ruido\test-1.h5` (7 archivos de ejemplo, 6,2–126 MB)
**Formato:** HDF5, exportado directamente por el osciloscopio Keysight en modo trigger +
memoria segmentada (antena UHF)
**Rama:** `lectura_keysight` — reader: [`data/readers/keysight_reader.py`](../data/readers/keysight_reader.py)

Cada archivo es una única adquisición en memoria segmentada: un disparo del osciloscopio
por descarga detectada, todos guardados en el mismo archivo. No tiene chunks, ambientales
ni eventos — es un formato mucho más simple que `med_5_ago_3.hdf5`.

---

## 1. Estructura jerárquica

```
test-1.h5
├── FileType/
│     └── KeysightH5FileType     |S40   = b"Keysight Waveform"   (firma de reconocimiento)
├── Frame/
│     └── TheFrame               struct = (Model, Serial, Date)
└── Waveforms/                   attrs: NumWaveforms
      └── "Channel 4"/           (nombre de canal, variable según el montaje)
            attrs: NumSegments, NumPoints, XInc, XOrg, YInc, YOrg, YDispRange,
                   YDispOrigin, YReference, XUnits, YUnits, MaxBandwidth, WaveformType, Count
            ├── "Channel 4 Seg1Data"      int16 (NumPoints,)   attrs: SegmentedTimeTag, ...
            ├── "Channel 4 Seg2Data"      int16 (NumPoints,)   attrs: SegmentedTimeTag, ...
            └── ...                                            (un dataset por segmento/disparo)
```

**Trampa verificada, no cosmética:** el orden alfabético de los datasets de segmento
**no** es el cronológico — `"...Seg100Data"` ordena alfabéticamente antes que
`"...Seg2Data"`. El reader extrae el número del nombre (`Seg(\d+)Data`) y ordena por él
explícitamente; nunca confía en el orden que entrega `h5py`.

---

## 2. Atributos

### 2.1 `Frame/TheFrame` (registro, no grupo)

| Campo | Tipo | Ejemplo | Descripción |
|---|---|---|---|
| `Model` | bytes | `b"DSOS804A"` | Modelo del osciloscopio. |
| `Serial` | bytes | `b"MY60060103"` | Número de serie del instrumento. |
| `Date` | bytes | `b"19-Aug-2026 16:03:40"` | Marca de tiempo del *frame* guardado (`%d-%b-%Y %H:%M:%S`, **sin zona horaria** — se interpreta como hora local de la máquina de adquisición). |

### 2.2 `Waveforms` (grupo raíz de canales)

| Atributo | Descripción |
|---|---|
| `NumWaveforms` | Total de formas de onda en el archivo (segmentos × canales). |

### 2.3 Cada canal (p. ej. `"Channel 4"`)

| Atributo | Valor típico | Descripción |
|---|---|---|
| `NumPoints` | `20000` | Muestras por segmento. |
| `NumSegments` | 144–2931 (varía por archivo) | Número de disparos capturados. |
| `XInc` | `5e-11` s | Paso de muestreo → `fs = 1/XInc` = **20 GS/s**. |
| `XOrg` | ≈ `-1.97e-7` s | Origen del eje de tiempo intra-segmento (pretrigger). |
| `YInc` | `1.3174e-5` V/cuenta | Escala del ADC. |
| `YOrg` | `0.01328` V | Offset del ADC. Conversión: **`v = raw·YInc + YOrg`**. |
| `YDispRange` | `0.8` V | Rango vertical configurado en el osciloscopio (fondo de escala de pantalla). |
| `MaxBandwidth` | `2.1008e9` Hz | Ancho de banda del canal/sonda. |
| `XUnits` / `YUnits` | `b"Second"` / `b"Volt"` | Unidades declaradas por el instrumento. |

Todos los archivos de ejemplo comparten `XInc`, `NumPoints`, `YInc`, `YOrg`,
`YDispRange` — solo cambian `NumSegments` y la duración total de la adquisición.

### 2.4 Cada segmento (`"<canal> Seg<N>Data"`, `N` desde 1)

| Dato | Descripción |
|---|---|
| dataset | `int16 (NumPoints,)` — muestra cruda del ADC, **sin escalar**. |
| `SegmentedTimeTag` (attr) | Timestamp del segmento **relativo al primero** (`Seg1` → `0.0`), en segundos. Monótono creciente con el número de segmento en todos los archivos verificados. |
| `RawNumPts`, `StartIndex` (attr) | Metadatos internos del instrumento; no usados por el reader. |

No hay nivel de disparo (`trigger`) por segmento en este formato — a diferencia de
`med_5_ago_3.hdf5`, el osciloscopio no lo exporta.

---

## 3. Estadísticas verificadas (7 archivos de ejemplo)

| Archivo | Segmentos | Duración total | Δt mediana entre disparos |
|---|---|---|---|
| test-1.h5 | 144 | 82,91 s | 0,307 s |
| test-2.h5 | 344 | 91,49 s | 0,101 s |
| test-6.h5 | 2931 | 252,04 s | 5 µs (ráfaga) |
| test-7.h5 | 296 | 42,80 s | 0,187 ms |

El Δt mínimo observado (~4 µs en `test-6.h5`) es el tiempo de rearme del osciloscopio en
memoria segmentada, no una propiedad física de la descarga — límite del instrumento, no
del análisis.

---

## 4. Mapeo a `core.models.SignalBlock` (decisiones tomadas, ver `archivos_md/PLAN_LECTURA_KEYSIGHT.md` §2)

| Campo de `SignalBlock` | Origen en este formato |
|---|---|
| `data` | `raw·YInc + YOrg` (voltios), por segmento, ordenado por número extraído del nombre |
| `timestamps` | `epoch(Frame.Date) + SegmentedTimeTag` (o `SegmentedTimeTag` puro si no hay `Frame`) |
| `trigger` | `0.0` para todas las señales — el formato no lo registra (`SensorConfig.has_trigger_metadata = False` para `UHF_KS`; la UI muestra "no registrado") |
| `vrange` | `YDispRange / 2` — **media** escala vertical, no el fondo de escala completo (decisión del usuario) |

`fs_hz`/`n_samples` efectivos se leen del archivo (`XInc`, `NumPoints`) vía
`KeysightSegmentedReader.sensor_config_overrides()`, no del valor nominal de
`config/sensors.yaml` — dos adquisiciones con ventanas temporales distintas se leen
correctamente sin editar el YAML.

Si un archivo trae más de un canal en `Waveforms`, se usa el primero en orden alfabético
y se registra un aviso (`logging.warning`); no se pregunta ni se falla.
