# PROMPT MAESTRO — Plataforma de Análisis de Señales UHF / AE

> **Instrucciones de uso:** entrega este documento completo como prompt inicial. Las secciones marcadas con `⚠️ COMPLETAR` requieren que pegues información específica de tu entorno (esquema real de la base de datos, listado definitivo de métricas) antes de ejecutar. Las secciones marcadas con `❓ DECIDIR` son puntos donde el agente debe proponer y justificar una opción antes de codificar.

---

## 1. ROL Y OBJETIVO

Actúa como **arquitecto de software senior especializado en procesamiento digital de señales y aplicaciones científicas de alto rendimiento en Python**.

Tu tarea es diseñar e implementar una **plataforma de análisis y visualización interactiva de señales de descargas parciales**, adquiridas mediante dos tipos de sensores independientes: **UHF** y **Acústico (AE)**.

El sistema debe cumplir simultáneamente tres objetivos no negociables:

1. **Rendimiento**: interacción fluida sobre volúmenes grandes de señales (decenas o cientos de miles de trazas), con visualización en tiempo interactivo (< 200 ms de respuesta en operaciones de filtrado/selección ya cacheadas).
2. **Corrección científica**: normalización obligatoria por escala vertical antes de cualquier cálculo; trazabilidad total entre señal cruda → señal normalizada → métrica → punto graficado.
3. **Extensibilidad**: agregar una nueva métrica, un nuevo tipo de sensor o un nuevo tipo de gráfica debe ser una operación **aditiva** (registrar un plugin), nunca una modificación invasiva del núcleo.

**No escribas código hasta haber entregado y validado conmigo el diseño de la Fase 0** (ver Sección 12).

---

## 2. CONTEXTO DEL DOMINIO

### 2.1 Naturaleza de las señales

- Se analizan **señales crudas**, tal cual salen del sistema de adquisición. **No se aplica ningún preprocesamiento** (sin filtrado, sin suavizado, sin resampleo) salvo la normalización descrita en §2.4.
- Existen dos familias completamente separadas: **UHF** y **AE**. Se procesan y visualizan de forma independiente, en ventanas gemelas pero desacopladas.
- **La duración de la señal individual difiere radicalmente entre sensores:**

| Sensor | Duración de la señal individual | Orden de magnitud relativo |
|---|---|---|
| **UHF** | ≈ **1 microsegundo** (1 µs) | referencia |
| **AE (acústico)** | ≈ **100 milisegundos** (100 ms) | **10⁵ veces más larga** |

**Implicaciones de diseño obligatorias derivadas de esta asimetría** (el agente debe respetarlas explícitamente y no asumir simetría entre sensores):

- **Ninguna dimensión temporal puede estar cableada en el código.** La duración de señal, la frecuencia de muestreo y el número de muestras por traza son **propiedades del sensor**, resueltas por configuración y/o leídas de la base de datos, nunca constantes literales.
- El número de muestras por señal (`M`) será **muy distinto** entre UHF y AE. Todas las estructuras, cálculos y reservas de memoria deben parametrizarse por `M` del sensor activo. Verifica y documenta el `M` real de cada familia durante la ingesta.
- **Presupuesto de memoria y E/S diferenciado**: una traza AE puede pesar órdenes de magnitud más que una UHF. El dimensionamiento de bloques de lectura, el nivel de paralelismo y el tamaño de lote para el cálculo de métricas deben calcularse **en función del peso real de la traza del sensor**, no con un valor único compartido.
- **Dominio frecuencial diferenciado**: la resolución espectral (`Δf = 1/T`) y el rango de frecuencias útil son completamente distintos entre ambos sensores. Las métricas frecuenciales y sus parámetros por defecto (bandas de interés, tamaño de ventana FFT, solapamiento) deben definirse **por sensor**, no de forma global. El coste de la FFT también difiere de forma sustancial: planifica en consecuencia.
- **Escalas de eje**: la gráfica tipo #2 debe rotular su eje horizontal en la unidad natural del sensor (µs para UHF, ms para AE), de forma automática.
- **Relación entre duración de señal y ventana de agrupamiento**: para AE, una señal de 100 ms es una fracción no despreciable de una ventana de agrupamiento de 10–12 s, mientras que para UHF es despreciable. Documenta si el agrupamiento temporal usa el timestamp de **inicio** de la señal o su **centro**, y advierte que para AE la elección tiene efecto medible en los bordes de ventana. **Por defecto: timestamp de inicio de captura**, consistente entre ambos sensores.

