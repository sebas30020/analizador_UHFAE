# Fase 2 — Motor de métricas (entrega)

Formato de respuesta según PROMPT maestro §13. Alcance (§12): registro por decorador,
motor paralelo, agrupamiento, espectros centralizados, y las métricas del listado con
sus pruebas — incorporando los ajustes de `AUDITORIA_FORMULAS_PDF_vs_metricas.md`.

## 1. Decisiones de diseño

- **Contrato uniforme `MetricContext`**: en vez de firmas distintas por combinación de
  necesidades (traza / espectro / timestamps / duración de ventana), toda función de
  métrica recibe un único objeto con campos opcionales y lee solo los que declara
  necesitar. Evita explosión combinatoria de firmas y mantiene "agregar una métrica =
  un archivo nuevo" sin tocar el motor (prueba de fuego §10.3 del PROMPT).
- **Régimen "grupo" con dos mecanismos** (AUDITORIA...md §2): reducción genérica
  (mediana por defecto, también `mean`/`percentile`) de cualquier métrica puntual sobre
  un grupo, y 3 métricas de grupo intrínsecas (`tasa_pulsos`, `tasa_energia`,
  `tasa_rafagas`) que no son reducción de nada, tienen fórmula propia a nivel de
  ventana.
- **`T_w` siempre explícito, nunca inferido** (AUDITORIA...md §4): `core/grouping.py`
  fija `T_w` = duración declarada de la ventana (modo `by_time`) o span real del propio
  grupo (modo `by_count`, único caso donde no hay duración declarada que usar). Las 3
  métricas de grupo intrínsecas y el motor nunca recalculan `T_w` desde los timestamps
  observados dentro de la ventana.
- **`shannon_entropy` declara unidad `""`** (adimensional), no `"(bits)"` — corrige el
  error de etiqueta heredado de `analizador_datos.py` (AUDITORIA...md §3).
- **Sin `np.nan_to_num` defensivo en kurtosis/skewness** (deviación consciente frente a
  `analizador_nuevo/metricas.py`): una señal de varianza cero produce NaN, no un valor
  fabricado (0, o 3 en Pearson) que aparentaría ser una gaussiana real. `engine.py` es
  quien decide si un punto se excluye, nunca la métrica misma.
- **Espectro centralizado** (`metrics/spectral.py`): una FFT vectorizada por bloque,
  compartida por todas las métricas con `requires_spectrum=True` de la misma pasada
  (`feq` hoy; cualquier métrica espectral futura la reutiliza automáticamente).
- **Régimen "grupo" excluye siempre señales inválidas**, de forma uniforme (puntual y
  grupo) — decisión de alcance documentada en `metrics/engine.py`, justificada porque el
  PROMPT §11 no reserva una excepción para el conteo de grupo.

## 2. Código

```
core/grouping.py
metrics/registry.py
metrics/spectral.py
metrics/engine.py
metrics/time_domain/{rms,vpp,vmax,kurtosis,skewness,crest_factor,shannon_entropy,zcr,
                      rise_time,teq,energia_relativa,energia_v2s,energia_joules,
                      delta_t,log_delta_t,tasa_pulsos,tasa_energia,tasa_rafagas}.py
metrics/freq_domain/{feq,f_aprox}.py
```

20 métricas registradas (17 puntuales: 15 tiempo + 2 frecuencia; 3 de grupo intrínsecas,
todas tiempo), verificado por `tests/test_registry.py`. `mypy core/ data/ metrics/` sin
errores (36 archivos).

## 3. Pruebas

**81/81 pruebas en verde** (`pytest tests/ -q`). Las nuevas de esta fase:

- `test_grouping.py`: ventanas fijas ancladas, ventanas vacías omitidas, `T_w` siempre
  la duración declarada (no el span observado), timestamp representativo = centro de
  ventana, marca de grupo parcial, modo `by_count` con `T_w` = span real del grupo.
- `test_registry.py`: descubrimiento completo (20/20), conteos por régimen/dominio,
  registro duplicado rechazado, unidad de `shannon_entropy` corregida.
- `test_spectral.py`: un tono puro pica en su propia frecuencia, recorte a
  `freq_limit_hz`, resultado vectorizado idéntico a calcularlo señal por señal.
- `test_metrics_time_domain.py` / `test_metrics_freq_domain.py` / `test_metrics_group.py`:
  **cada métrica con ecuación en el PDF se prueba contra el "Ejemplo inmediato" exacto
  del documento** (RMS, Crest Factor, Shannon Entropy, ZCR, Energía relativa/V²s/Joules,
  Δt, log Δt, Tasa de Pulsos, Frec. Aprox., conteo de ráfagas con los casos de τ y
  N_min del PDF). Las que no tienen ecuación en el PDF (kurtosis/skewness sin ejemplo
  numérico, VPP, Vmax, Rise Time) se prueban contra un resultado analítico calculado a
  mano o una señal sintética de referencia conocida (PROMPT §11).
