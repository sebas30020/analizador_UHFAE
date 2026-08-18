# FASE 0 — Diseño de la Plataforma de Análisis UHF/AE (SIN CÓDIGO)

Documento de diseño exigido por `PROMPT_Analizador_Señales_UHF_AE.md` §12 (Fase 0), antes de
escribir una sola línea de código. Completa los puntos `⚠️ COMPLETAR` con los datos reales del
proyecto y resuelve, con justificación, los puntos `❓ DECIDIR`.

**Fuentes usadas:**
- Esquema real de la base de datos origen: [`esquema_med_5_ago_3.md`](./esquema_med_5_ago_3.md)
  (inspección directa de `D:\data\data\main\med_5_ago_3.hdf5`, una medición real).
- Listado definitivo de métricas: [`listado_metricas_tiempo_frecuencia.md`](./listado_metricas_tiempo_frecuencia.md).
- Formulación matemática de referencia: `C:\0_matrix\doctorado\proyectos\programacion\analizador_datos\analizador_nuevo\metricas.py`
  (implementación existente, ya validada, del proyecto `analizador_nuevo`).

---

## 0. Conflictos detectados frente al PROMPT maestro (requieren tu confirmación)

Antes de fijar el diseño, dos puntos del PROMPT maestro no coinciden con los datos reales
inspeccionados. Los resuelvo con una asunción razonable (ver §10) pero **deben confirmarse**:

1. **§2.3 asume un motor de base de datos "SQLite/PostgreSQL/otro" con DDL de tablas.** La base
   real es **HDF5** (jerárquico, sin DDL relacional). Todo el diseño de este documento asume
   HDF5 como formato de origen, con el esquema documentado en `esquema_med_5_ago_3.md`. Si existe
   además una base relacional en otro punto del flujo, no fue la que se inspeccionó.
2. **§2.3 dice que los eventos son "únicamente un timestamp, sin payload".** En los datos reales,
   `events/type` sí existe y trae un tipo (`SHOT`, `PA`, `FO` en el archivo de prueba). **Asunción
   tomada:** se preserva el campo `type` como metadato opcional del evento (para poder colorear o
   filtrar por tipo más adelante), pero el comportamiento mínimo exigido por el PROMPT (marca
   vertical por timestamp, sin más lógica) se respeta igual. Confírmame si el tipo de evento debe
   tener algún tratamiento especial (p. ej. solo `SHOT` es un evento de "botón de operador" real, y
   `PA`/`FO` son detecciones automáticas del sistema con otro significado).

---

## 1. `⚠️ COMPLETAR` — Esquema real de la base de datos origen

Resumen (detalle completo en `esquema_med_5_ago_3.md`):

```
<test_group>/                        (atributos: date, chunk_duration_s, initial_dead_time_s,
                                       description, version)
  chunk_NNNNNN/                      (atributos: is_baseline, chunk_index, start_time, end_time,
                                       n_signals, signal_offset, n_ae_signals, ae_signal_offset)
    signals/      {data float32 (n,3000), timestamps f64, triggers f64, vranges f64}   # UHF
    ae_signals/   {data float32 (n,10000), timestamps f64, triggers f64, vranges f64}  # AE
    events/       {timestamps f64, type object(str)}     # puede estar vacío
    humidity/     {humidity f64, temperature f64, timestamps f64}   # largo variable por chunk
```

Datos verificados sobre `med_5_ago_3.hdf5` (766 chunks, un solo experimento):

| Propiedad | UHF | AE |
|---|---|---|
| Muestras por señal (`M`) | **3000** (constante en todo el archivo) | **10000** (constante en todo el archivo) |
| `fs` (Hz) | 3×10⁹ | 1×10⁵ |
| Duración de la traza | ≈ 1 µs | ≈ 100 ms |
| dtype de `data` | float32 | float32 |
| Compresión observada | gzip | gzip |
| Señales totales en el archivo | 12 484 | 20 574 |
| Chunks con datos de este sensor | 259 / 766 | 681 / 766 |