### 2.2 El concepto de "chunk" y su eliminación

La base de datos origen (`main`) almacena las señales agrupadas en **chunks**: ventanas temporales de adquisición que encapsulan un conjunto de señales capturadas durante aproximadamente 10 segundos.

**Requisito central:** el concepto de chunk es un artefacto de almacenamiento y debe **desaparecer por completo del modelo de datos de la aplicación**. Durante la ingesta:

- Se desempaquetan todos los chunks.
- Se concatenan todas las señales en **una única estructura matricial global**, ordenada cronológicamente de forma estricta.
- Cada señal conserva su **timestamp absoluto de captura** como identidad temporal.
- El resto del sistema (métricas, agrupamiento, gráficas, filtrado) opera **exclusivamente** sobre esta matriz global. Ninguna capa aguas arriba de la ingesta puede tener conocimiento de la existencia de chunks.

### 2.3 Contenido de la base de datos origen

La base de datos contiene:

| Entidad | Descripción |
|---|---|
| **Señales UHF** | Trazas crudas encapsuladas en chunks, con timestamp, metadatos de trigger y escala vertical. |
| **Señales AE** | Ídem, sensor acústico. |
| **Variables ambientales** | Lecturas de un sensor de **humedad relativa** y **temperatura**, con su propio timestamp y cadencia de muestreo (independiente y típicamente mucho más lenta que la de las señales). |
| **Eventos** | Marcas puntuales: únicamente un timestamp que registra el instante en que un operador presionó un botón durante la adquisición. No tienen payload de señal. |

> `⚠️ COMPLETAR` — Pega aquí el esquema real de la base de datos: nombre del motor (SQLite / PostgreSQL / otro), DDL de las tablas, tipos de dato, formato de serialización de los arrays de señal (BLOB binario, JSON, texto, `float32`/`float64`, orden de bytes), y una muestra representativa de registros.

**Comportamiento esperado del agente:** el módulo de ingesta debe estar aislado tras una interfaz abstracta, de modo que un cambio de esquema o de motor de base de datos solo requiera implementar un nuevo *reader* sin tocar el resto del sistema.

### 2.4 Metadatos y normalización (CRÍTICO)

Cada señal lleva asociados al menos dos metadatos que deben mostrarse en la interfaz:

- **Trigger** seleccionado durante la adquisición.
- **Escala vertical** utilizada.

**Regla inviolable:** antes de calcular **cualquier** métrica, la señal debe **normalizarse respecto a la magnitud de su escala vertical**. Esto garantiza que señales adquiridas con escalas distintas sean comparables entre sí.

Implicaciones de diseño que debes respetar:

- La normalización pertenece al **pipeline de datos**, no a las funciones de métrica. Cada función de métrica recibe señal ya normalizada y **nunca** debe implementar su propia normalización.
- La firma de normalización debe versionarse e incluirse en la clave de caché (§8). Si cambia la regla de normalización, las métricas cacheadas quedan invalidadas automáticamente.
- Debe existir un *flag* explícito y auditable que permita, en depuración, inspeccionar la señal cruda vs. la normalizada.
- `❓ DECIDIR`: define y documenta la fórmula exacta de normalización (p. ej. `x_norm = x / escala_vertical`, o normalización a fondo de escala en unidades físicas). Propón la más defendible y pídeme confirmación.

---

## 3. MODELO DE DATOS INTERNO

Diseña un modelo canónico, independiente del origen, con esta forma conceptual:

### 3.1 Matriz global de señales

Estructura columnar/matricial que contiene:

- **Muestras**: matriz `(N_señales × N_muestras)` de amplitudes, en `float32` salvo justificación en contra.
- **Eje temporal intra-señal**: vector de tiempos de las muestras (el "par de tiempo" de cada punto). Si el paso de muestreo es constante y compartido, **no lo repliques por señal**: almacena `t0` y `dt` y reconstruye bajo demanda. Documenta esta decisión.
- **Timestamp absoluto**: instante de captura de cada señal, con resolución suficiente para ordenamiento estricto.
- **Metadatos por señal**: trigger, escala vertical, sensor (UHF/AE), índice cronológico global (0..N-1) que sirve como identificador estable en toda la aplicación.

**Restricción de rendimiento:** este modelo debe permitir acceso aleatorio a cualquier señal sin cargar el conjunto completo en RAM.

`❓ DECIDIR` — Evalúa y justifica el formato de persistencia intermedia: **HDF5 (h5py)**, **Parquet + Arrow**, o **memmap de NumPy + sidecar de metadatos**. Criterios: acceso aleatorio por fila, lectura en rangos, footprint en disco, compatibilidad con procesamiento paralelo, simplicidad operativa.

### 3.2 Series auxiliares

- **Ambientales**: serie temporal `(timestamp, temperatura, humedad_relativa)`, con su propia cadencia. No se interpola sobre las señales salvo petición explícita.
- **Eventos**: lista de timestamps. Se renderizan como marcas verticales sobre las gráficas tipo #1 y tipo #3.

---

## 4. MOTOR DE MÉTRICAS

### 4.1 Las dos filosofías

El sistema calcula métricas bajo dos regímenes:

**A. Métricas puntuales** — una métrica por señal. Entrada: una traza normalizada. Salida: un escalar. Genera un punto `(timestamp_señal, valor)`.

**B. Métricas de grupo** — una métrica por conjunto de señales. Entrada: un conjunto de trazas normalizadas contiguas. Salida: un escalar. Genera un punto `(timestamp_representativo_del_grupo, valor)`.

`⚠️ COMPLETAR` — Pega aquí el listado definitivo de métricas con su definición matemática, clasificadas por:
> - **Régimen**: puntual | grupo
> - **Dominio**: tiempo | frecuencia
> - **Parámetros** que requiera cada una (ventana FFT, banda de interés, umbrales, etc.)

### 4.2 Criterios de agrupamiento

El agrupamiento se define **sobre la matriz global concatenada y ordenada cronológicamente**, nunca sobre los chunks originales. Dos modos, mutuamente excluyentes, seleccionables desde la GUI:

1. **Por ventana temporal**: agrupar todas las señales cuyo timestamp caiga dentro de una ventana de duración `Δt` configurable por el usuario (10 s, 12 s, o cualquier valor arbitrario). Define y documenta si las ventanas son fijas y ancladas al `t0` del experimento, o deslizantes. Por defecto: **fijas, no solapadas, ancladas al primer timestamp**.
2. **Por cantidad de señales consecutivas**: agrupar en bloques de `K` señales consecutivas en el orden cronológico global.

Casos borde que debes manejar explícitamente:
- Grupo final incompleto (menos de `K` señales, o ventana temporal parcial): política configurable — **descartar** o **calcular igualmente marcando el grupo como parcial**. Por defecto: calcular y marcar.
- Ventanas temporales vacías: se omiten, no generan punto.
- Timestamp representativo del grupo: por defecto el **centro temporal del grupo**; documenta la elección.
- Interacción con el filtrado (§7): decide y documenta si el agrupamiento se recalcula sobre las señales supervivientes tras un filtro, o si se mantiene fijo respecto al conjunto original. **Recomendación: recalcular**, y advertirlo en la interfaz.

### 4.3 Separación tiempo / frecuencia

Toda métrica declara su dominio (`tiempo` o `frecuencia`). Esta clasificación:
- Determina su ubicación en la interfaz (listas separadas visualmente).
- Es un simple atributo del registro de métrica; **no** debe implicar dos motores de cálculo distintos.

