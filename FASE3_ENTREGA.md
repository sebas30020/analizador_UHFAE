
# Fase 3 — Caché (entrega)

Formato de respuesta según PROMPT maestro §13. Alcance (§12): backend, claves,
invalidación, precalentamiento en segundo plano.

## 1. Decisiones de diseño

- **SQLite (índice) + HDF5 (payload)**, tal como se decidió y justificó en Fase 0 §3.3:
  `cache_index.sqlite` guarda una fila por clave (metadatos + puntero), los arrays
  `(timestamps, values, is_partial)` viven como grupos HDF5 en `cache_payload.h5`
  comprimidos con gzip. Evita BLOBs grandes dentro de SQLite.
- **Clave determinista** (`cache/keys.py`) exactamente según el diseño de Fase 0 §7:
  JSON canónico (`sort_keys=True`) + sha256. El campo `grouping` es `null` para régimen
  puntual y un objeto `{mode, value, partial_policy, [reducer, percentile_q]}` para
  régimen grupo — esto por sí solo separa limpiamente puntual/grupo-reducción/
  grupo-intrínseca sin necesitar un campo de "régimen" aparte en la clave.
- **`cache/service.py` como fachada nueva**, no prevista literalmente en el árbol de
  directorios de §9 de `FASE0_DISENO...md` (que solo listaba `backend.py`/`keys.py`).
  Es la capa que implementa el flujo de 3 pasos del PROMPT §8.1 (verificar → devolver
  si existe → calcular y persistir si no) conectando `cache/backend.py`,
  `cache/keys.py` y `metrics/engine.py`. Adición justificada dentro del propio módulo
  `cache/`, no toca ninguna otra capa.
- **Precalentamiento con conexión SQLite propia por hilo** (`cache/warmup.py`): en vez de
  compartir una instancia de `CacheBackend` ya abierta entre el hilo principal y el hilo
  de warmup (los objetos `sqlite3.Connection` no son seguros de compartir entre hilos
  por defecto), `start_background_warmup` abre su propia `SqliteHdf5CacheBackend` dentro
  del hilo daemon. Elimina el problema de raíz sin locks ni `check_same_thread=False`.
- **Invalidación automática real, no solo de diseño**: `purge_orphaned` compara
  `(metric_id, metric_version)` contra el registro de métricas activo y `dataset_id`
  contra los datasets que siguen existiendo — probado con un caso concreto (bump de
  versión de una métrica invalida su entrada vieja aunque el dataset siga igual).

## 2. Código

```
cache/keys.py
cache/backend.py
cache/service.py
cache/warmup.py
```

## 3. Pruebas

**118/118 pruebas en verde** (`pytest tests/ -q`), 37 nuevas de esta fase.
`mypy core/ data/ metrics/ cache/` sin errores (41 archivos).

- `test_cache_keys.py`: determinismo, insensibilidad al orden de los parámetros,
  cada campo de la clave (dataset, sensor, métrica+versión, parámetros, normalización,
  agrupamiento) produce una clave distinta al cambiar — incluida la separación
  puntual/grupo y `percentile_q` presente solo cuando el reductor es `percentile`.
- `test_cache_backend.py`: round-trip put/get, preservación de `is_partial`,
  sobrescritura sin duplicar fila de índice, purga de huérfanos por dataset eliminado
  y por versión de métrica obsoleta, reapertura del backend ve entradas previas.
- `test_cache_service.py`: **consistencia frío vs. caliente bit a bit** (PROMPT §11,
  `np.array_equal`, no `allclose`) para los 3 regímenes de cálculo; y la prueba más
  importante — **mutar la traza subyacente después del primer cálculo y verificar que
  la segunda llamada retorna el valor viejo cacheado**, no uno recalculado sobre datos
  mutados (demuestra que realmente no se recalculó, no solo que el resultado coincide
  por casualidad).
- `test_cache_warmup.py`: cobertura de specs por defecto, precalentamiento puebla todas
  las entradas esperadas, es idempotente (no duplica), y **la llamada a
  `start_background_warmup` retorna en <0.5 s** aunque el cálculo real tarde más
  (no bloqueante, verificado con un hilo real, no simulado).

## 4. Verificación del objetivo de rendimiento §9.2 del PROMPT

Medido sobre datos reales (UHF, `med_5_ago_3.hdf5`, 12 484 señales):

| Operación | Objetivo (PROMPT §9.2) | Medido |
|---|---|---|
| Lectura de una métrica desde caché | < 200 ms | **~15-17 ms** |

## 5. Supuestos asumidos

- Los de fases anteriores siguen vigentes.
- El caché es de un solo proceso local (§10 de Fase 0, "herramienta de un solo
  usuario"): no se implementó bloqueo entre procesos para escrituras concurrentes al
  mismo `cache_payload.h5` — aceptable bajo ese supuesto, a revisar si el alcance
  cambia a multi-proceso/multi-usuario.
- `purge_orphaned` es una operación explícita (el llamador decide cuándo ejecutarla,
  p. ej. al abrir la app), no se ejecuta automáticamente en cada `get`/`put` — evita el
  costo de escanear todo el índice en el camino caliente de lectura.

## 6. Riesgos de rendimiento

- Ninguno nuevo detectado en esta fase. El único riesgo heredado (paralelismo
  contraproducente a la escala actual, Fase 2 §6) no aplica aquí: el caché no lanza
  workers propios, solo evita repetir el cálculo serial ya medido.

## Próximo paso

Fase 4 — Interfaz base: una ventana de sensor completa en Dash (panel de control,
gráficas tipo #1/#2/#3, navegación de señal, panel de metadatos), conectada a
`cache/service.py` para todas las solicitudes de métricas.
