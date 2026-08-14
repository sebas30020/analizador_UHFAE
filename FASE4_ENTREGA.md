
# Fase 4 — Interfaz base (entrega)

Formato de respuesta según PROMPT maestro §13. Alcance (§12): una ventana de sensor
completa (panel de control, gráficas tipo #1/#2/#3, navegación de señal, panel de
metadatos).

## 1. Decisiones de diseño

- **Una sola ventana montada** (sensor `UHF` por defecto, parametrizable): las ventanas
  gemelas UHF/AE y las ventanas adicionales de métricas son explícitamente Fase 5 en el
  PROMPT §12. Los componentes ya están escritos parametrizados por `sensor` (§6.1
  "mismo componente reutilizable") para que la Fase 5 solo tenga que montar una segunda
  instancia, no reescribir nada.
- **Lista de "métricas de grupo" corregida antes de wire-up**: el panel de control
  inicial solo iba a ofrecer las 3 métricas de grupo intrínsecas
  (tasa_pulsos/energía/ráfagas), dejando fuera la reducción genérica (mediana/media/
  percentil) de las 17 métricas puntuales que sí implementa `metrics/engine.py` desde
  Fase 2. Se corrigió antes de escribir los callbacks: ahora hay 2 listas de grupo
  (intrínsecas y "reducción de métrica puntual"), ambas alimentando el mismo Store de
  selección con un campo `regimen` que distingue `puntual` / `grupo_intrinseca` /
  `grupo_reduccion`.
- **Selección de BD vía `tkinter.filedialog`**, ejecutado dentro del propio callback de
  Dash: válido porque el servidor corre en la misma máquina que el usuario (herramienta
  de un solo usuario local, FASE0_DISENO...md §3.4), no un despliegue remoto.
- **Lógica de callbacks separada de su envoltorio Dash** (`ui/callbacks/helpers.py`):
  las decisiones de navegación (botones/click-en-gráfica/campo numérico) y de exclusión
  mutua entre los 4 `RadioItems` de selección de métrica son funciones puras, probables
  sin arrancar Dash ni un navegador. Un sentinela `NO_CHANGE` propio (no
  `dash.no_update`) mantiene esa lógica libre de dependencia de Dash.
- **Diezmado min/max por bin de píxel** (`viz/decimation.py`), agregación exacta
  (`np.minimum.at`/`np.maximum.at`, nunca submuestreo): reutilizado por la gráfica
  tipo #1 (envolvente de todas las señales) y tipo #2 (traza AE completa, resolución
  completa al hacer zoom).
- **Gráfica tipo #3 es scatter puro** (`mode="markers"`, nunca `"lines"`), verificado
  también en producción real vía HTTP (§4).

## 2. Código

```
viz/decimation.py
ui/state.py
ui/components/{graph_timeseries,graph_signal,graph_metric,control_panel,metadata_panel,sensor_window}.py
ui/callbacks/{helpers,sensor_window_callbacks}.py
ui/app.py
scripts/run_dev_server.py   (auxiliar de desarrollo)
```

## 3. Pruebas

**143/143 pruebas en verde** (`pytest tests/ -q`), 25 nuevas de esta fase.
`mypy core/ data/ metrics/ cache/ viz/ ui/` sin errores (57 archivos).

- `test_decimation.py`: agregación exacta (el outlier/pico aislado sobrevive al
  diezmado, no se pierde por submuestreo), bins vacíos omitidos, paso a través sin
  diezmar cuando ya cabe en los píxeles, construcción de segmentos verticales.
- `test_ui_callback_helpers.py`: parseo de índices de comparación, resolución de
  navegación (prev/next parten del índice del **estado**, no del campo a medio
  escribir), exclusión mutua entre los 4 radios de selección de métrica — incluida la
  prueba de regresión del bug de cascada (§4).

**Verificación en servidor real** (no solo pruebas unitarias, siguiendo la instrucción
de probar cambios de UI antes de reportarlos como terminados): arranqué el servidor Dash
real y conduje el flujo completo — seleccionar BD → refrescar gráfica #1 → navegar a la
señal #100 → seleccionar métrica puntual → refrescar gráfica #3 → seleccionar métrica de
grupo por reducción → refrescar gráfica #3 — mediante peticiones HTTP reales a
`/_dash-update-component` (el mismo endpoint que usa el cliente JS de Dash), sin
navegador disponible en este entorno (ver §5). Todo el flujo respondió correctamente de
punta a punta.