`triggers` = nivel de disparo del osciloscopio en voltios (metadato "Trigger" del PROMPT §2.4).
`vranges` = escala vertical configurada (metadato "Escala vertical" del PROMPT §2.4) — **es el
divisor de la normalización obligatoria** (ver §3.1).

---

## 2. `⚠️ COMPLETAR` — Listado definitivo de métricas (régimen · dominio · parámetros)

Fuente de las fórmulas: `metricas.py` del proyecto `analizador_nuevo` (funciones citadas). Todas
operan hoy sobre matrices `(N, M)` vectorizadas — cumplen el requisito de vectorización del §4.4 y
§9.1 sin cambios estructurales, solo se reempaquetan como plugins del nuevo registro.

### 2.1 Métricas puntuales — dominio tiempo (15)

| Id propuesto | Métrica | Función de referencia | Parámetros (default) |
|---|---|---|---|
| `rms` | Valor RMS | `calcular_rms` | — |
| `vpp` | VPP | `calcular_vpp` | — |
| `vmax` | Vmax | `calcular_vmax` | — |
| `kurtosis` | Kurtosis | `calcular_kurtosis_stat` | `type`: `pearson`\|`excess` (def. `pearson`) |
| `skewness` | Skewness | `calcular_skewness` | — |
| `crest_factor` | Factor de cresta | `calcular_crest_factor` | — |
| `shannon_entropy` | Entropía Shannon | `calcular_shannon_entropy` | `bins` (def. 64) |
| `zcr` | ZCR | `calcular_zcr` | `dead_zone` (def. 0.01, fracción del pico) |
| `rise_time` | Rise Time | `calcular_rise_time` | `lower_pct`/`upper_pct` (def. 0.1/0.9); requiere `fs` del sensor |
| `teq` | Tiempo Eq. | `calcular_teq` | requiere `fs` del sensor |
| `energia_relativa` | Energía Relativa | `calcular_energia_relativa` | — |
| `energia_v2s` | Energía V²s | `calcular_energia_v2s` | requiere `fs` del sensor |
| `energia_joules` | Energía Joules | `calcular_energia_j` | `R` ohms (def. 50) |
| `delta_t` | Delta T | `calcular_delta_t` | requiere timestamp de la señal anterior (ver nota) |
| `log_delta_t` | Log Delta T | `calcular_log_delta_t` | `epsilon` (def. 1e-9) |

> **Nota sobre `delta_t`/`log_delta_t`:** a diferencia del resto, no dependen solo de la traza
> propia sino del timestamp de la señal cronológicamente anterior del mismo sensor. Siguen siendo
> "régimen puntual" (un valor por señal), pero el motor de métricas debe exponer al plugin acceso
> de solo lectura al vector de timestamps global, no solo a la traza. Se documenta como una
> categoría de dependencia adicional en el contrato de plugin (§8).

### 2.2 Métricas puntuales — dominio frecuencia (2)

| Id propuesto | Métrica | Función de referencia | Parámetros (default) |
|---|---|---|---|
| `feq` | Frecuencia Eq. | `calcular_feq` | `freq_limit` **por sensor** (100 MHz UHF / 50 kHz AE); requiere `fs` |
| `f_aprox` | Frec. Aprox. | `calcular_f_aprox` | reutiliza `zcr.dead_zone`; requiere `fs` |

El espectro base (`welch_1ghz` en el código de referencia → PSD de Welch) **no es una métrica**,
es el cálculo espectral centralizado que exige el PROMPT §4.3: se calcula una vez por señal y se
cachea internamente en la misma pasada para que `feq` (y cualquier métrica espectral futura) lo
reutilice sin recalcular la FFT.

### 2.3 Métricas de grupo — dominio tiempo (3)

