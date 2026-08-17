# Esquema de la base de datos `med_5_ago_3.hdf5`

**Ruta:** `D:\data\data\main\med_5_ago_3.hdf5`
**Tamaño:** 180.4 MB
**Formato:** HDF5 (compatible con el formato leído por `data_handler.py` del proyecto
`analizador_nuevo`)

Este archivo contiene **un único experimento** ("Test group") con 766 chunks temporales,
señales UHF y de Emisión Acústica (AE), eventos detectados y datos ambientales.

---

## 1. Estructura jerárquica

```
med_5_ago_3.hdf5
└── "Test - 2026-08-07T13-47-09Z"/          [Grupo raíz del experimento]
      ├── chunk_000000/
      ├── chunk_000001/
      ├── ...
      └── chunk_000765/                      (766 chunks en total)
            ├── signals/                     # Sensor UHF (antena)
            │     ├── data          float32  (n_signals, 3000)
            │     ├── timestamps    float64  (n_signals,)
            │     ├── triggers      float64  (n_signals,)
            │     └── vranges       float64  (n_signals,)
            ├── ae_signals/                  # Sensor AE (ultrasonido)
            │     ├── data          float32  (n_ae_signals, 10000)
            │     ├── timestamps    float64  (n_ae_signals,)
            │     ├── triggers      float64  (n_ae_signals,)
            │     └── vranges       float64  (n_ae_signals,)
            ├── events/                      # Eventos detectados (opcional, puede estar vacío)
            │     ├── timestamps    float64  (n_eventos,)
            │     └── type          object   (n_eventos,)   # strings: 'SHOT', 'PA', 'FO'
            └── humidity/                    # Datos ambientales (longitud variable por chunk)
                  ├── humidity      float64  (n_amb,)
                  ├── temperature   float64  (n_amb,)
                  └── timestamps    float64  (n_amb,)
```

Solo hay **un grupo de nivel raíz**: `"Test - 2026-08-07T13-47-09Z"`. No tiene atributos propios
a nivel de archivo (raíz de HDF5 sin atributos).

---

## 2. Atributos por nivel

### 2.1 Grupo del experimento (`Test - 2026-08-07T13-47-09Z`)

| Atributo | Valor | Descripción |
|---|---|---|
| `date` | `2026-08-07T13-47-09Z` | Marca de tiempo de inicio del experimento. |
| `chunk_duration_s` | `10.0` | Duración nominal de cada chunk, en segundos. |
| `initial_dead_time_s` | `300.0` | Tiempo muerto inicial antes de empezar a adquirir (s). |
| `description` | *(placeholder sin completar)* | Campo libre de descripción del experimento — quedó con el texto por defecto, no fue editado por el usuario. |
| `version` | `2` | Versión del esquema/formato de adquisición. |

### 2.2 Cada chunk (`chunk_NNNNNN`)

| Atributo | Tipo | Descripción |
|---|---|---|
| `is_baseline` | bool | Si el chunk corresponde a la medición de línea base (sin falla/estímulo). |
| `chunk_index` | int | Índice secuencial del chunk (0 a 765). |
| `start_time` | float64 | Timestamp UNIX de inicio del chunk. |
| `end_time` | float64 | Timestamp UNIX de fin del chunk. |
| `n_signals` | int | Número de señales UHF adquiridas en el chunk (0 a 748). |
| `signal_offset` | int | Offset acumulado de señales UHF respecto al inicio del experimento. |
| `n_ae_signals` | int | Número de señales AE adquiridas en el chunk (0 a 88). |
| `ae_signal_offset` | int | Offset acumulado de señales AE respecto al inicio del experimento. |

### 2.3 Datasets de señal (`signals/` y `ae_signals/`)

| Dataset | Dtype | Forma | Descripción |
|---|---|---|---|
| `data` | float32 | `(n_señales, 3000)` UHF / `(n_señales, 10000)` AE | Forma de onda cruda de cada pulso adquirido. |
| `timestamps` | float64 | `(n_señales,)` | Timestamp UNIX absoluto de disparo de cada señal. |
| `triggers` | float64 | `(n_señales,)` | **Nivel de disparo del osciloscopio, en voltios** (p. ej. 0.024 V) — no es un código de evento. |
| `vranges` | float64 | `(n_señales,)` | Rango de voltaje configurado en el osciloscopio para esa adquisición. |

Compresión observada en los datasets de señal: `gzip`, con chunking interno HDF5 (p. ej.
`(6, 1250)` en `ae_signals/data`).

Consistente con `config.py` del analizador: UHF a `FS_UHF = 3 GHz` (3000 puntos → 1 µs de
ventana) y AE a `FS_AE = 100 kHz` (10000 puntos → 100 ms de ventana).

### 2.4 Eventos (`events/`)

| Dataset | Dtype | Descripción |
|---|---|---|
| `timestamps` | float64 | Timestamp UNIX del evento. |
| `type` | object (string) | Tipo de evento. Valores encontrados en este archivo: `SHOT`, `PA`, `FO`. |

El grupo `events` puede existir vacío (sin datasets) en chunks sin eventos detectados.

### 2.5 Datos ambientales (`humidity/`)

| Dataset | Dtype | Descripción |
|---|---|---|
| `humidity` | float64 | Humedad relativa muestreada durante el chunk. |
| `temperature` | float64 | Temperatura muestreada durante el chunk. |
| `timestamps` | float64 | Timestamp UNIX de cada muestra ambiental. |

La cantidad de muestras ambientales por chunk es variable (entre 8 y 377 en este archivo),
a diferencia de `signals`/`ae_signals`, cuyo largo de vector por señal es fijo.

---

## 3. Estadísticas globales del archivo

| Métrica | Valor |
|---|---|
| Chunks totales | 766 |
| Chunks marcados como baseline (`is_baseline=True`) | 3 |
| Chunks con señales UHF (`signals` no vacío) | 259 |
| Chunks con señales AE (`ae_signals` no vacío) | 681 |
| Chunks con eventos (`events` no vacío) | 87 |
| Chunks con datos ambientales | 766 (todos) |
| Señales UHF totales (suma de `n_signals`) | 12 484 |
| Señales AE totales (suma de `n_ae_signals`) | 20 574 |
| Eventos totales | 98 → `PA`: 76, `SHOT`: 21, `FO`: 1 |
| Rango temporal del experimento | 2026-08-07 13:47:09 UTC → 2026-08-07 19:39:06 UTC (≈ 5 h 52 min) |
| Tamaño del archivo | 180.4 MB |

**Nota:** no todos los chunks contienen señales de ambos sensores en simultáneo — hay chunks
solo con AE, solo con UHF, con ambos, o sin ninguna señal (solo ambientales/baseline).

---

## 4. Compatibilidad con el proyecto `analizador_nuevo`

Este archivo sigue exactamente el esquema esperado por `data_handler.py`:
- `abrir_experimento` localiza el grupo raíz buscando subgrupos `chunk_*`.
- `obtener_datos_senal` / `obtener_todos_datos_senales` leen `signals/` o `ae_signals/` según
  `sensor_type='UHF'|'AE'`.
- `obtener_eventos_chunk` lee exclusivamente `events/` (nunca `signals/triggers`, que es el nivel
  de disparo del osciloscopio, no un código de evento).
- `obtener_datos_ambientales` lee `humidity/{humidity,temperature,timestamps}`.

Puede abrirse directamente con la GUI del analizador (`python analizador_datos.py`) o inspeccionarse
con las funciones puras de `data_handler.py` sin necesidad de Qt.
