# Diagnóstico de rendimiento: resultado

Cierra el trabajo planteado en
[`CONTINUAR_DIAGNOSTICO_RENDIMIENTO.md`](CONTINUAR_DIAGNOSTICO_RENDIMIENTO.md). Todas las
cifras de aquí se midieron **en la máquina del usuario, con sus archivos reales** —
que era justamente lo que la sesión anterior, en la nube, no podía hacer.

- Fecha: 2026-08-21
- Máquina: Windows 11, 12 CPUs, 16,9 GB de RAM (**5,1 GB libres, 69 % de carga** durante
  la medición — dato relevante, ver §4)
- Archivos: `med_5_ago_3.hdf5` (12 484 UHF + 20 574 AE), `ruido/test-6.h5` (2931 UHF_KS),
  `test-6_filtrado_20260820_205921.hdf5` (1000 resultantes / 1931 filtradas)

---

## 1. Veredicto

**No hay ninguna regresión de rendimiento en el código nuevo.** La aplicación se siente
lenta por dos costos que ya existían y que este dataset hace grandes, más un defecto real
que sí se encontró y se corrigió (§3).

La hipótesis principal del documento anterior — «el caché está frío» — **es falsa**, y su
mecanismo multiplicador propuesto — «el lock global del caché deja a la interfaz
esperando» — **también**. Ambos se descartaron con medición, no con lectura de código.

---

## 2. Qué se descartó, con evidencia

### 2.1 El caché NO estaba frío (era el Caso A previsto; no aplica)

`scripts/diagnostico_cache.py` sobre el caché real: **141 entradas**, y todos los archivos
que el usuario abrió tienen las suyas.

| Entradas | Última escritura | Archivo |
|---:|---|---|
| 9 | 2026-08-21 01:00 | `test-6_filtrado_20260820_205921.hdf5` (`particion=resultantes`) |
| 15 | 2026-08-20 21:00 | `ruido/test-6.h5` |
| 17 | 2026-08-20 18:55 | `ruido/test-3.h5` |
| 32 | 2026-08-18 15:10 | `med_5_ago_3.hdf5` (UHF + AE) |
| 19 | 2026-08-17 18:41 | `med_5_ago_2.hdf5` |

Nueve entradas por archivo y sensor son exactamente las 9 `default_warmup_specs`: el
precalentamiento **había terminado** en todos ellos. Confirmado además en vivo: al
precargar el archivo exportado en el servidor, las 9 métricas salieron `cache=hit`.

### 2.2 El lock global del caché NO deja esperando a la interfaz

Medición directa: latencia de lectura de una métrica desde caché, con y sin el hilo de
precalentamiento escribiendo al mismo tiempo.

| | Mediana | p90 | Máx |
|---|---:|---:|---:|
| Interfaz en reposo | 9,2 ms | 13,5 ms | 73,6 ms |
| Con el warmup activo | 8,1 ms | 12,3 ms | 70,7 ms |

Indistinguible. El `threading.Lock` único de `cache/backend.py` se sostiene muy poco
tiempo: los payloads son de unos pocos miles de valores, no matrices. **Esa vía está
cerrada; no hay que rediseñar el backend de caché.**

### 2.3 El código nuevo no está en el camino caliente

`git diff c8d6421..HEAD` toca exportación, detección de formato (una comprobación de
atributo) y layout. Nada de ingesta, métricas ni render. Coincide con el A/B que ya había
hecho la sesión anterior.

---

## 3. El defecto que sí había: el precalentamiento obsoleto nunca moría

`ui/state.py` lanzaba el hilo de `start_background_warmup(...)` y **descartaba el hilo
devuelto**. No había rastreo ni cancelación. Consecuencia, al cargar un archivo nuevo
antes de que terminara el anterior:

1. El hilo viejo seguía calculando métricas de un dataset que ya nadie mira.
2. Peor: su marco mantenía viva **la matriz completa del dataset anterior**.

Verificado con `weakref` sobre la matriz real, no por lectura de código:

```
Tras cargar OTRO archivo y soltar toda referencia propia al primero:
  hilos de warmup vivos : 2
  matriz de test-6 (234 MB) liberada: False
  tras esperar a que terminen los warmups, liberada: True
```

Con `med_5_ago_3.hdf5` lo retenido es **~1 GB**, hasta **63 s** (§4). En una máquina con
5,1 GB libres, encadenar dos o tres archivos deja gigabytes muertos ocupados y varios
hilos disputando CPU: exactamente «la aplicación se puso lenta» sin que nada se rompa.

**Corregido**: `AppState.load_dataset` señala el warmup anterior con un `threading.Event`
**antes** de empezar a ingerir — que es cuando más molesta la competencia. `warm_cache`
corta entre especificaciones y sale. Tras el arreglo, la matriz se libera en 1,6 s en vez
de retenerse el warmup entero, y solo queda un hilo vivo.

Lo ya calculado se conserva en el caché y sigue siendo válido: la clave incluye el
`dataset_id`, así que nada se confunde con el dataset nuevo.

**Dos matices sobre el alcance del corte**, añadidos al revisar el arreglo:

- **La cota real no es 1,6 s.** El corte se comprueba al inicio de cada spec, así que un
  warmup cancelado termina la métrica que tenga en curso antes de salir. Los 1,6 s son el
  caso favorable; el peor caso es la duración de la métrica más lenta (`kurtosis` sobre
  AE: ~26 s). No hay forma de mejorarlo sin interrumpir una llamada vectorizada de numpy,
  que no es interrumpible.
- **Publicar un Event también cancela el que hubiera.** Entre el
  `_cancel_pending_warmup()` del inicio y la publicación del Event propio media la
  ingesta entera (11-41 s), tiempo de sobra para que otra carga concurrente — una segunda
  pestaña, el selector de partición — publique el suyo. Sin señalar al publicar, ese
  warmup quedaba huérfano: nadie volvería a cancelarlo y correría hasta el final
  reteniendo su matriz, el defecto original en una ventana más estrecha. Cubierto por
  `test_publishing_a_warmup_cancels_one_left_by_a_concurrent_load`, comprobada en rojo.

Verificado también en la aplicación real (el diálogo de archivo es nativo, así que se usó
el selector de partición, que recorre el mismo `load_dataset`):

```
etapa=ingesta.experimento n_senales=1931          <- carga "filtradas"
etapa=cache.warmup evento=cancelado ... calculadas=4 de=9
etapa=ingesta.experimento n_senales=2931          <- carga "ambas", cancela la anterior
```

---

## 4. De dónde sale realmente el tiempo

Lo que domina no es ningún bug, es el costo fijo del diseño sobre estos datos.

### `load_dataset` — el único punto donde la interfaz se congela de verdad

Es síncrono dentro del callback de Dash. Con `med_5_ago_3.hdf5`, cinco corridas de la
misma llamada, mismo archivo, misma máquina, en el mismo rato:

```
10,8 s   12,2 s   25,8 s   31,3 s   31,5 s   41,1 s
```

Referencia archivada (`docs/benchmarks_med_5_ago_3.json`, 2026-08-14): **6,7 s**.