- `test_engine.py`: exclusión de señales inválidas, normalización por `vrange` propio,
  paralelo vs. serial dan resultado idéntico, robustez de la mediana de grupo ante un
  outlier, grupos degenerados (`T_w=0`) omitidos — y la prueba más importante de esta
  fase (ver §4).

## 4. Dos bugs reales encontrados y corregidos al escribir las pruebas

1. **Frontera entre grupos en `delta_t`/`log_delta_t`** (`test_group_reduction_delta_t_respects_cross_group_boundary`):
   el diseño inicial de `compute_group_reduction` recalculaba la métrica "en frío"
   dentro de cada grupo, lo que reseteaba `Δt=0` al inicio de cada ventana en vez de
   medir contra el último pulso válido del grupo anterior. Se corrigió calculando estas
   métricas **una sola vez sobre toda la secuencia global** y recortando por grupo con
   los índices originales — documentado en el propio código (`metrics/engine.py`).
2. **Idempotencia falsa en `discover_metrics()`**: la guardia original ("si el registro
   no está vacío, ya se descubrió todo") se rompía si algún módulo importaba una sola
   métrica directamente (p. ej. `from metrics.time_domain.zcr import compute_zcr_array`,
   usado por `f_aprox.py` y por un test) **antes** de que nada llamara a
   `discover_metrics()`: el registro quedaba con 1 entrada y la guardia cortaba el
   descubrimiento del resto para toda la sesión. Se corrigió recorriendo siempre los 20
   módulos (`importlib.import_module` es barato para un módulo ya importado — no
   reejecuta el decorador, así que repetir el recorrido es seguro e idempotente por
   construcción de Python, sin necesitar una bandera de "ya completo").

## 5. Supuestos asumidos

- Los de `FASE0_DISENO...md` §10 y `FASE1_ENTREGA.md` §4 siguen vigentes.
- Timestamp representativo de grupo: centro de la ventana **declarada** (`by_time`) o
  del span real del grupo (`by_count`) — no el promedio de los timestamps de los pulsos
  dentro de la ventana (documentado en `core/grouping.py`).
- Ventana final parcial (`by_time`): se calcula y se marca `is_partial=True` si su fin
  declarado excede el último timestamp observado (política "calcular y marcar" del
  PROMPT §4.2). `T_w` no cambia por ser parcial, sigue siendo la duración configurada.

## 6. Riesgos de rendimiento detectados (medidos, no estimados)

- **El paralelismo (`n_workers>1`) es contraproducente a la escala actual del
  proyecto.** Medido sobre las 20 574 señales AE reales: RMS serial ≈ 3.4 s; RMS con
  8 procesos ≈ 39 s (11x más lento). El costo de arrancar procesos `loky` en Windows
  (spawn, sin `fork`) y reimportar numpy/scipy/h5py + redescubrir el registro de
  métricas en cada proceso hijo domina por completo sobre un cálculo vectorizado que ya
  es barato. **Mitigación aplicada:** el valor por defecto de `n_workers` sigue siendo
  `1`; la infraestructura paralela queda implementada y probada (exige el PROMPT que sea
  configurable) pero documentada explícitamente en `metrics/engine.py` como no
  recomendada hasta medir un caso donde el costo de arranque se amortice (dataset mucho
  mayor, o una métrica mucho más cara como CWT/Transformada S en fases futuras).
- **`zcr` con zona muerta (`dead_zone>0`) usa un bucle por señal**, no vectorizado sobre
  el eje de señales (documentado en `zcr.py`) — heredado del comportamiento ya validado
  de `analizador_nuevo/metricas.py`; la compactación irregular por fila no se vectorizó
  limpiamente en esta fase. No es un cuello de botella medido todavía (ZCR sobre las
  12 484 señales UHF tardó 0.82 s), pero es candidato a revisar si escala a datasets
  mucho mayores.
- **`compute_group_reduction` recalcula la métrica puntual dentro de cada grupo** (salvo
  para `requires_global_timestamps`, que sí se computa una vez global). Es correcto pero
  redundante frente a "calcular una vez y recortar por grupo" — no optimizado en esta
  fase porque el costo por grupo es pequeño (decenas a cientos de señales), a vigilar si
  el número de grupos crece mucho.

## Próximo paso

Fase 3 — Caché: backend SQLite (índice) + HDF5 (payload de arrays), construcción de la
clave determinista (§7 de FASE0_DISENO...md), invalidación automática por versión de
métrica/normalización, precalentamiento en segundo plano.
