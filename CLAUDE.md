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
  filtrado.** El régimen de grupo hace bypass total del caché a propósito; escribir ahí el
  resultado filtrado envenena la vista sin filtro.
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