## 4. Dos bugs reales encontrados (uno en diseño, uno solo visible con el servidor real)

1. **Cascada de limpieza borraba la selección recién hecha** (encontrado razonando el
   flujo antes de escribir el callback, sin necesidad de ejecutar nada): al limpiar los
   radios hermanos escribiendo `None` en sus outputs, ese cambio retriggerea el mismo
   callback (esos radios también son `Input`), y una implementación ingenua terminaba
   sobrescribiendo el Store de selección con `None` en esa segunda pasada. Corregido con
   el sentinela `NO_CHANGE` (`ui/callbacks/helpers.py`, ver docstring de
   `resolve_selected_metric_radios`), probado explícitamente en
   `test_resolve_selected_metric_clearing_cascade_does_not_wipe_selection`.
2. **`SqliteHdf5CacheBackend` no era seguro para hilos** (`sqlite3.ProgrammingError:
   SQLite objects created in a thread can only be used in that same thread`) — **solo
   apareció al probar contra el servidor Dash real**, nunca en las pruebas unitarias
   (que siempre usan la instancia desde el mismo hilo). El servidor de desarrollo de
   Flask atiende cada petición HTTP en un hilo del pool, no necesariamente el que creó
   `AppState`/`SqliteHdf5CacheBackend`. Corregido en `cache/backend.py`:
   `check_same_thread=False` en la conexión SQLite + un `threading.Lock` propio
   envolviendo cada operación (también protege el acceso al HDF5 de payload, cuya
   librería C tampoco es segura para hilos concurrentes sin coordinación). Este bug
   es la prueba concreta de por qué la instrucción de "arrancar el servidor y probar de
   verdad" importa: nada en la suite de pruebas de la Fase 3 lo habría detectado.

## 5. Limitación de verificación reconocida

No hay navegador disponible en este entorno (Claude in Chrome no se instaló en esta
sesión). La verificación se hizo: (a) HTTP directo contra los endpoints Dash reales
(`/`, `/_dash-layout`, `/_dash-dependencies`, `/_dash-update-component`) simulando
exactamente las peticiones que haría el cliente JS, con aserciones sobre las figuras
Plotly devueltas (número de trazas, `mode="markers"` en la gráfica #3, contenido del
panel de metadatos); (b) las funciones puras de construcción de figuras probadas
directamente contra datos reales. **No se verificó el renderizado visual real en un
navegador** (interacción de lazo, hover, aspecto del CSS). Si el usuario instala la
extensión de Chrome más adelante, vale la pena una pasada visual antes de dar la Fase 4
por completamente cerrada en términos de UX, aunque el comportamiento funcional ya está
confirmado end-to-end.

## 6. Supuestos asumidos

- Los de fases anteriores siguen vigentes.
- El servidor Dash se ejecuta localmente (`127.0.0.1`) para un solo usuario; el diálogo
  de selección de archivo asume acceso a un display local (Windows con sesión gráfica).
- El overlay de comparación de señales (§5.2, "grupo de señales seleccionadas
  superpuestas") se implementó como un campo de texto de índices separados por comas
  (`compare-indices`), no como selección por lazo — la selección multi-señal por lazo es
  del sistema de filtrado cruzado, explícitamente Fase 6 en el PROMPT.

## 7. Riesgos de rendimiento

- Ninguno nuevo medido. El único riesgo activo sigue siendo el de Fase 2 (paralelismo
  contraproducente a la escala actual) — no aplica aquí porque los callbacks de la UI
  usan `n_workers=1` (default) a través de `cache/service.py`.

## Próximo paso

Fase 5 — Ventanas gemelas y adicionales: montar UHF y AE simultáneamente (reutilizando
los mismos componentes), botón para generar ventanas adicionales de solo gráficas
tipo #3 (máx. 4 cada una), estado compartido entre todas.