| Id propuesto | Métrica | Función de referencia | Parámetros (default) |
|---|---|---|---|
| `tasa_pulsos` | Tasa de Pulsos | `calcular_tasa_pulsos_array` | `T_w` = duración del grupo (derivada del criterio de agrupamiento §4.2) |
| `tasa_energia` | Tasa de Energía | `calcular_tasa_energia_array` | `T_w` idem |
| `tasa_rafagas` | Tasa de Ráfagas | `calcular_tasa_rafagas_array` (usa `calcular_rafagas`) | `tau` (def. 10 ms), `n_min` (def. 5), `T_w` idem |

No hay métricas de grupo en dominio frecuencia en el listado actual — el contrato de plugin (§8)
no lo impide, simplemente no hay ninguna implementada hoy.

**Total: 17 puntuales (15 tiempo + 2 frecuencia) + 3 de grupo (tiempo) = 20 métricas iniciales**,
coincidiendo con el inventario de `listado_metricas_tiempo_frecuencia.md` una vez separadas por
régimen puntual/grupo.

---

## 3. `❓ DECIDIR` — Resoluciones de diseño

### 3.1 Fórmula de normalización (§2.4)

**Decisión:** `x_norm = x_raw / vrange`, donde `vrange` es el valor de `vranges` **de esa señal
específica** (no un valor global por sensor — cada señal puede haberse adquirido con una escala
vertical distinta).

