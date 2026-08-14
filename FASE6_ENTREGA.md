
# Fase 6 — Filtrado cruzado bidireccional (entrega)

Formato de respuesta según PROMPT maestro §13. Alcance (§7): selección con lazo/
rectángulo de Plotly desde la gráfica tipo #1 o cualquiera de las gráficas tipo #3
apiladas, propagación bidireccional total, fuente única de verdad por sensor,
deshacer/rehacer/restablecer, indicador permanente.

## 1. Decisiones de diseño

- **Máscara de exclusión aditiva, separada de `valid_mask`.** `AppState` (`ui/state.py`)
  guarda `_active_mask` por sensor (`True` = señal incluida), inicializada a todo-`True`
  en `load_dataset()` y combinada con la máscara de metadato inválido (`valid_mask`,
  Fase 1) solo en el punto de cómputo — nunca se funden en un único array persistente,
  así ninguna capa pierde la distinción entre "inválida por metadato" y "excluida por el
  usuario".
- **La conversión a minutos (Fase 5) evitó un problema de exactitud real.** Emparejar un
  punto seleccionado de una gráfica de grupo con su `Group` de origen no puede hacerse
  por igualdad exacta de `center_timestamp` porque el valor mostrado en pantalla ya pasó
  por `to_elapsed_minutes`/`elapsed_minutes_to_unix_seconds` (redondeo de punto
  flotante). Se resuelve por **coincidencia más cercana** (`nearest_group_index`, mismo
  patrón que `AppState.nearest_index_for_timestamp` para clic-navegar) sobre los grupos
  recalculados en caliente (`resolve_groups` es puro y barato) — el ruido (~1e-7 s) es
  inofensivo frente a cualquier `grouping-value` realista (segundos o más).
- **Bypass de caché total para métricas de grupo, filtro posterior puro para puntuales.**
  Excluir una señal cambia el agregado de un grupo (mediana/tasa recalculada), así que
  `get_or_compute_group_reduction`/`_intrinsic` (`cache/service.py`) nunca leen ni
  escriben caché cuando `active_mask` está activo — recalculan directo vía
  `metrics/engine.py`. Las métricas puntuales, en cambio, tienen correspondencia 1:1 con
  la señal (excluir una no cambia el valor de las demás), así que `get_or_compute_puntual`
  sigue usando el caché normal y solo aplica la máscara como filtro posterior en memoria
  — nunca dispara un recálculo (PROMPT §8.2).
- **`extra_mask` opcional y aditivo en `metrics/engine.py`**, nunca obligatorio: las tres
  funciones de cómputo (`compute_puntual`, `compute_group_reduction`,
  `compute_group_intrinsic`) ganaron un parámetro `extra_mask: np.ndarray | None = None`
  que por defecto reproduce el comportamiento exacto de antes de esta fase (verificado
  con pruebas de regresión byte a byte). Se introdujo `_effective_mask()` como único
  punto de combinación `valid_mask & extra_mask`, específicamente para que la rama
  `requires_global_timestamps` de `compute_group_reduction` (delta_t/log_delta_t) nunca
  desincronice sus dos variables enmascaradas entre sí — un riesgo real detectado al
  diseñar esta fase, con prueba de regresión dedicada (`test_compute_group_reduction_
  delta_t_extra_mask_reattributes_across_group_boundary`).
- **Selección de la gráfica #1 resuelta SOLO por rango de tiempo, nunca por punto.** La
  envolvente pasó de `mode="lines"` a `mode="lines+markers"` (necesario para que el lazo/
  rectángulo de Plotly haga hit-test sobre la traza), pero cada punto graficado puede ser
  un bin de diezmado que representa miles de señales reales cuando hay diezmado activo
  (`viz/decimation.py`) — así que la resolución de exclusión ignora `points` y usa
  únicamente `range.x` (selección rectangular) o el bounding-box de `lassoPoints.x`
  (lazo), convertido de minutos a UNIX y aplicado como filtro vectorizado sobre
  `block.timestamps` sin ninguna ambigüedad de agregación.
- **Selección de la gráfica #3 en régimen puntual reusa `nearest_index_for_timestamp`**
  (ya probado por el clic-navegar de Fase 4/5): 1 punto graficado = 1 señal real siempre,
  sin el problema de "grupo omitido" que sí tiene el régimen de grupo.
- **Selección de la gráfica #3 en régimen de grupo se expande al grupo completo**
  (PROMPT §7.2: "se excluyen todas las señales del grupo"): `resolve_group_selection_
  indices` mapea cada punto seleccionado a su `Group` más cercano y excluye todo
  `range(start_idx, end_idx)`, no solo la señal más próxima al clic.
