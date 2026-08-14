# Analizador de Señales UHF / AE

Herramienta local de análisis de señales de descargas parciales capturadas por dos
sensores con escalas temporales muy distintas: **UHF** (3 GS/s, 3000 muestras = 1 µs por
traza) y **AE** (100 kS/s, 10 000 muestras = 100 ms por traza). Lee la base de datos
HDF5 de origen, la convierte en una matriz global ordenada cronológicamente, calcula
métricas de tiempo y frecuencia sobre ella y las presenta en una interfaz web local con
filtrado cruzado bidireccional.

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

Abre `http://127.0.0.1:8050/sensor/UHF` (o `/sensor/AE`). Las dos rutas son la misma
página servida con distinto sensor activo: para trabajar con ambos a la vez se abren en
**pestañas separadas** del navegador, que comparten el mismo proceso y por lo tanto el
mismo dataset cargado.

Con el servidor arriba, el botón **"Seleccionar base de datos…"** abre un explorador
nativo para elegir el `.hdf5` de origen. Al cargarlo se dispara en segundo plano el
precalentamiento del caché de las métricas más usadas, sin bloquear la interfaz.

## La ventana de sensor

De arriba abajo:

1. **Señal individual** (gráfica tipo #2) con sus controles: *Anterior* / *Siguiente*,
   índice directo, *Comparar con índices* para superponer otras señales, y *Ver señal
   cruda* para inspeccionar la traza sin normalizar. Debajo, la barra de metadatos:
   índice, timestamp UTC, trigger, escala vertical y si la vista está diezmada.
2. **Serie temporal global** (gráfica tipo #1): la envolvente min/max de **todas** las
   señales, con temperatura y humedad en el eje derecho y una línea vertical roja por
   evento. Aquí no hay diezmado: se dibuja un segmento por señal, sean 12 000 o 20 000.
3. **Gráficas de métricas** (tipo #3): una por cada métrica que se agregue desde el
   selector del panel lateral, apiladas con scroll de página.

En el panel lateral se eligen las métricas, el reductor de grupo (mediana, media,
percentil), el criterio de agrupamiento (por ventana temporal o por cantidad de señales)
y los controles de filtrado.

### Filtrado cruzado

Selecciona con el lazo o el rectángulo de Plotly sobre la gráfica #1 o sobre cualquier
gráfica #3, y confirma con **"Filtrar selección"**. La exclusión se propaga a todas las
gráficas de ese sensor a la vez: hay una sola máscara por sensor, y todas las vistas se
derivan de ella. *Deshacer* y *Rehacer* recorren el historial; *Restablecer todo* es un
borrón y cuenta nueva no deshacible. El indicador permanente muestra cuántas señales
siguen activas y cuántos filtros hay aplicados. UHF y AE nunca comparten máscara.

Seleccionar sobre una gráfica de métrica **de grupo** excluye todas las señales del
grupo, no solo la más cercana al clic.

---

## Documentación

| Documento | Contenido |
|---|---|
| [docs/ARQUITECTURA.md](docs/ARQUITECTURA.md) | Capas, flujo de datos, decisiones estructurales y las tres pruebas de fuego de extensibilidad |
| [docs/COMO_AGREGAR_UNA_METRICA.md](docs/COMO_AGREGAR_UNA_METRICA.md) | Guía paso a paso: una métrica nueva = un archivo nuevo |
| [docs/RENDIMIENTO.md](docs/RENDIMIENTO.md) | Indicadores medidos por sensor, metodología, y cómo usar la instrumentación |
| `FASE*_ENTREGA.md` | Bitácora de cada fase: qué se decidió y por qué |
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
