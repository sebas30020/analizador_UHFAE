# Analizador de Señales UHF / AE

Herramienta local de un solo usuario para análisis de descargas parciales. Dash + Python
3.13 sobre Windows. Tres sensores con escalas temporales muy distintas: **UHF** (3 GS/s,
3000 muestras = 1 µs por traza), **AE** (100 kS/s, 10 000 muestras = 100 ms) y **UHF_KS**
(osciloscopio Keysight en memoria segmentada, 20 GS/s, 20 000 muestras = 1 µs por traza,
rama `lectura_keysight`). Dos formatos de origen reconocidos por contenido, no por
extensión (`data/readers/factory.py`): ver `archivos_md/esquema_med_5_ago_3.md` y
`archivos_md/esquema_keysight_h5.md`.

La documentación de este repo es buena y está al día — **léela antes de tocar código**, no
la repitas aquí:

| Para saber | Lee |
|---|---|
| Qué hace y cómo se usa la interfaz | `README.md` |
| Capas, flujo de datos, decisiones estructurales | `docs/ARQUITECTURA.md` |
| Agregar una métrica nueva | `docs/COMO_AGREGAR_UNA_METRICA.md` |
| Rendimiento e instrumentación | `docs/RENDIMIENTO.md` |
| Por qué se decidió algo | `FASE*_ENTREGA.md`, `archivos_md/` |

## Comandos

El intérprete es el del venv del repo, **no** el `python` del PATH:

```
.venv\Scripts\python.exe
```

```
pytest tests/ -q                                   # suite completa
mypy core data metrics cache ui viz utils          # la lista de paquetes es explícita
python -m benchmarks.run_benchmarks --dataset RUTA.hdf5 --repeats 5
ANALIZADOR_PROFILING=1 python scripts/run_dev_server.py    # tiempos por etapa en el log
python scripts/run_dev_server.py                   # http://127.0.0.1:8050/sensor/UHF
python scripts/run_server.py                       # waitress, monoproceso 8 hilos
```

No hay `pyproject.toml`. El único `mypy.ini` del repo tiene una sola línea,
`ignore_missing_imports = True`, y existe para callar los 28 `import-untyped` de plotly,
dash y demás dependencias sin stubs: sin él la salida es ruido puro y no se ve un error
real. No debilita nada del código propio — esos módulos ya se resolvían a `Any`. Todo lo
demás corre con defaults sobre esa lista de paquetes.
Las dependencias están pinneadas exactas en `requirements.txt` — no las subas de versión de
paso mientras haces otra cosa.

## Invariantes que es fácil romper sin darse cuenta

Estas no son preferencias de estilo; romperlas produce resultados incorrectos que no fallan
ruidosamente. `docs/ARQUITECTURA.md` explica el porqué de cada una.

- **Ninguna métrica normaliza por su cuenta.** La regla es única y versionada en
  `core/normalization.py`. Si cambia la regla, sube su versión: eso invalida el caché solo.
- **La clave de caché debe cubrir todo lo que altera el resultado.** Si agregas un parámetro
  que cambia el valor calculado y no entra en `cache/keys.py`, sirves resultados viejos.
  Excepción deliberada: `fs_hz`/`n_samples` **no** están en la clave aunque algunos
  orígenes (Keysight en memoria segmentada, `data/readers/keysight_reader.py`) los
  varíen por archivo vía `OriginReader.sensor_config_overrides()` — no hace falta,
  porque `dataset_id` (`ruta:tamaño:mtime_ns`) ya identifica el archivo 1 a 1, así que
  dos configuraciones efectivas distintas nunca comparten clave.
- **El caché almacena siempre el conjunto completo de señales, nunca un subconjunto
  filtrado.** El régimen de grupo hace bypass total del caché cuando la máscara excluye al
  menos una señal; escribir ahí el resultado filtrado envenena la vista sin filtro. Sin
  exclusiones (máscara todo-True o None), sí usa el caché con normalidad.
- **Régimen puntual = vectorizado sobre la matriz `(N, M)` entera**, nunca un bucle señal
  por señal.
- **`ingest_sensor` ordena explícitamente por timestamp.** No asumas que el orden de llegada
  del lector es cronológico, aunque en los datos reales coincida.
- **Una sola máscara de filtrado por sensor.** UHF y AE nunca la comparten.
- **Los callbacks de Dash se registran una sola vez** y resuelven el sensor en tiempo de
  render leyendo el Store `page-sensor`. No montes dos layouts a la vez.
- **Para saber a qué señal corresponde un punto de una gráfica, usa su posición
  (`curveNumber` + `pointNumber`), nunca `customdata`.** Con plotly.py 6.x el
  `customdata` **no llega al servidor** (typed array en base64 que el `filterEventData` de
  `dcc.Graph` no sabe indexar), pero el tooltip sí lo muestra: la funcionalidad aparenta
  estar bien mientras los callbacks no hacen nada. Y ojo con probarlo: un payload de test
  escrito a mano con `customdata` pasa sin que la app funcione. Ver `docs/ARQUITECTURA.md`
  §9 y `archivos_md/MAPAS_2D_3D_ENTREGA.md` §3.