- **"Seleccionar" y "filtrar" son dos pasos, no uno** (PROMPT §7.1: "puede seleccionar...
  puede filtrar"): la selección en curso se guarda en `pending-exclusion-indices`
  (`dcc.Store`) y solo se aplica a `AppState` al confirmar con el botón "Filtrar
  selección" — evita que un arrastre de lazo a medio hacer excluya señales sin querer.
- **Deshacer/rehacer con snapshots completos de la máscara**, no diffs: incluso AE con
  ~2×10⁴ señales son ~20KB por snapshot, irrelevante para una herramienta local de un
  solo usuario (mismo criterio de simplicidad que Fase 0). "Restablecer todo" es un
  **reseteo duro** deliberadamente no deshacible (limpia ambas pilas) y alcanza solo al
  sensor visible — UHF y AE nunca comparten máscara ni historial (PROMPT §7.2: "un único
  estado... por sensor").
- **`filter-version` espeja `dataset-version`** (mismo patrón de Fase 4/5: contador
  monotónico sembrado en un `dcc.Store` al renderizar la página, consumido como `Input`
  por los callbacks de refresco de gráfica #1/#3) para no reinventar la señal de "hay
  que re-renderizar".

## 2. Código

```
metrics/engine.py                       (+ _effective_mask, extra_mask en las 3 funciones de cómputo)
cache/service.py                        (+ active_mask: filtro post-hoc en puntual, bypass total en grupo)
ui/state.py                             (+ _active_mask/_undo_stack/_redo_stack/_filter_version + 8 métodos)
ui/callbacks/filtering.py               (nuevo: resolución pura de selección → índices)
ui/components/graph_timeseries.py       (+ active_mask obligatorio, traza a lines+markers)
ui/components/control_panel.py          (+ sección "Filtrado": indicador + 4 botones)
ui/components/sensor_window.py          (+ Stores filter-version, pending-exclusion-indices)
ui/callbacks/sensor_window_callbacks.py (+ 3 callbacks nuevos, active_mask en refresco de #1/#3)
```

## 3. Pruebas

**187/187 pruebas en verde** (`pytest tests/ -q`), 35 nuevas de esta fase:
- `tests/test_engine.py` (+7): `extra_mask=None` reproduce el resultado sin máscara
  byte a byte, exclusión de señal en puntual/reducción/intrínseca, el caso de
  regresión delta_t cruzando límite de grupo, grupo sin señales activas se omite.
- `tests/test_cache_service.py` (+5): puntual reutiliza la entrada de caché sin
  filtrar y nunca la reescribe; grupo con `active_mask` ni lee ni escribe caché en
  ninguna dirección.
- `tests/test_state.py` (+11): máscara inicial todo-`True`, aplicar/deshacer/rehacer/
  restablecer, no-op silencioso con pila vacía, aislamiento por sensor, reseteo al
  cargar un dataset nuevo, conteos del indicador.
- `tests/test_filtering.py` (nuevo, 15 pruebas): resolución de rango por selección
  rectangular y por lazo, `nearest_group_index` con grupos omitidos, expansión de
  grupo completo, unión sin duplicados, resolución puntual con función inyectada.

`mypy` sin errores nuevos en los 8 módulos tocados (solo el ruido preexistente de
bibliotecas sin *stubs* — plotly/h5py/scipy/joblib — ya presente antes de esta fase).

**Verificación manual con datos reales** (dataset real `med_5_ago_3.hdf5`, 12 484
señales UHF / 20 574 AE, precargado en `AppState` para saltar el diálogo nativo de
selección de archivo): confirmé por script que `compute_t0`, la app completa
(`create_app()`) y el servidor Dash arrancan sin error con el dataset cargado.

**Pendiente, no completado**: el smoke test en navegador real contra ese servidor
(arrastrar lazo/rectángulo real sobre la gráfica #1 y confirmar que `selectedData`
llega no vacío) — la extensión de Chrome no logró conectarse en este entorno en dos
intentos. Este es el punto de mayor incertidumbre del diseño (si Plotly reporta
selección sobre una traza `lines+markers` con miles de puntos) y **debería verificarse
manualmente antes de dar la fase por completamente cerrada**. Pasos para reproducirlo:
cargar un dataset real, seleccionar una métrica puntual y una de grupo (para tener
ambos regímenes de gráfica #3 apilados), hacer lazo/rectángulo sobre la gráfica #1 y
sobre cada tipo de gráfica #3, confirmar propagación bidireccional visual, y ejercitar
deshacer/rehacer/restablecer.

## 4. Supuestos asumidos

- **La gráfica #2 (señal individual) no salta índices filtrados** al navegar con
  anterior/siguiente — sigue visitando todo índice crudo. El PROMPT §5.2 no menciona el
  filtro, y saltar índices introduce casos borde (vecindario íntegramente filtrado) no
  pedidos por el spec. Las señales filtradas simplemente dejan de dibujarse en las
  gráficas #1/#3; la #2 es una vista aparte.
- **`reset_filters` es un reseteo duro y no deshacible**, distinto de `undo_filter` — ver
  §1. Alcanza solo al sensor de la ventana activa.
- El emparejamiento de punto de grupo seleccionado usa coincidencia **más cercana**, no
  exacta, sobre `center_timestamp` recalculado — ver §1, documentado también en el
  docstring de `resolve_group_selection_indices`.

## 5. Riesgos de rendimiento

- Sin medir contra el objetivo formal de <200ms del PROMPT §9.2 en este entorno (no hay
  navegador disponible para cronometrar el viaje de ida y vuelta real). El diseño evita
  trabajo redundante por construcción: puntual nunca recalcula (solo indexa un array ya
  en memoria), y grupo recalcula el equivalente a un cache-miss normal (ya medido <20ms
  por métrica en caché caliente en Fase 3) más un `resolve_groups` adicional, barato y
  puro. Pendiente confirmar con cronometraje real una vez se pueda ejercitar la UI en
  navegador.

## Próximo paso

Verificación manual en navegador (§3) antes de considerar la fase completamente cerrada.
Más allá de eso, quedan del PROMPT: instrumentación de *profiling*/logging estructurado
por etapa (§9.3, "no se optimiza lo que no se mide") y el reporte formal de los
indicadores medibles de §9.2 separados por UHF/AE.
