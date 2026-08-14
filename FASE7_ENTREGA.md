
# Fase 7 — Optimización y documentación (entrega)

Formato de respuesta según PROMPT maestro §13. Alcance (§12): *profiling*, benchmarks
contra §9.2, documentación de usuario y de arquitectura, guía de "cómo agregar una
métrica".

## 1. Decisiones de diseño

- **La instrumentación se apaga en la rama corta, no con un logger silencioso.** Con
  `profiling.enabled: false` (el default), `stage()` cede el control y retorna sin llamar
  a `perf_counter` ni construir el registro. La alternativa habitual — instrumentar
  siempre y filtrar por nivel de log — pagaría dos llamadas al reloj y la construcción de
  un objeto en cada etapa, incluidas las que están dentro de bucles por grupo. Con eso, la
  instrumentación se puede dejar en el camino caliente sin discutir su costo.
- **Precedencia de configuración `force` > variable de entorno > archivo.** Encender el
  profiling para una corrida suelta (`ANALIZADOR_PROFILING=1`) no debe obligar a editar
  —y luego recordar revertir— un archivo versionado del proyecto. `force` existe para que
  las pruebas y los benchmarks controlen el estado sin depender del entorno de quien las
  ejecuta.
- **Doble destino: log estructurado y colector en memoria.** El log sirve para mirar una
  sesión real de la aplicación; el colector es lo que consumen los benchmarks y las
  pruebas, que necesitan afirmar sobre tiempos sin parsear texto. Son la misma medición
  entregada en dos formas, no dos mecanismos.
- **Las etapas se instrumentan donde está la unidad de trabajo, no donde está el usuario.**
  `render.grafica1` envuelve `build_timeseries_figure`, no el callback de Dash: así el
  mismo número lo produce la aplicación real y el benchmark, que llama directo al
  constructor de la figura. Instrumentar el callback habría dado un número que el
  benchmark no puede reproducir sin levantar un servidor.
- **El arnés de benchmarks se partió en dos.** `benchmarks/harness.py` (medición repetida,
  veredicto, formato) es puro y está cubierto por la suite; `benchmarks/run_benchmarks.py`
  necesita un `.hdf5` real y tarda minutos, así que vive fuera de `pytest`. La lógica que
  decide si un objetivo se cumple no puede ser la parte no probada del sistema.
- **El benchmark mide contra un caché temporal propio, que borra al terminar.** "Caché
  frío" tiene que ser frío de verdad; y una corrida de medición no debe dejar el
  `cache_data/` del usuario con entradas que él no pidió. Por lo mismo apaga el
  precalentamiento en segundo plano (`warmup_on_load=False`): un hilo calculando métricas
  mientras se cronometra vuelve el resultado irreproducible.
- **Se mide hasta `to_json`, no hasta la figura.** Una figura construida y no serializada
  todavía no está en pantalla. Incluir la serialización es lo que hace comparable el
  número con el objetivo de §9.2, que habla de lo que percibe el usuario.
- **Mediana de 5 muestras descartando una de calentamiento.** La primera llamada paga
  imports perezosos de Plotly, el primer `discover_metrics()` y las primeras páginas de
  memoria: costos reales, pero que el usuario ve una vez por sesión, no en cada
  interacción. Las operaciones no repetibles (ingesta, cálculo en frío) se miden una vez y
  se reportan como tal (`min = mediana = max`), sin fingir una distribución.
- **Normalización perezosa de la matriz de grupo** (la optimización que destapó la
  medición, ver §5): `MetricContext` dejó de ser un `dataclass` para que `signal_matrix`
  sea una propiedad memoizada sobre una factoría opcional. Se eligió esto en vez de un
  flag `requires_signal_matrix` en el decorador porque un flag hay que mantenerlo
  correcto en cada métrica nueva —y equivocarse es silencioso—, mientras que la
  materialización perezosa acierta sola: quien no lee el atributo, no paga. Los plugins no
  cambiaron ni una línea.

## 2. Código

```
utils/profiling.py                      (nuevo: stage/profiled, colector, config, resumen)
benchmarks/harness.py                   (nuevo: medición repetida, veredicto, reporte)
benchmarks/run_benchmarks.py            (nuevo: los 7 indicadores de §9.2, UHF y AE)
config/sensors.yaml                     (+ sección profiling: enabled/level)
metrics/registry.py                     (MetricContext: signal_matrix perezosa y memoizada)
metrics/engine.py                       (compute_group_intrinsic: matriz vía factoría)
metrics/spectral.py                     (+ etapa metricas.espectro)
data/ingest.py                          (+ etapas ingesta.experimento / ingesta.sensor)
cache/service.py                        (+ etapas cache.* con hit/miss/bypass)
cache/warmup.py                         (+ manejo de error del hilo daemon)
ui/app.py                               (+ resolución de profiling al construir la app)
ui/state.py                             (+ precalentamiento al cargar dataset; warmup_on_load)
ui/components/graph_timeseries.py       (+ etapa render.grafica1)
ui/components/graph_signal.py           (+ etapa render.grafica2)
ui/components/graph_metric.py           (+ etapa render.grafica3)
README.md                               (reescrito: guía de usuario + puesta en marcha)
docs/ARQUITECTURA.md                    (nuevo: capas, decisiones, 3 pruebas de fuego §10.3)
docs/COMO_AGREGAR_UNA_METRICA.md        (nuevo: guía paso a paso)
docs/RENDIMIENTO.md                     (nuevo: indicadores medidos + uso del profiling)
docs/benchmarks_med_5_ago_3.json        (nuevo: corrida de referencia, comparable entre versiones)
```

## 3. Pruebas

**211/211 pruebas en verde** (`pytest tests/ -q`), 24 nuevas de esta fase:

- `tests/test_profiling.py` (nuevo, 14 pruebas): desactivado no registra nada; etapa con
  campos; campos añadidos dentro del bloque; etapa que falla se registra con `error=` y
  relanza; decorador `profiled`; `summarize`; línea de log parseable como `clave=valor`;
  y las cinco de precedencia de configuración (archivo, sin sección, archivo inexistente,
  variable de entorno, `force`).
- `tests/test_benchmark_harness.py` (nuevo, 8 pruebas): el calentamiento se descarta;
  mediana/min/max coherentes; `repeats=0` es error; veredicto CUMPLE / NO CUMPLE; una
  operación sin objetivo no tiene veredicto (`—`, no un falso aprobado); tabla markdown
  con una fila por sensor; reporte JSON serializable que conserva el veredicto.
- `tests/test_metrics_group.py` (+3): una métrica que solo usa timestamps **no**
  materializa la matriz (`llamadas == 0`); una que sí la usa la materializa **una sola
  vez**; una matriz explícita sigue teniendo prioridad sobre la factoría.
- `tests/test_state.py`: el fixture `app_state` pasa `warmup_on_load=False` — con el
  precalentamiento conectado, un hilo escribiendo en el caché de `tmp_path` mientras
  pytest lo borra convertiría una limpieza fallida en un fallo intermitente.

`mypy` sin errores nuevos (solo el ruido preexistente de bibliotecas sin *stubs*).

**Benchmarks contra datos reales**: corrida completa sobre `med_5_ago_3.hdf5` (33 058
señales), reportada en `docs/RENDIMIENTO.md` §1 y archivada en
`docs/benchmarks_med_5_ago_3.json`. **Los 7 objetivos con umbral se cumplen en ambos
sensores** (en mediana; ver el matiz de `Filtro + propagación` en §5).

## 4. Supuestos asumidos y preguntas abiertas

- **"Una métrica puntual" (§9.2) se instancia en dos**: `rms` (dominio tiempo, sin FFT) y
  `feq` (dominio frecuencia, paga la FFT centralizada). El PROMPT no nombra cuál medir, y
  un solo número escondería que sobre AE la espectral cuesta 2,2 veces más que la
  temporal.
- **"Propagación a todas las ventanas" se instancia como la ventana completa de un
  sensor**: gráfica #1 + una gráfica #3 puntual (filtro posterior sobre caché) + una
  gráfica #3 de grupo (bypass de caché, recálculo real). Es el peor caso realista de una
  ventana en uso. Una ventana con diez métricas apiladas costaría proporcionalmente más;
  el número reportado no es un techo.
- **La exclusión medida es del 5 % de las señales**, en un tramo contiguo. Un filtro que
  excluya el 90 % es más barato (menos señales que dibujar y agregar), así que el número
  reportado es el lado conservador.
- **Pregunta abierta**: el objetivo de §9.2 se interpretó como tiempo de servidor hasta
  la respuesta serializada. Si lo que quieres acotar es el tiempo hasta que el usuario ve
  la gráfica repintada, falta el tramo de navegador y hay que medirlo allí (ver §5).

## 5. Riesgos de rendimiento

- **El tramo de navegador sigue sin medir.** Todo este reporte es del lado del servidor.
  La gráfica #1 manda 1,9 MB (UHF) / 2,5 MB (AE) de JSON con ~37 000 / ~62 000 puntos por
  refresco; cuánto tarda WebGL en pintarlos no está cronometrado, y la extensión de
  navegador no conectó en este entorno. **Es la verificación pendiente más importante**,
  y arrastra también el *smoke test* de la Fase 6 (lazo/rectángulo real sobre las
  gráficas).
- **`Filtro + propagación` cumple en mediana pero no con margen**: 163 ms (UHF) y 167 ms
  (AE) contra un umbral de 200 ms, con muestras individuales que llegaron a 202 y 220 ms.
  Es el primer indicador que se va a romper si el dataset crece o se apilan más gráficas
  tipo #3. El siguiente paso natural ahí sería cachear las métricas de grupo por máscara,
  que hoy hacen bypass total por diseño (Fase 6).
- **Las mediciones tienen variabilidad alta entre corridas** (hasta 4× en operaciones
  dominadas por CPU/disco, ver `docs/RENDIMIENTO.md` §1.1): la máquina de medición es un
  equipo de trabajo con otros procesos activos. Los números publicados son los de la
  corrida más lenta de dos, y sirven para detectar regresiones de orden de magnitud, no
  diferencias del 20 %.
- **UI no bloqueante: brecha reconocida del §9.1.** El precalentamiento corre en segundo
  plano, pero un cálculo en frío pedido desde la interfaz se ejecuta dentro del callback:
  `feq` sobre AE son ~3,1 s de interfaz congelada, **sin indicador de progreso ni
  cancelación**. Es el requisito del §9.1 que sigue sin cumplirse.
- **Ingesta en RAM.** `data/ingest.py` arma la matriz completa antes de persistir (~800 MB
  para AE en el dataset real). Riesgo documentado desde la Fase 1; con datasets varias
  veces mayores hay que pasar a escritura incremental por bloques.
- **El precalentamiento ahora sí se ejecuta al cargar un dataset**, lo que consume CPU
  durante el primer minuto de una sesión (9 métricas × 2 sensores). Es trabajo que el
  usuario iba a pedir de todos modos y corre en un hilo daemon, pero explica por qué la
  máquina está ocupada justo después de abrir un archivo.

## Próximo paso

Cronometraje y verificación en navegador real (§5), lo único que queda para cerrar tanto
esta fase como el pendiente de la Fase 6. Después de eso, el candidato natural a la
siguiente mejora es el indicador de progreso y cancelación para cálculos en frío.