Esa dispersión de 4x sobre la misma operación **no la puede causar el código**: es el
estado de la máquina. Todo lo demás medido hoy también sale ~2-2,5x por encima de la
referencia de agosto (render de gráfica #1: 160 ms hoy contra 63 ms archivados), de forma
pareja. Es lo que ya advierte `docs/RENDIMIENTO.md` §1.1, pero más marcado, y encaja con
los 5,1 GB libres: la ingesta ensambla la matriz global en RAM y su pico con
`med_5_ago_3.hdf5` ronda los 2 GB (823 MB de AE, más la copia que hace `data[order]` al
ordenar por timestamp).

### El precalentamiento en segundo plano

Un solo hilo, en serie, 9 métricas por sensor:

| | `med_5_ago_3.hdf5` | `ruido/test-6.h5` |
|---|---:|---:|
| Total del warmup | **62,6 s** | **18,3 s** |
| La peor métrica sola | `kurtosis` AE: 25,7 s | `kurtosis`: 7,2 s |

Es decir: tras cada carga de `med_5_ago_3.hdf5` la aplicación pasa **un minuto largo**
quemando un núcleo de fondo. Con 12 CPUs eso no debería notarse por sí solo — y medido,
no bloquea las lecturas de caché (§2.2) — pero sí compite por memoria y por ancho de
banda justo cuando el usuario está interactuando.

### Lo que NO es lento

Con el caché caliente, todo lo que ocurre por interacción está en milisegundos:

| Operación | UHF_KS (test-6) | UHF (med_5_ago_3) | AE (med_5_ago_3) |
|---|---:|---:|---:|
| Render gráfica #1 + `to_json` | 181 ms / 0,23 MB | 160 ms / 1,88 MB | 163 ms / 2,48 MB |
| Cambio de señal (gráfica #2) | 64 ms / 0,13 MB | 17 ms / 0,06 MB | 27 ms / 0,13 MB |
| Lectura de métrica desde caché | 3,3 ms | — | — |

La navegación entre señales no toca disco: `LoadedDataset.blocks` ya está en RAM.

---

## 5. Qué se puede hacer, si el usuario quiere

Ninguna de estas está implementada: son decisiones suyas, no correcciones.

1. **Recortar `default_warmup_specs`** (hoy 9 métricas por sensor). Quitar solo
   `kurtosis` se lleva el 41 % del warmup de `med_5_ago_3.hdf5` (25,7 s de 62,6 s).
   Coste: esa métrica se paga la primera vez que se pida.
2. **Indicador de progreso durante la carga.** No la acelera, pero 30 s de espera muda se
   sienten como un cuelgue. Es el arreglo de percepción con mejor relación esfuerzo/efecto.
3. **Paralelizar el warmup entre métricas.** Hay 12 núcleos y se usa 1. Ojo: la nota de
   `metrics/engine.py` mide que paralelizar *dentro* de una métrica sale peor en Windows
   (coste de `spawn` de `loky`); repartir *métricas distintas* entre hilos es otra cosa y
   habría que medirlo aparte.
4. **Liberar RAM en la máquina antes de trabajar.** Es lo más barato de todo y, a la vista
   de la dispersión de §4, probablemente lo de mayor efecto sobre los 30 s de carga.

No vale la pena tocar el nivel de gzip del payload de caché (era la tercera opción del
plan anterior): §2.2 muestra que ese lock no es un cuello de botella.

---

## 6. Cambios de esta sesión

| Archivo | Qué |
|---|---|
| `cache/warmup.py` | `warm_cache`/`start_background_warmup` aceptan `cancelled: threading.Event` y cortan entre especificaciones |
| `ui/state.py` | `AppState` rastrea el warmup en curso y lo cancela al empezar a cargar otro dataset |
| `tests/test_cache_warmup.py` | 3 pruebas de la cancelación (antes de empezar, a mitad, y que sin el evento nada cambia) |
| `tests/test_state.py` | 2 pruebas de regresión: se cancela el warmup anterior, y su matriz se libera |
| `tests/test_ui_callback_helpers.py` | 2 pruebas que fijaban rutas POSIX (`/datos/...`) y fallaban en Windows |

Las dos pruebas nuevas de `test_state.py` se comprobaron **en rojo** desactivando el
arreglo, para que no sean pruebas vacías.

**422 pruebas pasan.** `mypy core data metrics cache ui viz utils`: 28 errores, todos
`import-untyped` por falta de *stubs* de `h5py`/`plotly`/`scipy`/`joblib` — el ruido
preexistente ya documentado, ninguno en los archivos tocados.

> Nota sobre el conteo: el documento anterior decía «414 pruebas pasan», medido en el
> contenedor Linux. En Windows eran 414 con 2 fallos de esas rutas POSIX. 414 + 2
> corregidas + 5 nuevas = 421.