Para métricas en frecuencia, centraliza el cálculo del espectro: una sola transformada por señal, cacheada y reutilizada por todas las métricas frecuenciales que la requieran dentro de la misma pasada. **No recalcules la FFT por métrica.**

### 4.4 Arquitectura del registro de métricas (extensibilidad)

Implementa un **registro por decorador**. Agregar una métrica nueva debe consistir en crear un archivo en `metrics/` con una función decorada, sin tocar ninguna otra parte del código. La interfaz descubre las métricas disponibles introspeccionando el registro en tiempo de arranque.

Cada métrica registrada declara como mínimo: identificador único y estable, nombre legible, régimen, dominio, unidades, parámetros con valores por defecto, y una **versión** que participa en la clave de caché.

Las funciones de métrica deben ser **puras y vectorizadas**: sin estado global, sin efectos secundarios, operando sobre arrays NumPy. Prohibido iterar señal por señal en Python cuando la operación admita vectorización sobre el eje de señales.

---

## 5. VISUALIZACIÓN — LOS TRES TIPOS DE GRÁFICA

### 5.1 Gráfica tipo #1 — Serie temporal global + variables ambientales

- **Eje horizontal**: tiempo completo del experimento.
- **Contenido señal**: representación **diezmada min/max**. Para cada señal se calcula su **mínimo y su máximo**, y se dibuja una **barra vertical** que abarca ese rango, posicionada en el timestamp de la señal. El resultado es la envolvente completa del experimento sin renderizar millones de puntos.
- **Contenido ambiental**: temperatura y humedad relativa como trazas continuas sobre eje secundario. No requieren diezmado.
- **Marcas de evento**: líneas verticales en los timestamps de eventos de botón.
- **Optimización obligatoria**: el par (min, max) por señal se calcula **una sola vez durante la ingesta** y se persiste. Nunca se recalcula en tiempo de render. Si el número de señales visibles supera el número de píxeles horizontales disponibles, aplica un segundo nivel de diezmado por *bins* de píxel (min de los mins, max de los maxs) — esta agregación debe ser exacta, no un submuestreo.

### 5.2 Gráfica tipo #2 — Señal individual

