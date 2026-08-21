# Continuación: diagnóstico de rendimiento (sesión local)

> **CERRADO (2026-08-21).** El diagnóstico se hizo y sus resultados están en
> [`DIAGNOSTICO_RENDIMIENTO_RESULTADO.md`](DIAGNOSTICO_RENDIMIENTO_RESULTADO.md).
> Este documento queda como registro del plan y de lo que ya se había descartado.
> Ojo antes de reusarlo: la medición **refutó** sus dos hipótesis principales (§6) —
> el caché no estaba frío y el lock del caché no bloquea la interfaz.

> **Cómo usar este documento.** Abre Claude Code en la terminal, dentro de la carpeta del
> proyecto, y pega como primer mensaje:
>
> ```
> Lee archivos_md/CONTINUAR_DIAGNOSTICO_RENDIMIENTO.md y continúa el trabajo desde ahí.
> ```
>
> Está escrito para una sesión **sin memoria** de la conversación anterior: contiene todo
> el contexto necesario, qué ya se descartó (con evidencia, para no repetirlo) y el plan
> de acción con su árbol de decisión.

---

## 1. Por qué esta sesión tiene que ser local

El trabajo previo se hizo en una sesión **en la nube**: contenedor aislado, con un clon
del repositorio y sin acceso a la máquina del usuario. Eso bastó para escribir y probar
el código, pero no para diagnosticar el problema abierto, porque lo que hay que medir no
viaja por git:

- `D:\data\data\main\med_5_ago_3.hdf5` — los datos reales (`.gitignore`: `*.hdf5`).
- `cache_data/` — el caché local (`.gitignore`: `cache_data/`).

Esta sesión corre **en la máquina del usuario**, así que sí puede leer ambos. Ese es
todo el motivo del cambio de entorno.

---

## 2. Estado del repositorio

Rama `main`, sincronizada con `origin/main`. Últimos commits:

| Commit | Qué hizo |
|---|---|
| `e90f506` | Agrega `scripts/diagnostico_cache.py` |
| `ac65d62` | Corrige el selector de partición: cambiarlo no hacía nada |
| `65e0c6b` | Añade exportación de datos filtrados (resultantes/filtradas) a HDF5 |
| `c8d6421` | *(base: último commit antes de este trabajo)* |

**414 pruebas pasan, `mypy` sin errores de tipos reales.** Antes de tocar nada, confirma
que sigue así en esta máquina:

```
.venv\Scripts\python.exe -m pytest tests/ -q
.venv\Scripts\python.exe -m mypy core data metrics cache ui viz utils
```

(`mypy` reporta ruido preexistente de `import-untyped` por falta de *stubs* de
`h5py`/`plotly`/`scipy`/`joblib`. Eso es normal, no es una regresión.)

---

## 3. Qué se construyó (contexto de la funcionalidad)

Exportación de datos filtrados, documentada en detalle en
[`EXPORTACION_FILTRADA_ENTREGA.md`](EXPORTACION_FILTRADA_ENTREGA.md):

- Botón **"Exportar datos filtrados…"** → escribe un `.hdf5` con dos particiones,
  `/resultantes/` (activas tras el filtro) y `/filtradas/` (excluidas), más ambientales
  y eventos.
- `data/readers/filtered_export_reader.py` relee ese archivo como un `OriginReader` más.
- Selector **"Partición visible"**: cambia en vivo qué partición se muestra.

Esta funcionalidad **no está en duda**; está probada y verificada. El problema abierto es
otro.

---

## 4. El problema abierto

El usuario reporta que **la aplicación se siente lenta** tras actualizar. Concretamente,
las tres cosas a la vez:

1. Cargar el archivo.
2. Las gráficas al dibujarse.
3. Navegar entre señales.

No sabe en qué estado está su `cache_data/`, ni si el archivo que abrió es el de siempre.

---

## 5. Lo que YA se descartó — no lo repitas

Estas tres comprobaciones se hicieron con mediciones en la sesión anterior. **Dan por
descartado que el código nuevo sea la causa.** No hace falta rehacerlas:

### 5.1 A/B de rendimiento contra el commit anterior

Mismo dataset sintético (2000 señales UHF × 3000 muestras), `c8d6421` contra `ac65d62`:

| Operación | Antes | Ahora |
|---|---:|---:|
| `load_dataset` | 147 ms | 138 ms |
| Render gráfica #1 | 8,7 ms | 7,7 ms |
| Métrica `rms` en frío | 17,2 ms | 20,1 ms |
| Lectura desde caché | 1,7 ms | 1,8 ms |
| `normalize` del bloque | 6,4 ms | 4,7 ms |
| Construir el layout | 4,8 ms | 5,2 ms |

Todo dentro del ruido. **Sin regresión.**

### 5.2 La clave de caché no cambió

Misma clave SHA-256 (`a5491c02…`) antes y después, para la misma entrada. Si hubiera
cambiado, todo el caché quedaría invalidado y cada métrica se recalcularía — que sí
explicaría la lentitud. **No es el caso.**

### 5.3 No hay sondeo de fondo

Los dos `dcc.Interval` del layout (`export-status-poll` y `autoplay-interval`) nacen con
`disabled=True`. No hay callbacks disparándose solos.

---

## 6. Hipótesis principal: caché frío

Es la que mejor explica que los **tres** síntomas aparezcan juntos, y es la única que no
se ha podido comprobar (requiere esta máquina).

Medido sobre 2000 señales UHF × 3000 muestras:

```
métrica                 frío     caché   factor
rms                   4778ms     1.4ms    3361x
kurtosis               338ms     1.3ms     267x
```

