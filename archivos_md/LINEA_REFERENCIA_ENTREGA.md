
# Línea horizontal de referencia en las gráficas #3 (entrega)

Implementación de `archivos_md/prompt-linea-referencia.md`. Plan de trabajo y
reconocimiento previo del código en `archivos_md/PLAN_LINEA_REFERENCIA.md` — este
documento resume las decisiones tomadas y el resultado, sin repetir lo que ya está ahí.

## 1. Decisiones de diseño ante ambigüedades del enunciado

Dos ambigüedades reales se consultaron con el usuario antes de implementar (§0 del
enunciado: "si detectas una ambigüedad real que cambie el diseño de la solución,
pregunta antes de implementar"). Ambas quedaron confirmadas:

- **Fuente de datos en régimen de grupo.** El enunciado pide un promedio
  "independiente de la configuración de agrupación o suavizado activa". Para las
  métricas intrínsecas de grupo (`tasa_pulsos`, `tasa_energia`, `tasa_rafagas`) eso es
  imposible tomado al pie de la letra: esas métricas solo existen *por ventana*, no
  hay una serie sin agrupar de la que sacar la media. Decisión confirmada: el
  promedio se calcula sobre **la misma serie que la gráfica dibuja**, tomada después
  del caché y **antes** de cualquier suavizado o unión de puntos. Cambiar suavizado o
  método de suavizado nunca mueve la línea (criterio de aceptación 8); cambiar la
  ventana de agrupamiento sí, porque la magnitud graficada es otra — una línea en
  unidades distintas de las del eje Y de su propia gráfica sería incorrecta, no
  conservadora.
- **Interacción con el filtrado interactivo.** El enunciado no lo menciona. Decisión
  confirmada: la línea respeta la máscara de filtrado activa, igual que los puntos.
  El filtrado es una exclusión a nivel de dato, no una opción de presentación; además,
  en régimen de grupo el resultado filtrado ni siquiera pasa por el caché (bypass
  total, `docs/ARQUITECTURA.md` §5), así que la serie que llega a la gráfica ya viene
  filtrada — no hacerlo así habría exigido una segunda ruta de cálculo solo para la
  línea.

Una tercera decisión, de implementación, surgió durante la Fase 5 y no requirió
consulta porque es mecánica, no de producto: `_on_toggle_events` (el callback que
parcheaba `layout.shapes` de eventos con `dash.Patch`) asignaba la lista completa. Si
la línea de referencia se sumaba como otra shape con un callback aparte, conmutar
"Mostrar eventos" la habría borrado y viceversa. Se refactorizó en un único callback,
`_on_refresh_metric_decorations`, dueño de `layout.shapes` **y** `layout.annotations`
de las gráficas #3, que compone eventos + línea de referencia en cada parche.

## 2. Fuente de datos, convención del intervalo y casos borde

- **"Inicio del experimento"**: se reutiliza `ui/state.py::compute_t0` tal cual —
  mínimo timestamp entre todas las señales de todos los sensores, la serie ambiental y
  los eventos. Es el mismo cero que ya usan las gráficas #1 y #3; no se introdujo una
  referencia nueva.
- **Límite superior inclusivo**: se incluyen las muestras con `x <= t`
  (`np.searchsorted(..., side="right")`, `viz/reference_line.py::mean_until`).
- **Nulos y NaN**: excluidos del promedio mediante un conteo de valores finitos
  separado del acumulado — nunca tratados como cero. Mismo criterio que ya aplica
  `viz/smoothing.py::_rolling_mean_time`.
- **`t` mayor que la duración del experimento**: usa todos los datos disponibles, no
  es un error (`mean_until` no distingue ese caso de "cubre toda la serie").
- **`t <= 0` o sin muestras en el intervalo**: no se dibuja línea; se informa con una
  anotación breve y formal (`"Intervalo de referencia no definido: introduzca un valor
  mayor que 0."` / `"Sin datos disponibles en el intervalo definido para la línea de
  referencia."`, `ui/callbacks/helpers.py::resolve_reference_line_display`), sin
  bloquear el resto de la gráfica. Verificado end-to-end en navegador (ver §7).
- **Una sola muestra en el intervalo**: el promedio es ese valor, se dibuja
  (`tests/test_reference_line.py::test_single_sample_in_interval_is_valid`).

## 3. Arquitectura

Tres piezas nuevas, cada una en la capa que le corresponde (`CLAUDE.md`: "la capa de
presentación no contiene lógica de cálculo"):

- **`viz/reference_line.py`** (puro, sin Dash ni Plotly): `build_prefix_sums` /
  `mean_until` — sumas de prefijo + búsqueda binaria, mismo patrón que
  `viz/smoothing.py`.
- **`ui/components/reference_line.py`**: constructor de la shape (`xref="paper"`,
  `layer="below"`, punteada, gris, opacidad 0.5) y de la anotación con el valor,
  siguiendo el mismo reparto de responsabilidades que `ui/components/event_lines.py`.
  Color nuevo (`#7A7A7A`): ningún tono ya definido en el proyecto es "auxiliar" o "de
  referencia" — todos están atados a una serie concreta (puntos, parcial, evento,
  tendencia).
- **`ui/reference_registry.py`**: singleton de proceso (mismo nivel que
  `ui/state.py::AppState`) que retiene las series `(timestamps, values)` de las
  gráficas #3 actualmente renderizadas, para que cambiar `t` no tenga que volver a
  pasar por el caché ni por el motor de métricas. Se sustituye **completo** en cada
  ejecución de `_on_refresh_metrics` — acotado por construcción, sin cota explícita:
  nunca retiene más que la selección de métricas vigente, y se purga también cuando el
  dataset se descarga o la selección se vacía (mismo cuidado que motivó el commit
  `bd5814d` de corrección de agotamiento de memoria).

`build_metric_figure` (`ui/components/graph_metric.py`) gana dos parámetros,
`reference_value` y `reference_message`, usados exclusivamente para pintar la shape/
anotación — no participan en ningún cálculo de la figura.

## 4. Rendimiento

Ver `docs/RENDIMIENTO.md` §7 para el reporte completo con línea base medida antes de
tocar código (`archivos_md/benchmarks_baseline/baseline_linea_referencia.json`) contra
la corrida posterior (`.../despues_linea_referencia.json`). Resumen:

- **Sin regresión** en ninguna operación preexistente (±1-2 ms, dentro del ruido de
  máquina).
- **Costo nulo cuando está apagada** (estado por defecto): el promedio no se calcula
  si `show-reference-line` está desactivado.
- **Cambiar `t` nunca reentra en el caché ni en el motor de métricas**: por
  construcción, `reference-line-t` y `show-reference-line` son `Input` únicamente de
  `_on_refresh_metric_decorations`, nunca de `_on_refresh_metrics` (que solo los lee
  como `State`, para que la primera figura ya nazca coherente).
- **Conmutar visibilidad**: < 50 ms medido con 3 gráficas #3 simultáneas, en la
  práctica sub-milisegundo — independiente de `n_puntos`.
- Sumas de prefijo vs. promedio ingenuo: con 12 484-20 574 puntos la diferencia no es
  medible con `time.perf_counter` (ambas caminos están por debajo de 0.1 ms); la
  ventaja algorítmica (O(log n) contra O(n) por cambio de `t`) queda implementada y
  probada para datasets mayores, documentado explícitamente en vez de reclamar una
  mejora que hoy no se puede medir.

## 5. Auditoría de texto de interfaz (§3 del enunciado)

Los dos controles nuevos (`ui/components/control_panel.py`) siguen las convenciones:
registro formal, verbo en infinitivo + objeto (`Mostrar línea de referencia`,
`Definir intervalo de referencia (min)`), unidad explícita en la etiqueta, sin
referencias al proceso de desarrollo. Los dos mensajes de caso borde (§2, arriba) son
igualmente formales y no bloquean el resto de la gráfica.

No se encontró texto preexistente en el bloque "Opciones de visualización" que
incumpliera las convenciones del §3 — la auditoría no requirió correcciones fuera del
alcance de esta tarea.

## 6. Controles nuevos

En el bloque "Opciones de visualización" del panel de control, después del umbral de
corte de huecos:

1. **Mostrar línea de referencia** (`dcc.Checklist`, id `show-reference-line`) —
   apagado por defecto.
2. **Definir intervalo de referencia (min)** (`dcc.Input`, id `reference-line-t`,
   `debounce=True`, valor por defecto 60 min) — se deshabilita cuando el selector de
   visibilidad está apagado (`_on_refresh_reference_line_control_disabled`, mismo
   patrón que `_on_refresh_smoothing_controls_disabled`).

Valor por defecto (10 min): la duración típica del dataset real de referencia
(`med_5_ago_3.hdf5`) es ~350 min; 10 min es un intervalo inicial representativo sin
necesitar ajuste manual antes de ver algo útil.

## 7. Verificación

- `pytest tests/ -q`: 288 pruebas, incluyendo `tests/test_reference_line.py` (8),
  `tests/test_reference_registry.py` (5), casos nuevos en `tests/test_graph_metric.py`
  (4) y en `tests/test_sensor_window_callbacks.py` (10, cubriendo el flujo completo
  registro → figura inicial → parche por cambio de `t` → conmutación de visibilidad →
  caso borde `t=0`).
- `mypy core data metrics cache ui viz utils`: mismos 18 avisos preexistentes de stubs
  faltantes (`plotly`, `h5py`, `scipy`, `joblib`), ningún error nuevo.
- Verificación manual en navegador (dataset real, `med_5_ago_3.hdf5`, métrica `rms`
  UHF): la línea se dibuja con la geometría esperada (`xref="paper"`, ancho completo,
  `layer="below"`, punteada, gris), el valor de la anotación coincide con el
  promedio esperado, cambiar `t` actualiza el valor sin recargar la página, ocultar
  eventos no afecta la línea de referencia (ni viceversa), y `t=0` muestra el mensaje
  de caso borde sin dibujar línea.