- Muestra **una señal individual** en el tiempo, a resolución completa. La duración del eje depende del sensor: **≈ 1 µs para UHF**, **≈ 100 ms para AE** (ver §2.1). El eje horizontal se rotula automáticamente en la unidad natural del sensor.
- **UHF**: se renderiza sin diezmado, a resolución completa.
- **AE**: dado que la traza es ~10⁵ veces más larga, el número de muestras puede exceder ampliamente los píxeles disponibles. Aplica **diezmado min/max por bin de píxel** (agregación exacta, nunca submuestreo) al renderizar la traza completa, y **restaura automáticamente la resolución completa al hacer zoom** sobre un tramo. Este comportamiento debe ser transparente para el usuario e indicarse en la interfaz cuando la vista esté diezmada.
- Debe soportar la visualización de **un grupo de señales seleccionadas** superpuestas para inspección comparativa.
- **Navegación requerida:**
  - Campo de entrada numérico para saltar a una señal por su **índice cronológico** (ej.: escribir `450` y ver la señal #450).
  - Botones de navegación **anterior / siguiente**.
  - Sincronización bidireccional con la selección activa en las otras gráficas.
- **Panel de metadatos**: muestra de forma permanente y visible el **trigger** y la **escala vertical** de la señal en pantalla, junto con su timestamp absoluto e índice.

### 5.3 Gráfica tipo #3 — Evolución de métricas

- **Eje horizontal**: tiempo (duración completa del experimento), en el **mismo dominio y rango** que la gráfica tipo #1 para permitir comparación visual directa.
- **Eje vertical**: valor de la métrica (puntual o de grupo).
- **Tipo de trazado**: **scatter puro**. **Prohibido** dibujar líneas de tendencia, ajustes, regresiones, medias móviles o cualquier elemento derivado. Solo los puntos.
- Ordenación cronológica estricta.
- Marcas de evento igual que en tipo #1.

---

## 6. INTERFAZ GRÁFICA — ESTRUCTURA Y DISTRIBUCIÓN

### 6.1 Ventanas gemelas por sensor

Se generan **dos ventanas completas e independientes**: una para **UHF** y otra para **AE**. Ambas comparten exactamente la misma estructura, componentes y comportamiento; difieren únicamente en la fuente de datos. **Deben implementarse con el mismo componente parametrizado**, no duplicando código.

### 6.2 Distribución espacial dentro de cada ventana

**Columna izquierda — panel de control** (de arriba hacia abajo):

1. **Botón de selección de base de datos**, en la esquina superior izquierda. Al activarse, abre un explorador para buscar y seleccionar la base de datos a analizar.
2. Inmediatamente debajo: **lista de métricas puntuales**, visualmente segmentada en dos bloques:
   - Métricas de **dominio del tiempo**
   - Métricas de **dominio de la frecuencia**
3. Más abajo: **lista de métricas de grupo**, con la misma segmentación tiempo/frecuencia, acompañada de:
   - Selector del **criterio de agrupamiento** (por tiempo | por cantidad de señales).
   - Campo de entrada para el **valor** del criterio (segundos, o número de señales).

**Columna derecha — área de gráficas:**

- Las **tres gráficas distribuidas verticalmente**: tipo #1 arriba, tipo #2 en medio, tipo #3 abajo.

### 6.3 Ventanas adicionales de métricas (control de saturación)

Problema a resolver: al graficar más de dos métricas en la misma ventana, un diseño responsivo contraería las gráficas y degradaría la legibilidad. **Esto no es aceptable.**

Solución requerida:

- Un **botón dedicado** que genera bajo demanda una **nueva ventana gemela de métricas**.
- Esta ventana **no contiene** las gráficas tipo #1 ni tipo #2. Contiene **exclusivamente gráficas tipo #3**.
- Límite de **máximo 4 gráficas por ventana**.
- El usuario puede generar **tantas ventanas adicionales como desee**; cada una absorbe hasta 4 métricas más.
- Las gráficas mantienen **altura fija**; la ventana hace scroll si es necesario. **No se contraen.**
- Todas las ventanas adicionales participan plenamente del sistema de filtrado cruzado (§7) y comparten el mismo estado de selección.

### 6.4 Elección de tecnología de interfaz

`❓ DECIDIR` — El requisito de **herramienta lazo de Plotly** con filtrado cruzado bidireccional condiciona fuertemente la elección. Evalúa y justifica entre:

- **Dash (Plotly)** — soporte nativo de `selectedData`, callbacks de filtrado cruzado, multi-ventana vía múltiples pestañas/ventanas de navegador sobre un backend compartido. *Opción por defecto salvo argumento en contra.*
- **PyQt/PySide + QWebEngineView** embebiendo figuras Plotly — ventanas nativas reales, pero puente JS↔Python más frágil.

Justifica la decisión explícitamente en términos de: soporte del lazo, gestión de estado compartido entre ventanas, latencia de callbacks, y facilidad de empaquetado.

---

## 7. SELECCIÓN Y FILTRADO CRUZADO (BIDIRECCIONAL)

Este es uno de los requisitos funcionales más importantes del sistema.

### 7.1 Comportamiento requerido

- El usuario puede seleccionar señales con la **herramienta lazo de Plotly** (y también selección rectangular) desde:
  - La **gráfica de serie temporal** (tipo #1), o
  - **Cualquier** gráfica de métricas (tipo #3), en cualquier ventana.
- Sobre la selección, puede **filtrar / eliminar** las señales.
- La propagación es **bidireccional y total**:
  - Si se filtra desde la gráfica de métricas → las señales correspondientes **desaparecen también de la serie temporal**.
  - Si se filtra desde la serie temporal → las métricas correspondientes **desaparecen también de todas las gráficas de métricas**, en todas las ventanas abiertas.

### 7.2 Diseño exigido

- **Fuente única de verdad**: un único estado de selección/exclusión por sensor, expresado como **máscara booleana sobre el índice cronológico global**. Todas las gráficas son **vistas derivadas** de esa máscara. Ninguna gráfica mantiene estado de filtrado propio.
- El filtrado es **no destructivo**: nunca se modifican ni se borran los datos subyacentes. Solo se altera la máscara.
- **Pila de operaciones** con soporte de **deshacer/rehacer** y un botón de **restablecer todo**.
- Indicador permanente en la interfaz: número de señales activas / totales, y número de filtros aplicados.
- Propagación métrica de grupo ↔ señal: al filtrar un punto de una métrica **de grupo**, debe quedar definido y documentado qué ocurre con las señales que componen ese grupo (recomendación: se excluyen todas las señales del grupo, y se advierte al usuario).
- Reconciliación: si el agrupamiento se recalcula tras un filtro (§4.2), las gráficas de métricas de grupo deben refrescarse de forma consistente.

---

## 8. SISTEMA DE CACHÉ DE MÉTRICAS

### 8.1 Requisito funcional

Al solicitar el cálculo de una métrica, el sistema debe:

1. **Verificar primero** si esa métrica ya fue calculada previamente.
2. Si existe en caché → **devolver directamente la lista de puntos** para graficar, sin recalcular.
3. Si no existe → calcular, **graficar**, y **persistir los puntos** en la base de datos de caché para futuras consultas.

Objetivo explícito: **incrementar la velocidad de visualización**.

### 8.2 Diseño exigido

- Base de datos de caché **separada** de la base de datos origen. La base origen se trata como **estrictamente de solo lectura**.
- **Clave de caché** compuesta y determinista, que incluya como mínimo:
  - Identificador del dataset origen (ruta + hash de contenido o huella estable)
  - Sensor (UHF / AE)
  - Identificador y **versión** de la métrica
  - Parámetros de la métrica (serializados de forma canónica y ordenada)
  - **Versión de la regla de normalización**
  - Criterio y valor de agrupamiento (para métricas de grupo)
- **Invalidación automática**: cualquier cambio en la versión de la métrica o de la normalización produce una clave distinta, dejando la entrada anterior obsoleta. Incluye una utilidad de purga de entradas huérfanas.
- Almacenamiento de los resultados en formato binario compacto (arrays, no filas individuales) para lectura rápida.
- **Precalentamiento en segundo plano**: al cargar un dataset, calcular de forma asíncrona las métricas más usadas sin bloquear la interfaz.
- El caché almacena resultados sobre el **conjunto completo de señales**, no sobre subconjuntos filtrados. El filtrado se aplica como máscara sobre los resultados cacheados — **nunca dispara un recálculo** de métricas puntuales.

`❓ DECIDIR` — Motor del caché: **SQLite** (simple, portable, transaccional) vs **DuckDB** (analítico, columnar, mejor para agregaciones masivas). Justifica.

---

## 9. REQUISITOS DE RENDIMIENTO Y OPTIMIZACIÓN

Estos requisitos son **de primera clase**, al mismo nivel que los funcionales.

### 9.1 Reglas duras

- **Vectorización obligatoria**: prohibido iterar en Python sobre señales individuales cuando NumPy permita operar sobre el eje completo. Toda métrica puntual debe poder aplicarse por lotes sobre una matriz `(N × M)`.
- **Cero recálculo redundante**: min/max por señal, espectros y estadísticos base se calculan una vez y se reutilizan.
- **Carga perezosa**: la matriz global no se carga entera en RAM. Acceso por bloques (*chunked I/O* a nivel de almacenamiento, no del concepto eliminado de chunk de la base origen).
- **Paralelismo**: el cálculo de métricas sobre bloques de señales debe paralelizarse (`multiprocessing` / `joblib` / `concurrent.futures`), con el nivel de paralelismo configurable. Evalúa **Numba** para los núcleos numéricos más costosos y justifica su uso o descarte.
- **UI no bloqueante**: ninguna operación de cálculo puede congelar la interfaz. Cálculos largos se ejecutan en segundo plano con **indicador de progreso** y posibilidad de **cancelación**.
- **FFT**: usa una implementación de alto rendimiento (`scipy.fft` con workers, o `pyFFTW` si se justifica). Precalcula planes cuando el tamaño de señal sea constante.

### 9.2 Objetivos medibles

Define y reporta estos indicadores en la documentación final:

| Operación | Objetivo |
|---|---|
| Ingesta y construcción de la matriz global | Reportar throughput (señales/s) |
| Render inicial de gráfica tipo #1 | < 2 s para el dataset completo |
| Cálculo de una métrica puntual sobre el dataset completo | Reportar y optimizar |
| Lectura de una métrica desde caché | < 200 ms |
| Aplicación de un filtro con propagación a todas las ventanas | < 200 ms |
| Cambio de señal en gráfica tipo #2 (UHF) | < 100 ms |
| Cambio de señal en gráfica tipo #2 (AE, vista diezmada) | < 250 ms |

**Todos los indicadores deben reportarse por separado para UHF y para AE.** Un número agregado oculta el hecho de que las trazas AE son ~10⁵ veces más largas y es donde aparecerán los cuellos de botella reales.

### 9.3 Instrumentación

Incluye desde el inicio un módulo de *profiling* y logging estructurado de tiempos por etapa, activable por configuración. **No se optimiza lo que no se mide.**

---

## 10. ARQUITECTURA Y ESTRUCTURA DEL PROYECTO

### 10.1 Principios

- **Separación estricta de capas**: `datos` → `procesamiento` → `caché` → `presentación`. Ninguna capa superior conoce los detalles internos de la inferior. La capa de presentación no contiene lógica de cálculo.
- **Inyección de dependencias** en los puntos de variabilidad (lector de base de datos, backend de caché, backend de almacenamiento).
- **Configuración externalizada** en archivo (YAML/TOML): rutas, parámetros por defecto, nivel de paralelismo, límites de la interfaz.
- **Tipado estático completo** (`type hints` en todo el código público), verificado con `mypy`.
- **Docstrings** en todas las funciones públicas, incluyendo la definición matemática en las métricas.

### 10.2 Estructura de directorios propuesta

Propón una estructura equivalente a la siguiente y ajústala si lo justificas:

```
proyecto/
├── config/                  # Configuración externalizada
├── core/
│   ├── models.py            # Modelo de datos canónico
│   ├── normalization.py     # Normalización por escala vertical (versionada)
│   └── grouping.py          # Criterios de agrupamiento
├── data/
│   ├── readers/             # Lectores de BD origen (interfaz + implementaciones)
│   ├── ingest.py            # Desempaquetado de chunks → matriz global
│   └── storage.py           # Persistencia de la matriz global
├── metrics/
│   ├── registry.py          # Registro por decorador
│   ├── engine.py            # Orquestador y paralelismo
│   ├── spectral.py          # Cálculo centralizado y cacheado de espectros
│   ├── time_domain/         # Una métrica = un módulo
│   └── freq_domain/
├── cache/
│   ├── backend.py           # Interfaz de caché
│   └── keys.py              # Construcción de claves deterministas
├── ui/
│   ├── app.py               # Punto de entrada
│   ├── windows/             # Ventana de sensor, ventana de métricas
│   ├── components/          # Panel de control, gráficas tipo 1/2/3
│   ├── state.py             # Fuente única de verdad de la selección
│   └── callbacks/           # Filtrado cruzado bidireccional
├── viz/
│   └── decimation.py        # Diezmado min/max
├── utils/                   # Logging, profiling
└── tests/
```

### 10.3 Extensibilidad — pruebas de fuego

El diseño debe superar estas tres pruebas. Demuéstralo explícitamente en la documentación:

1. **Agregar una métrica nueva** → crear un archivo con una función decorada. Cero cambios en el núcleo, cero cambios en la interfaz.
2. **Agregar un tercer tipo de sensor** → registrar una nueva configuración de sensor. Cero duplicación de la lógica de ventana.
3. **Cambiar el motor de la base de datos origen** → implementar un nuevo *reader*. Cero cambios aguas arriba de la capa de datos.

---

## 11. CALIDAD Y VERIFICACIÓN

- **Pruebas unitarias** para cada métrica, contra señales sintéticas de resultado analítico conocido.
- **Pruebas de propiedad** para el agrupamiento: cobertura completa, sin solapamiento, manejo correcto de grupos parciales.
- **Pruebas de consistencia del caché**: calcular con caché frío vs. caliente debe producir resultados idénticos bit a bit.
- **Pruebas del filtrado cruzado**: filtrar desde tipo #1 y desde tipo #3 debe converger al mismo estado de máscara.
- **Prueba de regresión de normalización**: verificar que ninguna métrica se calcula sobre señal sin normalizar.
- **Prueba de asimetría entre sensores**: ejecutar la batería completa con trazas sintéticas UHF (1 µs) y AE (100 ms) para verificar que ninguna dimensión temporal, tamaño de ventana FFT ni tamaño de lote está cableado. Debe fallar de forma explícita si algún módulo asume la duración de un sensor.
- **Benchmarks reproducibles** para las operaciones de §9.2.
- Manejo de errores explícito: base de datos corrupta, chunks malformados, señales de longitud inconsistente, metadatos ausentes (trigger o escala vertical faltantes → la señal se marca y **no** se usa para métricas, con aviso en la interfaz).

---

## 12. PLAN DE ENTREGA POR FASES

**No implementes todo de una vez.** Entrega y valida conmigo fase por fase.

**Fase 0 — Diseño (SIN CÓDIGO).** Entrega: resolución razonada de todos los puntos `❓ DECIDIR`; diagrama de arquitectura de capas; esquema del modelo de datos canónico; **perfil de configuración por sensor** (duración, frecuencia de muestreo, muestras por traza, presupuesto de memoria, parámetros espectrales por defecto para UHF y para AE); diseño de la clave de caché; contrato de la interfaz de métricas; estructura de directorios definitiva; lista de supuestos y preguntas abiertas. **Espera mi aprobación.**

**Fase 1 — Núcleo de datos.** Lector de la base origen, desempaquetado de chunks, construcción y persistencia de la matriz global, cálculo y persistencia de min/max por señal, carga de ambientales y eventos, normalización versionada.

**Fase 2 — Motor de métricas.** Registro, motor paralelo, agrupamiento, espectros centralizados, y las métricas del listado de §4.1 con sus pruebas.

**Fase 3 — Caché.** Backend, claves, invalidación, precalentamiento en segundo plano.

**Fase 4 — Interfaz base.** Una ventana de sensor completa: panel de control, gráficas tipo #1, #2 y #3, navegación de señal, panel de metadatos.

**Fase 5 — Ventanas gemelas y adicionales.** Parametrización UHF/AE, generación bajo demanda de ventanas de métricas (máx. 4 gráficas cada una), estado compartido.

**Fase 6 — Filtrado cruzado.** Lazo, máscara única, propagación bidireccional total, deshacer/rehacer/restablecer.

**Fase 7 — Optimización y documentación.** Profiling, benchmarks contra §9.2, documentación de usuario y de arquitectura, guía de "cómo agregar una métrica".

---

## 13. FORMATO DE RESPUESTA ESPERADO

Para cada fase entrega, en este orden:

1. **Decisiones de diseño** tomadas y su justificación técnica.
2. **Código completo y ejecutable**, con tipado y docstrings. Sin fragmentos incompletos ni `TODO` sin explicar.
3. **Pruebas** correspondientes.
4. **Supuestos asumidos** y **preguntas abiertas** que requieran mi confirmación.
5. **Riesgos de rendimiento** detectados y cómo se mitigan.

Si en cualquier punto una especificación es ambigua o detectas un conflicto entre requisitos, **detente y pregunta antes de asumir**. Prefiero una pregunta a una implementación equivocada.
