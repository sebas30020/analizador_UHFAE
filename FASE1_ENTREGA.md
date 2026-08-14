# Fase 1 — Núcleo de datos (entrega)

Formato de respuesta según PROMPT maestro §13. Alcance: lector de la base origen,
desempaquetado de chunks, construcción y persistencia de la matriz global, cálculo y
persistencia de min/max por señal, carga de ambientales y eventos, normalización
versionada — tal como delimita el PROMPT §12 para esta fase.

## 1. Decisiones de diseño

- **Proyecto y entorno:** creado en esta misma carpeta (`metricas/`), con venv propio
  (`.venv/`, Python 3.13) y `requirements.txt` acotado a lo que necesita esta fase
  (numpy, scipy, h5py, PyYAML, pytest, mypy) — Dash/joblib se añaden cuando corresponda.
- **`config/sensors.yaml`** materializa el perfil por sensor de `FASE0_DISENO...md` §6
  con los valores verificados sobre datos reales (UHF: 3 GHz/3000 muestras/100 MHz;
  AE: 100 kHz/10000 muestras/50 kHz), más el tamaño de bloque de lote y la versión de
  normalización — ninguna de estas cifras está cableada en el código.
- **`core/models.py`** define el modelo canónico (`SensorConfig`, `SignalBlock`,
  `EnvironmentalSeries`, `EventSeries`, `IngestResult`) y el eje de tiempos intra-señal
  se reconstruye bajo demanda (`SensorConfig.time_axis()`), no se persiste por fila.
- **`core/normalization.py`** implementa `x_norm = x_raw / vrange` (§3.1 FASE0) y **no**
  aplica corrección de línea base — decisión explícita del usuario tras la auditoría
  contra el PDF de referencia (`AUDITORIA_FORMULAS_PDF_vs_metricas.md` §1, Opción C).
  `compute_valid_mask` excluye señales con `vrange` o `trigger` faltantes/no numéricos.
- **`data/readers/`** aísla el formato de origen tras `OriginReader` (ABC); `HDF5Reader`
  es la única clase que abre h5py directamente. Cambiar de motor de origen (prueba de
  fuego §10.3 del PROMPT) implica escribir otra implementación de `OriginReader`, cero
  cambios en `ingest.py` ni aguas arriba.
- **`data/ingest.py`** concatena todos los lotes de un sensor y **ordena explícitamente
  por timestamp** (no asume que el reader entregue orden cronológico) — verificado con
  un fixture que deliberadamente entrega chunks en desorden. Calcula `valid_mask` y
  `minmax` una sola vez aquí, nunca en tiempo de render (§5.1 del PROMPT).
- **`data/storage.py`** persiste en HDF5 canónico (chunked, gzip), con acceso aleatorio
  por fila (`get_signal_row`) y por bloques (`iter_blocks`) sin cargar el dataset
  completo — justificación de formato ya resuelta en Fase 0 §3.2.

## 2. Código

Archivos nuevos, todos tipados (`mypy core/ data/` sin errores):

```
config/sensors.yaml
core/models.py
core/normalization.py
data/readers/base.py
data/readers/hdf5_reader.py
data/ingest.py
data/storage.py
```

## 3. Pruebas

26 pruebas, todas en verde (`pytest tests/ -q` → `26 passed`):

- `tests/conftest.py`: fixture HDF5 sintético con casos borde deliberados — chunks en
  desorden cronológico, señal con `vrange=0`, señal AE con `trigger=NaN`, chunk sin
  señales, evento sin dataset `type`.
- `test_models.py`: perfil de sensor coincide con los valores reales verificados.
- `test_normalization.py`: `valid_mask` y `normalize`, incluida propagación de NaN.
- `test_hdf5_reader.py`: listado de experimentos, `dataset_id` estable, chunks vacíos
  omitidos, eventos sin `type` caen a `"SHOT"`, ambientales concatenados y ordenados.
- `test_ingest.py`: **ordena correctamente aunque los chunks lleguen desordenados**
  (la prueba más importante de esta fase), `valid_mask` correcta, `minmax` exacto,
  sensor vacío no rompe, orquestación de ambos sensores.
- `test_storage.py`: round-trip escritura/lectura exacto, incluida preservación de NaN;
  recorrido por bloques cubre todas las filas sin huecos ni solapes.
- `test_integration_real_data.py`: pipeline completo contra `med_5_ago_3.hdf5` real
  (se salta si el archivo no está disponible en la máquina) — confirma en datos reales:
  12 484 señales UHF (3000 muestras), 20 574 señales AE (10000 muestras), 98 eventos
  (`SHOT`/`PA`/`FO`), ambos sensores estrictamente ordenados y 100% de señales válidas.

## 4. Supuestos asumidos

- Los mismos ya registrados en `FASE0_DISENO...md` §10 (ubicación del proyecto, un
  archivo `.hdf5` = un experimento, paralelismo por defecto `cpu_count - 1`).
- La ingesta construye la matriz global en RAM antes de persistirla (no streaming),
  válido bajo el presupuesto de RAM asumido (§10 supuesto #7) y confirmado con el
  archivo real: ~9 s en escribir el canónico completo (150 MB UHF + 823 MB AE).
- `utils/logging.py` y `utils/profiling.py` (mencionados en la estructura de directorios
  de §9) se dejan como paquete vacío por ahora: no hay todavía un motor paralelo (Fase 2)
  cuyo tiempo por etapa valga la pena instrumentar. Se implementan al iniciar Fase 2,
  donde sí aportan (§9.3 del PROMPT).

## 5. Riesgos de rendimiento detectados

- **Ingesta en RAM sin streaming:** si un futuro archivo de origen supera el presupuesto
  de RAM de la máquina, `data/ingest.py` fallaría por memoria antes que degradar
  gradualmente. Mitigación diferida (no necesaria con los tamaños reales actuales):
  pasar a escritura incremental por bloques directamente contra `storage.py`.
- **Tamaño de chunk HDF5 del canónico vs. tamaño de bloque de lote:** `storage.py` usa
  un tamaño de chunk interno fijo (8 MB) independiente del `target_block_bytes` por
  sensor de `sensors.yaml`; en la práctica ambos son del mismo orden de magnitud, pero
  si se cambia agresivamente uno sin el otro podría degradar la localidad de lectura.
  Sin impacto medido todavía — a vigilar en los benchmarks de la Fase 7.

## Próximo paso

Fase 2 — Motor de métricas (registro por decorador, orquestador paralelo, agrupamiento,
espectro centralizado, y las 17 métricas puntuales + 3 de grupo intrínsecas del
listado, incorporando los ajustes de `AUDITORIA_FORMULAS_PDF_vs_metricas.md` §2–4:
reductor genérico por mediana para el régimen "grupo", unidad correcta de
`shannon_entropy`, y `T_w` siempre explícito en las métricas de tasa).
