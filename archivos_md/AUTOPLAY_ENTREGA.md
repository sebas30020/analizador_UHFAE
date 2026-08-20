# Auto-play de la gráfica #2 (entrega)

Rama `auto-play`. Botón que recorre las señales activas de la ventana como si se
mantuviera pulsado "Siguiente", con velocidad ajustable, bucle al llegar al final y
respetando el filtrado interactivo del usuario. Aplica por igual a los tres sensores
(UHF, AE, UHF_KS).

## 1. Decisiones (confirmadas con el usuario)

1. **Bucle**: al pasar la última señal activa vuelve a la primera, sin detenerse.
2. **Respeta el filtrado**, y esto se extendió también al botón "Siguiente"/"Anterior"
   manual (antes avanzaba ±1 sobre el índice bruto, ignorando la máscara): ambos saltan
   ahora las señales excluidas. Es la misma pieza de lógica (`step_to_adjacent_active`,
   `ui/callbacks/helpers.py`) con `wrap=False` para los botones y `wrap=True` para el
   auto-play — la única diferencia real entre "Siguiente sostenido" y "auto-play" es qué
   pasa en el extremo.
3. **Control de velocidad** junto al botón: slider con marcas fijas (0,5 / 1 / 2 / 4 / 6
   señales/s), no un campo libre — ver §3 para por qué.

## 2. Qué cambió

- **`ui/callbacks/helpers.py`**: `step_to_adjacent_active` (paso al activo adyacente,
  con o sin bucle) y `autoplay_interval_ms` (velocidad → periodo del `dcc.Interval`,
  saturado por abajo). `resolve_nav_index` gana un parámetro `active_indices` opcional
  y una rama para `AUTOPLAY_TRIGGER_ID` — sin `active_indices` (los demás disparadores:
  clic en gráfica, índice tecleado) el comportamiento es exactamente el de antes.
- **`ui/components/sensor_window.py`**: botón "▶ Auto-play"/"⏸ Pausar", `dcc.Slider` de
  velocidad, etiqueta de señales/s y el `dcc.Interval` (nace `disabled=True` — no hay
  ticks hasta pulsar).
- **`ui/callbacks/sensor_window_callbacks.py`**: `_on_navigate` gana
  `Input("autoplay-interval", "n_intervals")` y calcula `active_indices` desde
  `state.get_active_mask(sensor)` cuando el disparador es un botón de paso o un tick de
  auto-play; `_on_toggle_autoplay` conmuta `disabled` del Interval (fuente única de
  verdad del estado reproduciendo/pausado, no hay Store aparte); `_on_change_autoplay_speed`
  traduce el slider al `interval` en vivo, sin reiniciar el recorrido.

Cero cambios en `metrics/`, `cache/`, `core/normalization.py` ni en los readers — el
alcance es enteramente de presentación y navegación.

### Caso borde: una sola señal activa (o ninguna)

Con una sola señal activa, cada tick de auto-play recalcularía el mismo índice: se
detectó (`new_index == current` en un tick) y se convierte en `PreventUpdate`, para no
disparar un repintado completo de la gráfica #2 sin mover nada. Con cero señales activas
(todo filtrado), no hay información de qué es "activo" — degrada al paso simple ±1
acotado, igual que si nunca se hubiera filtrado nada.

## 3. Por qué la velocidad es una lista cerrada, no un campo libre

El techo de velocidad no lo pone el cálculo del servidor: construir la gráfica #2 mide
**3,0 ms (UHF)**, **4,5 ms (AE)**, **5,2 ms (UHF_KS)** sobre datos reales (mediana de 30
pasos consecutivos, `normalize` + `build_signal_figure` + serialización del payload).
Incluso al ritmo más agresivo del selector (6 Hz → 167 ms/tick) sobra un orden de
magnitud de margen servidor.

Lo que sí puede saturarse es el repintado de Plotly en el navegador más el viaje de ida
y vuelta del callback. Si los ticks llegan más rápido de lo que el navegador alcanza a
dibujar, las peticiones se encolan: la reproducción se ve a tirones y hasta el botón de
pausa tarda en responder, porque queda detrás de la cola. `AUTOPLAY_MIN_INTERVAL_MS = 150`
es el guardarraíl — `autoplay_interval_ms` satura cualquier velocidad por debajo de ese
piso, y las opciones del slider (hasta 6 Hz → 167 ms) se quedan con margen sobre él a
propósito, no al límite exacto.

## 4. Verificación

- **Lógica pura** (`tests/test_ui_callback_helpers.py`): salto de activo en activo hacia
  adelante/atrás, parada en el extremo para los botones, bucle para auto-play, arranque
  desde una señal ya excluida, degradación sin máscara (no-regresión del comportamiento
  anterior) y con todo filtrado, caso de una sola señal activa, y saturación/valor por
  defecto de `autoplay_interval_ms`.
- **Callbacks** (`tests/test_sensor_window_callbacks.py`): conmutación reproducir/pausar
  del botón, cambio de velocidad → `interval`/etiqueta, y `_on_navigate` disparado por
  `autoplay-interval` y por `btn-next` contra un `AppState` con filtro aplicado —
  incluido el `PreventUpdate` con una sola señal activa.
- **335/335 pruebas** de la suite completa pasan; `mypy core data metrics cache ui viz
  utils` sin errores de tipos reales (mismo ruido preexistente de `import-untyped`).
- **Verificación en navegador real** (`test-1.h5`, 144 señales `UHF_KS`, precargado sin
  pasar por el diálogo nativo de `tkinter` que el navegador automatizado no puede
  pilotar): clic en Auto-play avanza el índice en vivo (0→13→27 en ~6 s a 2 señales/s);
  Pausar detiene el avance de inmediato y lo deja quieto; cambiar la velocidad al máximo
  del slider (6 señales/s) y saltar al índice 140 con el bucle activo cruza el final
  (143) y vuelve a 0, continuando — el bucle funciona de punta a punta con datos reales,
  no solo en la lógica pura.
