# Analizador de Señales UHF / AE

Herramienta local de análisis de señales de descargas parciales capturadas por tres
sensores con escalas temporales muy distintas: **UHF** (3 GS/s, 3000 muestras = 1 µs por
traza), **AE** (100 kS/s, 10 000 muestras = 100 ms por traza) y **UHF_KS** (osciloscopio
Keysight en memoria segmentada, 20 GS/s, 20 000 muestras = 1 µs por traza). Lee la base
de datos de origen, la convierte en una matriz global ordenada cronológicamente, calcula
métricas de tiempo y frecuencia sobre ella y las presenta en una interfaz web local con
filtrado cruzado bidireccional.

Se reconocen dos formatos de origen, ambos en archivos `.h5`/`.hdf5` — la elección es
automática por contenido, no por extensión (`data/readers/factory.py`):

- **Esquema con chunks** (`med_5_ago_3.hdf5`, ver [archivos_md/esquema_med_5_ago_3.md](archivos_md/esquema_med_5_ago_3.md)): sensores UHF y AE, ambientales y eventos.
- **Keysight en memoria segmentada** (osciloscopio DSOSxxxA, ver [archivos_md/esquema_keysight_h5.md](archivos_md/esquema_keysight_h5.md)): un único canal de antena UHF, sensor `UHF_KS`, sin ambientales ni eventos.

Es una herramienta **de un solo usuario, en una sola máquina**: el servidor Dash y el
navegador corren en el mismo equipo, el estado vive en memoria de un proceso y el caché
es un SQLite + HDF5 local. No es un despliegue multiusuario ni está pensado para serlo.

---

## Puesta en marcha

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows;  source .venv/bin/activate en Linux/macOS
pip install -r requirements.txt

python scripts/run_dev_server.py  # http://127.0.0.1:8050
```

Abre `http://127.0.0.1:8050/sensor/UHF` (o `/sensor/AE`, `/sensor/UHF_KS`). Las tres
rutas son la misma página servida con distinto sensor activo: para trabajar con varios a
la vez se abren en **pestañas separadas** del navegador, que comparten el mismo proceso
y por lo tanto el mismo dataset cargado. Un dataset solo trae señales de los sensores
que su formato de origen ofrece (§ arriba) — la ventana de un sensor sin señales en el
archivo cargado lo indica con un aviso, en vez de mostrar tres gráficas vacías.

Con el servidor arriba, el botón **"Seleccionar base de datos…"** abre un explorador
nativo para elegir el archivo de origen (`.h5`/`.hdf5`, de cualquiera de los dos
formatos). Al cargarlo se dispara en segundo plano el precalentamiento del caché de las
métricas más usadas, sin bloquear la interfaz.

## La ventana de sensor

De arriba abajo:

1. **Señal individual** (gráfica tipo #2) con sus controles: *Anterior* / *Siguiente*
   (saltan las señales excluidas por el filtro), índice directo, **Auto-play** — recorre
   las señales activas en bucle a la velocidad elegida en el control junto al botón — ,
   *Comparar con índices* para superponer otras señales, y *Ver señal cruda* para
   inspeccionar la traza sin normalizar. Debajo, la barra de metadatos: índice,
   timestamp UTC, trigger (o "no registrado" en sensores como `UHF_KS` cuyo origen no lo
   mide), escala vertical y si la vista está diezmada.
2. **Serie temporal global** (gráfica tipo #1): la envolvente min/max de **todas** las
   señales, con temperatura y humedad en el eje derecho y una línea vertical roja por
   evento. Aquí no hay diezmado: se dibuja un segmento por señal, sean 12 000 o 20 000.
   Al hacer clic en cualquier punto de la gráfica se navega a la señal más cercana en el
   tiempo. La envolvente no muestra tooltip: queda fuera del cálculo de hover de Plotly, que
   con decenas de miles de puntos llegaba a bloquear la pestaña. Temperatura y humedad sí lo
   muestran.
3. **Gráficas de métricas** (tipo #3): una por cada métrica que se agregue desde el
   selector del panel lateral, apiladas con scroll de página.
4. **Mapas de separación** (tipos #4 y #5), siempre al final: ver abajo.

En el panel lateral se eligen las métricas, el reductor de grupo (mediana, media,
percentil), el criterio de agrupamiento (por ventana temporal o por cantidad de señales),
los ejes de los mapas y los controles de filtrado.

### Mapas de separación

Dos gráficas donde **un punto es una señal**, no una serie en el tiempo: sus coordenadas
son los valores de dos (mapa **2D**, tipo #4) o tres (mapa **3D**, tipo #5) métricas
cualesquiera para esa señal — los mismos valores que ya se ven en las gráficas de
métricas. Sirven para ver si distintos tipos de descarga se separan en el espacio de dos o
tres métricas.

Cada mapa se habilita con su propio checkbox y tiene sus propios selectores de eje; los
ejes ofrecen solo métricas **puntuales**, que son las que dan un valor por señal.
Deshabilitado no consume cómputo, pero conserva los ejes elegidos. Los mapas quedan
siempre debajo de las gráficas de métricas: agregar una métrica los empuja hacia abajo,
nunca al revés.

Al hacer clic en un punto, esa señal se dibuja en la gráfica #2 y queda resaltada en
ambos mapas a la vez. Si una señal no tiene valor finito para alguno de los ejes elegidos
se omite del mapa, y una nota discreta dice cuántas.

### Filtrado cruzado

Selecciona con el lazo o el rectángulo de Plotly sobre la gráfica #1, sobre cualquier
gráfica #3 o sobre el mapa 2D, y confirma con **"Filtrar selección"**. La exclusión se
propaga a todas las gráficas de ese sensor a la vez: hay una sola máscara por sensor, y
todas las vistas se derivan de ella. *Deshacer* y *Rehacer* recorren el historial;
*Restablecer todo* es un borrón y cuenta nueva no deshacible. El indicador permanente
muestra cuántas señales siguen activas y cuántos filtros hay aplicados. UHF y AE nunca
comparten máscara.

Seleccionar sobre una gráfica de métrica **de grupo** excluye todas las señales del
grupo, no solo la más cercana al clic.

El **mapa 3D no participa del filtrado por selección**: Plotly no ofrece lazo ni caja
dentro de una escena 3D. Sí responde al clic, igual que el 2D.

En la gráfica #1 la selección se resuelve **señal a señal**, respetando también la
amplitud: cada señal es un segmento vertical, y basta con que el lazo alcance uno de sus
dos extremos para incluirla. Un lazo que cruce el centro de un segmento sin tocar ninguno
de sus extremos no lo selecciona — Plotly solo conoce los vértices que dibuja.

### Exportar datos filtrados

El botón **"Exportar datos filtrados…"**, junto a los controles de filtrado, escribe a
un archivo `.hdf5` nuevo las señales de **todos** los sensores del dataset cargado, en
dos particiones separadas: las **resultantes** (activas tras el filtro) y las
**filtradas** (excluidas), cada una con sus trazas crudas, metadatos, y los
ambientales/eventos del experimento. La escritura corre en segundo plano, sin bloquear
la interfaz mientras dura.

El archivo exportado se puede volver a abrir con **"Seleccionar base de datos…"** como
un origen más. Con él cargado, el selector **"Partición visible"** cambia al instante
qué señales se ven — resultantes, filtradas, o ambas recombinadas (el conjunto original
completo, indistinguible de antes de filtrar) — recargando el archivo ya abierto, sin
volver a pasar por el explorador. La etiqueta bajo el botón dice siempre qué partición
está cargada.

Cambiar de partición **reinicia el filtrado**: cada partición es un conjunto de señales
distinto, no una vista del mismo. El selector es inerte para los otros dos formatos de
origen, que no tienen particiones.

---

## Documentación

| Documento | Contenido |
|---|---|
| [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) | Capas, flujo de datos, decisiones estructurales y las tres pruebas de fuego de extensibilidad |
| [docs/COMO_AGREGAR_UNA_METRICA.md](docs/COMO_AGREGAR_UNA_METRICA.md) | Guía paso a paso: una métrica nueva = un archivo nuevo |
| [docs/RENDIMIENTO.md](docs/RENDIMIENTO.md) | Indicadores medidos por sensor, metodología, y cómo usar la instrumentación |
| `FASE*_ENTREGA.md` | Bitácora de cada fase: qué se decidió y por qué |
| [archivos_md/MEJORA_GRAFICAS_ENTREGA.md](archivos_md/MEJORA_GRAFICAS_ENTREGA.md) | Control de eventos y suavizado de métricas en las gráficas #1/#3: diseño, rendimiento, auditoría de texto |
| [archivos_md/esquema_keysight_h5.md](archivos_md/esquema_keysight_h5.md) | Esquema del formato Keysight en memoria segmentada (sensor `UHF_KS`) |
| [archivos_md/LECTURA_KEYSIGHT_ENTREGA.md](archivos_md/LECTURA_KEYSIGHT_ENTREGA.md) | Lectura de bases de datos Keysight: decisiones, implementación y verificación |
| [archivos_md/AUTOPLAY_ENTREGA.md](archivos_md/AUTOPLAY_ENTREGA.md) | Auto-play de la gráfica #2: navegación consciente del filtrado, control de velocidad, verificación |
| [archivos_md/MAPAS_2D_3D_ENTREGA.md](archivos_md/MAPAS_2D_3D_ENTREGA.md) | Mapas de separación #4/#5: decisiones, dos bugs de identificación de puntos encontrados en uso, rendimiento |
| [archivos_md/EXPORTACION_FILTRADA_ENTREGA.md](archivos_md/EXPORTACION_FILTRADA_ENTREGA.md) | Exportación de datos filtrados: formato, reapertura como origen, rendimiento |
| `PROMPT_Analizador_Señales_UHF_AE.md` | Especificación maestra del proyecto |

## Desarrollo

```bash
pytest tests/ -q                  # suite completa
mypy core data metrics cache ui viz utils
python -m benchmarks.run_benchmarks --dataset RUTA.hdf5 --repeats 5
ANALIZADOR_PROFILING=1 python scripts/run_dev_server.py   # tiempos por etapa en el log
```

El caché vive en `cache_data/` y los datos `.hdf5` nunca se versionan (ver `.gitignore`).
Borrar `cache_data/` es siempre seguro: se reconstruye solo, cada entrada está atada a
una clave determinista que incluye el dataset, la métrica, su versión, la versión de
normalización y los parámetros de agrupamiento.
