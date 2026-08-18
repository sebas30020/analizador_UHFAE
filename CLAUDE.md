# Analizador de Señales UHF / AE

Herramienta local de un solo usuario para análisis de descargas parciales. Dash + Python
3.13 sobre Windows. Dos sensores con escalas temporales muy distintas: **UHF** (3 GS/s,
3000 muestras = 1 µs por traza) y **AE** (100 kS/s, 10 000 muestras = 100 ms).

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
mypy core data metrics cache ui viz utils          # sin config: la lista de paquetes es explícita
python -m benchmarks.run_benchmarks --dataset RUTA.hdf5 --repeats 5
ANALIZADOR_PROFILING=1 python scripts/run_dev_server.py    # tiempos por etapa en el log
python scripts/run_dev_server.py                   # http://127.0.0.1:8050/sensor/UHF
```

No hay `pyproject.toml` ni `mypy.ini`: mypy corre con defaults sobre esa lista de paquetes.
Las dependencias están pinneadas exactas en `requirements.txt` — no las subas de versión de
paso mientras haces otra cosa.

## Invariantes que es fácil romper sin darse cuenta

Estas no son preferencias de estilo; romperlas produce resultados incorrectos que no fallan
ruidosamente. `docs/ARQUITECTURA.md` explica el porqué de cada una.

- **Ninguna métrica normaliza por su cuenta.** La regla es única y versionada en
  `core/normalization.py`. Si cambia la regla, sube su versión: eso invalida el caché solo.
- **La clave de caché debe cubrir todo lo que altera el resultado.** Si agregas un parámetro
  que cambia el valor calculado y no entra en `cache/keys.py`, sirves resultados viejos.
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