**Justificación:** `vranges` es literalmente el metadato "escala vertical" que el PROMPT exige
usar (§2.4), ya presente y poblado en los datos reales para el 100% de las señales inspeccionadas.
Dividir por la escala vertical lleva la señal a una magnitud relativa al fondo de escala del
osciloscopio en el momento de la captura, haciendo comparables señales adquiridas con distinta
ganancia — exactamente el objetivo declarado ("garantiza que señales adquiridas con escalas
distintas sean comparables entre sí").

**Casos borde:**
- `vrange == 0` o ausente/NaN → la señal se marca inválida (`valid_mask = False`) y se excluye de
  todo cálculo de métricas, con aviso en interfaz — regla general de datos faltantes del PROMPT
  §11.
- `vrange < 0` (no debería ocurrir por diseño del instrumento) → mismo tratamiento que inválida.

**Versión de la regla:** se registra como `normalization_version = "v1_divide_by_vrange"` y forma
parte de la clave de caché (§7); cualquier cambio de fórmula futuro incrementa esta versión e
invalida el caché automáticamente.

**Flag de depuración:** el pipeline de ingesta conserva la señal cruda accesible (no se sobrescribe
en disco); la normalización se aplica **on-the-fly** al leer un bloque para cálculo de métricas o
para graficar tipo #2, nunca se persiste una copia normalizada — así "cruda vs. normalizada" es
simplemente aplicar o no la división, auditable y trivial de alternar en el visor.

### 3.2 Formato de persistencia de la matriz global (§3.1)

**Decisión: HDF5 (h5py)**, un archivo canónico por dataset ingerido, con un grupo por sensor
(`/uhf`, `/ae`) y datasets `data`, `timestamps`, `trigger`, `vrange`, `valid_mask`, `minmax`
(par min/max precalculado, ver §5.1 del PROMPT), chunked internamente (tamaño de chunk HDF5
alineado al tamaño de bloque de lote de §6) y comprimido (`gzip`, nivel bajo para no penalizar
lectura aleatoria).

**Justificación frente a las alternativas:**
- **Parquet + Arrow:** fuerte en analítica columnar y compresión, pero su unidad natural de acceso
  es el *row group*, no la fila individual — el acceso aleatorio a una señal por índice (requisito
  duro de la gráfica tipo #2, "saltar a la señal #450") es menos directo y más costoso que un
  slice HDF5 nativo. Añade además una dependencia (`pyarrow`) sin beneficio claro para este patrón
  de acceso.
- **Memmap de NumPy + sidecar de metadatos:** el más rápido en lectura pura, pero sin compresión
  nativa, sin soporte cómodo de crecimiento incremental (la ingesta puede llegar por lotes/archivos
  sucesivos) y duplicando a mano lo que HDF5 ya resuelve (atributos, grupos, datasets
  redimensionables).
- **HDF5** da acceso aleatorio por fila en O(1) vía slicing, soporta lectura concurrente
  multiproceso en modo solo-lectura (compatible con el paralelismo exigido en §9.1), permite
  atributos versionados junto a los datos (`fs`, `M`, `freq_limit`, `normalization_version`,
  `schema_version`, hash del dataset origen) y reutiliza el conocimiento ya construido en
  `analizador_nuevo` (que ya usa h5py de forma extensiva, incluyendo su propio caché de métricas en
  HDF5). Es la opción de menor riesgo operativo.

El origen (`med_5_ago_X.hdf5`) se abre **estrictamente en modo lectura** durante la ingesta; el
archivo canónico de salida es un artefacto nuevo e independiente, nunca el mismo archivo.

### 3.3 Motor del caché de métricas (§8.2)

**Decisión: SQLite para el índice de claves + payloads binarios en un HDF5 de caché adjunto**
(mismo patrón que ya usa `analizador_nuevo/metrics_cache.py`, pero separando índice de payload).

**Justificación frente a DuckDB:** el caso de uso es *lookup* puntual por clave compuesta
determinista (§7) → devolver o no un array ya calculado. Es un patrón clave-valor transaccional
simple, no una carga analítica columnar sobre grandes tablas (que es donde DuckDB brilla). SQLite
es transaccional, portable (un archivo), soporta índices únicos sobre la clave de caché para
detectar colisiones/duplicados en O(log n), y es sobradamente suficiente para un uso single-user
local. DuckDB añadiría una dependencia más pesada sin ventaja medible en este patrón de acceso.
Los **arrays de resultados** (los puntos `(timestamp, valor)` de cada métrica) se guardan como
datasets binarios en un HDF5 de caché aparte; SQLite solo guarda la fila de índice
`(cache_key → nombre_dataset_en_hdf5_cache, metadata, timestamp_de_escritura)`. Esto evita BLOBs
grandes dentro de SQLite (que degradan su rendimiento) y mantiene la lectura de arrays tan rápida
como cualquier slice HDF5.

### 3.4 Tecnología de interfaz (§6.4)

**Decisión: Dash (Plotly)**, tal como sugiere el PROMPT como opción por defecto.

**Justificación:** el requisito no negociable es el filtrado cruzado bidireccional vía lazo de
Plotly (§7), que en Dash es soporte nativo (`selectedData` + callbacks), sin puente JS↔Python
artesanal. El modelo de "ventanas gemelas" y "ventanas adicionales bajo demanda" (§6.3) se resuelve
con **pestañas/ventanas de navegador independientes** apuntando a rutas distintas del mismo
servidor Dash multi-página (`dash.register_page` o equivalente), todas contra el **mismo proceso
backend** — como es una herramienta de un solo usuario local, el estado compartido (máscara de
selección, caché caliente) vive en memoria del proceso Dash protegido por un lock simple, sin
necesitar Redis ni un almacén externo. Cada "ventana nueva" es literalmente `window.open()` a una
nueva ruta (`/uhf/metrics-window/<id>`), lo que cumple "tantas ventanas como desee" sin gestión de
ventanas nativas. Frente a PyQt/PySide + QWebEngineView: se evita el puente JS↔Python frágil que el
propio PROMPT señala como riesgo, a costa de que la app corre en el navegador en vez de como
ventana nativa — aceptable porque no hay requisito de "aplicación de escritorio nativa" en ningún
punto del PROMPT, solo de "ventanas" (que un navegador cumple igual).

---

## 4. Arquitectura de capas

```
┌─────────────────────────────────────────────────────────────┐
│ presentación (ui/)                                           │
│   Dash app · páginas UHF/AE · ventanas de métricas dinámicas │
│   Sin lógica de cálculo. Lee/escribe únicamente la máscara   │
│   de selección (state.py) y solicita puntos a metrics/engine │
└───────────────▲─────────────────────────────┬─────────────────┘
                │ callbacks (selectedData,     │ solicita métrica
                │ filtros, navegación)         ▼
┌───────────────┴─────────────────────────────────────────────┐
│ caché (cache/)                                                │
│   índice SQLite (clave→puntero) + payload HDF5 de resultados │
│   Intercepta toda solicitud de métrica antes del motor        │
└───────────────▲─────────────────────────────┬─────────────────┘
                │ miss                         │ hit: puntos listos
                ▼                              │
┌───────────────────────────────────────────────────────────────┐
│ procesamiento (metrics/, core/grouping.py, core/normalization) │
│   registro de plugins · orquestador paralelo · agrupamiento    │
│   espectro centralizado por señal · normalización versionada   │
└───────────────▲─────────────────────────────────────────────────┘
                │ lee bloques (N,M) + timestamps + metadatos
                ▼
┌───────────────────────────────────────────────────────────────┐
│ datos (data/)                                                  │
│   readers/ (interfaz abstracta + HDF5Reader) · ingest.py        │
│   (desempaqueta chunks → matriz global) · storage.py (HDF5      │
│   canónico, lectura perezosa por bloques, min/max persistido)   │
└───────────────────────────────────────────────────────────────┘
```

Regla dura: cada capa solo conoce la interfaz de la inmediatamente inferior. `ui/` nunca importa
`data/`; `metrics/` nunca importa `ui/`; `data/readers/` es la única capa que sabe que el origen es
HDF5 con chunks — todo lo demás ve la matriz global ya desempaquetada.

---

## 5. Modelo de datos canónico

### 5.1 Matriz global de señales (una por sensor: `uhf`, `ae`)

| Campo | Forma | Dtype | Notas |
|---|---|---|---|
| `data` | `(N, M_sensor)` | `float32` | Cruda, tal como llega del origen. `M_sensor` fijo por sensor (3000 UHF / 10000 AE, verificado). |
| `timestamps` | `(N,)` | `float64` | Timestamp UNIX absoluto de captura. Orden cronológico estricto tras la ingesta (ordenamiento explícito, no asumido). |
| `trigger` | `(N,)` | `float64` | Nivel de disparo del osciloscopio (V). Metadato de interfaz, no se usa en cálculo. |
| `vrange` | `(N,)` | `float64` | Escala vertical. Divisor de normalización (§3.1). |
| `valid_mask` | `(N,)` | `bool` | `False` si falta `trigger`/`vrange` o son no numéricos → señal excluida de métricas. |
| `minmax` | `(N, 2)` | `float32` | Min/max por señal, precalculado una sola vez en ingesta (alimenta gráfica tipo #1). |
| `sensor_index` | escalar (atributo) | — | `'UHF'` o `'AE'`, fijo por matriz — no se mezclan sensores en una misma matriz. |
| índice cronológico global | implícito (posición de fila) | `int` | 0..N-1, identificador estable usado por toda la app (navegación, selección, caché). |

**Eje temporal intra-señal:** no se replica por fila. Se almacena `t0=0.0` y `dt=1/fs_sensor` como
atributos del grupo del sensor (`fs` es constante por sensor, verificado en los datos reales — no
varía entre chunks). El eje de tiempos de una traza se reconstruye bajo demanda como
`np.arange(M_sensor) * dt + t0`, documentado explícitamente aquí como exige el PROMPT §3.1.

**Acceso aleatorio sin cargar todo a RAM:** HDF5 slicing (`data[i]` o `data[i0:i1]`) lee solo las
filas solicitadas gracias al chunking interno del dataset — no requiere `[:]` completo.

### 5.2 Series auxiliares

- **Ambientales** (`/environmental`): `timestamps`, `temperature`, `humidity`, longitud propia
  (variable en los datos reales, 8–377 muestras por bloque de 10 s). No se interpola contra las
  señales; se grafica en eje secundario de la gráfica tipo #1 con su propio muestreo.
- **Eventos** (`/events`): `timestamps`, `type` (ver conflicto §0.2 — se preserva `type` aunque el
  PROMPT no lo exigía).

---

## 6. Perfil de configuración por sensor (UHF vs. AE)

| Parámetro | UHF | AE | Fuente |
|---|---|---|---|
| `fs` (Hz) | 3 × 10⁹ | 1 × 10⁵ | Verificado en datos reales |
| `M` (muestras/señal) | 3000 | 10000 | Verificado en datos reales |
| Duración de traza | 1 µs | 100 ms | `M/fs` |
| Unidad de eje gráfica tipo #2 | µs | ms | PROMPT §2.1 |
| `freq_limit` (límite de análisis espectral) | 100 MHz | 50 kHz (≈ Nyquist) | Heredado de `config.py` de `analizador_nuevo` |
| Resolución espectral `Δf = fs/M` | 1 MHz | 10 Hz | Calculado |
| Bytes/señal cruda (`M × 4` por float32) | 12 000 B (11.7 KB) | 40 000 B (39.1 KB) | Calculado |
| Peso total observado en `med_5_ago_3.hdf5` | 12 484 señales ≈ **150 MB** | 20 574 señales ≈ **823 MB** | Calculado sobre datos reales |
| Tamaño de bloque de lote (I/O + cálculo), objetivo 64 MB/bloque | ≈ 5461 señales/bloque | ≈ 1638 señales/bloque | `target_block_bytes // (M×4)`, configurable en `config/` |
| Ventana FFT | `M` completo (1 sola FFT de 3000 pts por traza, sin solapamiento — la traza completa es la ventana) | `M` completo (10000 pts) | No hay solapamiento porque cada señal ya es un evento discreto corto, no una serie continua a segmentar |

**Implicación de rendimiento explícita:** una traza AE pesa ~3.3× una UHF, pero además hay ~1.6×
más señales AE que UHF en el archivo de prueba → el volumen total de datos AE (~823 MB) es
~5.5× el de UHF (~150 MB) en este dataset. El dimensionamiento de bloques, el nivel de
paralelismo y los benchmarks de §9.2 del PROMPT deben reportarse **por separado** y no promediarse,
tal como exige el PROMPT — este perfil es la base para hacerlo.

---

## 7. Diseño de la clave de caché

Clave compuesta, determinista, serializada como string canónico (JSON ordenado + hash SHA-256 corto
para el nombre físico del dataset en el HDF5 de caché, string completo como valor indexado en
SQLite para trazabilidad):

```
cache_key = sha256(json_canonico({
    "dataset_id": <hash de contenido o (ruta, tamaño, mtime) del HDF5 canónico origen>,
    "sensor": "UHF" | "AE",
    "metric_id": "<id del plugin, p.ej. 'rms'>",
    "metric_version": <int>,
    "metric_params": {<parámetros ordenados alfabéticamente, valores canonizados>},
    "normalization_version": "v1_divide_by_vrange",
    "grouping": {
        "mode": "none" | "by_time" | "by_count",
        "value": <Δt en s | K>,
        "partial_policy": "include_marked" | "discard"
    } | null   # null para métricas puntuales
}))
```

- Cambiar `metric_version` (el autor de la métrica la incrementa al tocar la fórmula),
  `normalization_version`, o cualquier parámetro → clave distinta → caché anterior queda huérfano,
  nunca se lee por error.
- Utilidad de purga: recorre el índice SQLite y elimina toda fila cuyo `dataset_id` ya no exista en
  disco, o cuya combinación `(metric_id, metric_version)` ya no esté registrada en el registro de
  plugins activo.
- El caché siempre almacena sobre el **conjunto completo** de señales del sensor (§8.2 del PROMPT);
  el filtrado por máscara se aplica en la capa de presentación sobre el resultado ya leído, nunca
  dispara un recálculo.

---

## 8. Contrato de la interfaz de métricas (registro por decorador)

Cada métrica se registra en un archivo propio dentro de `metrics/time_domain/` o
`metrics/freq_domain/` con un decorador que expone, como mínimo:

| Campo | Obligatorio | Descripción |
|---|---|---|
| `id` | sí | Identificador único y estable (usado en la clave de caché). |
| `label` | sí | Nombre legible para la UI. |
| `regimen` | sí | `"puntual"` \| `"grupo"`. |
| `dominio` | sí | `"tiempo"` \| `"frecuencia"`. |
| `unit` | sí | Unidad para el eje/etiqueta. |
| `version` | sí | Entero, participa en la clave de caché. |
| `params_schema` | sí | Parámetros con valores por defecto (tipados). |
| `requires_spectrum` | no (def. `False`) | Si `True`, el motor le inyecta el espectro ya calculado (una FFT por señal, compartida entre todas las métricas espectrales de la pasada) en vez de dejar que la métrica recalcule. |
| `requires_global_timestamps` | no (def. `False`) | Caso `delta_t`/`log_delta_t` (§2.1): la función recibe también el vector de timestamps del sensor completo, de solo lectura. |
| `compute(signals, **params) -> np.ndarray` | sí | Pura, vectorizada, sin estado; recibe `signals` ya normalizada `(N, M)` o `(N,)` para una sola traza; para régimen `grupo` recibe además el particionado de grupos ya resuelto por `core/grouping.py`. |

El motor (`metrics/engine.py`) descubre el registro por introspección en arranque (escaneo de
`metrics/time_domain/` y `metrics/freq_domain/`), sin *hardcodear* ninguna lista — agregar una
métrica nueva es crear un archivo, cero cambios en núcleo ni UI (prueba de fuego §10.3 del PROMPT).

---

## 9. Estructura de directorios definitiva

Se adopta la propuesta del PROMPT §10.2 sin cambios estructurales. **Ubicación confirmada por el
usuario:** el proyecto se crea directamente dentro de la carpeta actual
`C:\0_matrix\doctorado\proyectos\aislador_trafo\Analizadores_de_datos\metricas\` — no se crea una
subcarpeta `analizador_uhf_ae\` adicional; esta carpeta **es** la raíz del proyecto. Los documentos
de diseño ya existentes (`esquema_med_5_ago_3.md`, `listado_metricas_tiempo_frecuencia.md`,
`PROMPT_Analizador_Señales_UHF_AE.md`, este archivo, y el PDF de referencia) permanecen en la raíz
junto al código:

```
C:\0_matrix\doctorado\proyectos\aislador_trafo\Analizadores_de_datos\metricas\
├── esquema_med_5_ago_3.md                    (ya existente)
├── listado_metricas_tiempo_frecuencia.md      (ya existente)
├── PROMPT_Analizador_Señales_UHF_AE.md         (ya existente)
├── FASE0_DISENO_Analizador_UHF_AE.md           (ya existente, este documento)
├── Informe_variables_PD_linea_base_alarmas_v2 (1).pdf   (ya existente)
├── config/
│   └── sensors.yaml              # perfil §6 (fs, M, freq_limit, block size) por sensor
├── core/
│   ├── models.py
│   ├── normalization.py
│   └── grouping.py
├── data/
│   ├── readers/
│   │   ├── base.py               # interfaz abstracta
│   │   └── hdf5_reader.py        # implementación para el esquema de esquema_med_5_ago_3.md
│   ├── ingest.py
│   └── storage.py
├── metrics/
│   ├── registry.py
│   ├── engine.py
│   ├── spectral.py
│   ├── time_domain/              # 15 puntuales + 3 de grupo, ver §2
│   └── freq_domain/              # 2 puntuales, ver §2
├── cache/
│   ├── backend.py                # índice SQLite
│   └── keys.py                   # §7
├── ui/
│   ├── app.py
│   ├── pages/                    # una página Dash por ventana (sensor, ventana de métricas N)
│   ├── components/
│   ├── state.py                  # máscara única por sensor + pila undo/redo
│   └── callbacks/
├── viz/
│   └── decimation.py
├── utils/
│   ├── logging.py
│   └── profiling.py
└── tests/
```

---

## 10. Supuestos asumidos

1. El archivo `med_5_ago_3.hdf5` es representativo del esquema de **todos** los archivos de
   `D:\data\data\main\`; el `HDF5Reader` se implementa contra ese esquema general (múltiples
   `chunk_*` bajo uno o más grupos de test), no contra las particularidades de un solo archivo.
2. `fs` y `M` son constantes por sensor **dentro de un mismo archivo/experimento** (verificado);
   se asume que también lo son entre archivos distintos del mismo instrumento — a confirmar si hay
   mediciones con otra configuración de osciloscopio.
3. El campo `type` de eventos se conserva como metadato opcional (§0.2, con tratamiento genérico —
   ver decisión en §11), sin que esto bloquee el comportamiento mínimo pedido por el PROMPT.
4. El proyecto se crea directamente en
   `C:\0_matrix\doctorado\proyectos\aislador_trafo\Analizadores_de_datos\metricas\` (confirmado por
   el usuario — ver §9), sin subcarpeta adicional.
5. Es una herramienta de un solo usuario local (no multi-usuario concurrente) — justifica el
   backend Dash en memoria de proceso único sin Redis (§3.4) y SQLite sin necesidad de manejo de
   alta concurrencia de escritura (§3.3).
6. Cada archivo `.hdf5` de `main/` (p. ej. `med_5_ago_3.hdf5`) es un experimento independiente: se
   ingiere y cachea por separado, y el usuario elige cuál abrir desde el botón de selección de base
   de datos (§6.2 del PROMPT). El `dataset_id` de la clave de caché (§7) identifica un archivo, no
   el directorio completo.
7. Nivel de paralelismo por defecto: `os.cpu_count() - 1` (dejando un núcleo libre para la UI/SO),
   configurable en `config/sensors.yaml`. Sin presupuesto de RAM específico confirmado, se asume una
   máquina de desarrollo estándar (≥ 16 GB) y se diseña para nunca cargar el dataset completo en RAM
   (carga perezosa por bloques, §6) — el valor exacto de `target_block_bytes` (64 MB propuesto) es
   ajustable sin cambios de arquitectura si el presupuesto real difiere.

## 11. Decisiones confirmadas por el usuario y preguntas que siguen abiertas

**Confirmado:**
- Ubicación del proyecto: esta misma carpeta (`metricas/`), sin subcarpeta nueva (§9).
- `events/type` se preserva con **tratamiento genérico**: se guarda el dato, pero por ahora todas
  las marcas verticales (`SHOT`, `PA`, `FO`) se renderizan igual, sin color ni filtro diferenciado.
  Diferenciar por tipo queda como mejora futura, no bloquea ninguna fase.
- Alcance del dataset: **un archivo `.hdf5` = un experimento independiente** (recogido como
  supuesto #6 arriba).
- Las fórmulas de `metricas.py` se **contrastan contra el PDF**
  `Informe_variables_PD_linea_base_alarmas_v2 (1).pdf` antes de portarlas al nuevo registro de
  métricas (Fase 2) — pendiente de ejecutar esa revisión; se reportará cualquier discrepancia entre
  el código y el documento fuente antes de fijar la versión `1` de cada plugin de métrica.

**Sigue abierto:**
1. Nivel de paralelismo/RAM real de la máquina objetivo — se usa el valor por defecto razonado del
   supuesto #7 salvo que indiques otro.

---

## 12. Próximo paso

Este documento cubre íntegramente los entregables de Fase 0 pedidos por el PROMPT §12: resolución
de los `❓ DECIDIR`, diagrama de capas, modelo de datos canónico, perfil por sensor, clave de
caché, contrato de métricas, estructura de directorios, supuestos y preguntas abiertas.

**No se ha escrito código.** Queda a la espera de tu aprobación (y de las respuestas a §11) antes
de iniciar la Fase 1 — Núcleo de datos.