Y hay un multiplicador: al cargar un archivo, `ui/state.py::load_dataset` dispara
`cache/warmup.py::start_background_warmup`, que precalcula **9 métricas por sensor** en
un hilo de fondo. Detalles relevantes verificados en el código:

- Es **un solo hilo**, y recorre las métricas **en serie** (`for spec in specs:`).
- `compute_puntual` usa `n_workers=1` — sin paralelismo, decisión medida
  (`metrics/engine.py`: con 8 procesos tardaba *más*).
- `cache/backend.py` protege `get()` y `put()` con **un único `threading.Lock` global**,
  sostenido mientras se abre y comprime el HDF5 de payload. Mientras el warmup escribe,
  cualquier lectura de caché desde la interfaz **espera en ese lock**.

Con el caché caliente todo eso es imperceptible. Con el caché frío y el dataset real
(12 484 señales UHF + 20 574 AE) son minutos de CPU compitiendo con la interfaz.

**Ojo:** esto es una hipótesis, no un hecho comprobado. El paso 7.1 la confirma o la
refuta.

---

## 7. Plan de acción

### 7.1 Primero: medir el estado del caché

```
.venv\Scripts\python.exe scripts\diagnostico_cache.py "D:\data\data\main\med_5_ago_3.hdf5"
```

Ajusta la ruta al archivo que el usuario abrió realmente. Sin argumento, lista todos los
datasets con entradas en caché.

**No borres `cache_data/` mientras se investiga.** Es seguro en general (se reconstruye
solo), pero ahora destruiría justo la evidencia que se está midiendo.

### 7.2 Árbol de decisión según el resultado

**Caso A — 0 entradas para ese archivo (o caché vacío):**
Hipótesis confirmada, no hay bug. El caché está frío porque es un clon nuevo, se borró
`cache_data/`, o el archivo se movió/renombró/reescribió (su `dataset_id` es
`ruta:tamaño:mtime_ns`, así que cualquiera de esas cosas lo invalida aunque el contenido
sea idéntico).

Qué hacer: arrancar la app, cargar el archivo y **dejarla en reposo** hasta que el
warmup termine. Luego volver a medir. Si tras eso va fluida, cerrado.

Si al usuario le molesta ese primer arranque lento, hay opciones a discutir **con él**
antes de implementar nada:
- Reducir la lista de `default_warmup_specs` (hoy 9 métricas por sensor).
- Retrasar el warmup, o mostrar un indicador de progreso en la interfaz.
- Bajar el nivel de gzip del payload de caché (hoy 4) para acortar el tiempo con el lock
  tomado.

**Caso B — hay muchas entradas y aun así va lento:**
La hipótesis está mal y hay algo real que encontrar. Siguiente paso:

```
set ANALIZADOR_PROFILING=1
.venv\Scripts\python.exe scripts\run_dev_server.py
```

El log emite una línea por etapa con `clave=valor`, incluyendo `cache=hit|miss|bypass`.
Qué buscar:
- Una ristra de `cache=miss` → algo invalida la clave en cada petición.
- `cache=bypass` constante → hay una máscara de filtro activa: el régimen de grupo
  bypasea el caché a propósito (docs/ARQUITECTURA.md §5) y recalcula siempre.
- Etapas de `ui.*` lentas con `cache=hit` → el cuello está en el render o en el
  navegador, no en el cálculo.

Y con datos reales:

```
.venv\Scripts\python.exe -m benchmarks.run_benchmarks --dataset "D:\...\med_5_ago_3.hdf5" --repeats 5
```

Compara contra la tabla de `docs/RENDIMIENTO.md` §1. **Lee antes §1.1**: esos números
sirven para detectar regresiones de orden de magnitud, no diferencias del 20 % — la
variabilidad entre corridas en la misma máquina ya es grande.

**Caso C — el usuario menciona un síntoma distinto** (un error en pantalla, un diálogo
que no abre, gráficas vacías): abandona esta hipótesis y persigue el síntoma concreto.

---

## 8. Invariantes que no se pueden romper

De `CLAUDE.md`, y son las que producen resultados incorrectos sin fallar ruidosamente:

- Ninguna métrica normaliza por su cuenta (`core/normalization.py` es la regla única).
- La clave de caché debe cubrir todo lo que altera el resultado (`cache/keys.py`).
- El caché almacena **siempre** el conjunto completo, nunca un subconjunto filtrado.
- Régimen puntual = vectorizado sobre la matriz `(N, M)` entera, nunca señal por señal.
- Una sola máscara de filtrado por sensor; UHF y AE nunca la comparten.
- Los callbacks de Dash se registran una sola vez y resuelven el sensor leyendo el Store
  `page-sensor`.
- Identificar un punto de Plotly por posición (`curveNumber` + `pointNumber`), **nunca**
  por `customdata`.

Convenciones: documentación, comentarios y mensajes de commit **en español**. Repo
público — nada de datos de sensor, rutas locales sensibles ni credenciales en lo que se
commitea. Las dependencias de `requirements.txt` están pinneadas exactas: no subirlas de
versión de paso.

**Autoría de los commits**: el usuario pidió explícitamente que se hagan bajo su perfil,
`sebas30020 <sebastian.marin.quiceno.3@gmail.com>`, sin líneas de coautoría.

---

## 9. Antes de dar nada por cerrado

Si esta sesión termina cambiando código:

```
.venv\Scripts\python.exe -m pytest tests\ -q
.venv\Scripts\python.exe -m mypy core data metrics cache ui viz utils
```

Y una verificación en la app real con los datos reales — el fallo del selector de
partición (§2.1 de `EXPORTACION_FILTRADA_ENTREGA.md`) pasó las 408 pruebas y aun así no
funcionaba en uso. Las pruebas cubren la lógica pura; no cubren el cableado de Dash.