- **`T_w` en métricas de grupo intrínsecas es la duración declarada de la ventana**, jamás
  inferida de los timestamps observados.
- **El desfase temporal de una fusión es uno solo para todo el archivo.** Calcularlo por
  sensor desincroniza UHF y AE entre sí y no falla ruidosamente. El cálculo se basa
  exclusivamente en señales (`t_fin_1` y `t_ini_2`), y se aplica a todas las series.
- **Resolución geométrica de lazo y caja en servidor.** La selección en la gráfica #1 se
  procesa en el servidor (`ui/callbacks/filtering.py`) evaluando la intersección del segmento
  vertical `[y_min, y_max]` con el polígono de `lassoPoints` o rectángulo de `range`.
  Nunca asumir que el cliente entrega la lista de `points` en una selección de área: con la
  envolvente en `mode="lines"` llega vacía siempre (medido en la app: `points: []`,
  `lassoPoints` con el polígono). El fallback por `points` se conserva por compatibilidad.
- **La gráfica #1 no puede quedarse sin ninguna traza con marcadores.** Plotly solo pone
  `select2d`/`lasso2d` en la barra de herramientas si alguna traza es "seleccionable", y
  una traza de líneas puras no lo es (`isSelectable`, `components/modebar/manage.js`). Las
  cuatro trazas visibles de esta gráfica son de líneas, así que el lazo depende por
  completo de la traza ancla invisible que añade `_build_selection_anchor_trace`
  (`ui/components/graph_timeseries.py`). Quitarla deja el filtrado por lazo inalcanzable
  desde la interfaz **sin romper ningún test de la lógica de filtrado**, que sigue verde.
  Forzar los botones con `modeBarButtonsToAdd` no funciona: Plotly los filtra igual.
- **La envolvente de la gráfica #1 va con `hoverinfo="skip"`.** Por debajo de
  `TOO_MANY_POINTS = 1e5` Plotly no construye el kd-tree de `scattergl` y recorre los
  61 722 puntos en cada evento de hover, cada `HOVERMINTIME = 50` ms: el hilo principal se
  satura y la pestaña se congela. Medido: 22,5 ms contra 3,3 ms por `mousemove`. Va junto
  con el diezmado de las ambientales a 2000 bins — sin él la ganancia se cae a 1,3x,
  que es justo lo que llevó a revertir este mismo cambio una vez (`docs/RENDIMIENTO.md`
  §1.2). No revertirlo sin volver a medir las dos cosas a la vez.
- **Envolvente de transporte en `float32`.** `build_vertical_segments` (`viz/decimation.py`)
  emite arrays `np.float32` para reducir un 50% el buffer binario base64 y ~44% el JSON
  transportado a la gráfica #1. El modelo canónico (`SignalBlock`), persistencia y cálculo
  analítico de métricas (`metrics/engine.py`) permanecen estrictamente en 64 bits (`float64`).
- **Umbral adaptativo WebGL en gráficas de métricas (`METRIC_WEBGL_THRESHOLD = 5000`).**
  `ui/components/graph_metric.py` conmuta a `go.Scattergl` exclusivamente en régimen
  puntual cuando $N > 5000$ puntos. Régimen de grupo y datasets pequeños usan siempre
  `go.Scatter` (SVG) para asegurar nitidez vectorial y evitar agotar el presupuesto de
  contextos WebGL del navegador (límite de 8-16 contextos).
- **Cero diezmado en la envolvente de la serie temporal.** La gráfica #1 dibuja un segmento
  vertical por señal activa sin diezmar (`3 × N` puntos: min, max, NaN), garantizando que
  todas las señales activas están representadas visualmente.


## Datos y caché

- Los `.hdf5` de origen **nunca se versionan** (ver `.gitignore`). No los muevas ni los
  copies al repo.
- Borrar `cache_data/` es siempre seguro: se reconstruye solo.
- El perfil por sensor (paralelismo, normalización, profiling) vive en `config/sensors.yaml`.

## Convenciones

- Documentación, comentarios y mensajes de commit **en español**, como el resto del repo.
- Una métrica nueva = un archivo nuevo en `metrics/time_domain/` o `metrics/freq_domain/`,
  registrado por decorador. Sigue `docs/COMO_AGREGAR_UNA_METRICA.md`.
- La capa de presentación no contiene lógica de cálculo. Si te ves calculando en `ui/`,
  el código va más abajo.
- Repo público (`sebas30020/analizador_UHFAE`): nada de datos de sensor, rutas locales
  sensibles ni credenciales en lo que se commitea.
