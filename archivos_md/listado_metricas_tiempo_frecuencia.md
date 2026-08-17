# Listado de Métricas: Dominio del Tiempo vs. Dominio de la Frecuencia

Este documento inventaria las métricas puntuales implementadas en `metricas.py` y expuestas en la
GUI (`analizador_datos.py`, método `get_metrics_config`), clasificándolas según el dominio en el
que se calculan.

**Convención de dominio:**
- **Tiempo**: la métrica se calcula directamente sobre las muestras de amplitud $v(t)$ o sobre
  marcas de tiempo de los pulsos, sin pasar por una transformada espectral.
- **Frecuencia**: la métrica se calcula a partir del espectro de la señal (FFT/PSD).

Cada fila indica la etiqueta mostrada en la GUI, la función que la calcula en `metricas.py`, su
unidad, la ecuación de referencia citada en el docstring (cuando existe) y una descripción breve.

---

## 1. Métricas en el dominio del tiempo

### 1.1 Estadística de amplitud / forma del pulso

| Métrica (GUI) | Función (`metricas.py`) | Unidad | Ecuación | Descripción |
|---|---|---|---|---|
| Valor RMS | `calcular_rms` | V | Ec. 29 | Valor cuadrático medio de la señal. |
| VPP | `calcular_vpp` | V | — | Voltaje pico a pico ($V_{max} - V_{min}$). |
| Vmax | `calcular_vmax` | V | — | Amplitud pico absoluta. |
| Kurtosis | `calcular_kurtosis_stat` | — | Ec. 26 (Pearson) / 27 (exceso) | Curtosis estadística de la distribución de amplitudes (config. `KURTOSIS_TYPE`). |
| Skewness | `calcular_skewness` | — | Ec. 32 | Asimetría estadística de la distribución de amplitudes. |
| Factor de cresta | `calcular_crest_factor` | — | Ec. 30 | Relación entre el pico absoluto y el RMS ($V_{peak}/RMS$). |
| Entropía Shannon | `calcular_shannon_entropy` | bits | Ec. 16–20 | Entropía de Shannon normalizada del histograma de amplitud (normalizada al pico, `SHANNON_BINS` bins en [-1, 1]). |
| ZCR | `calcular_zcr` | — | Ec. 22 | Tasa de cruces por cero, con zona muerta configurable (`ZCR_DEAD_ZONE`) para filtrar ruido. |
| Rise Time | `calcular_rise_time` | ns | — | Tiempo de subida del flanco ascendente (10%–90% del pico). |
| Tiempo Eq. (Teq) | `calcular_teq` | µs | — | Duración equivalente del pulso, momento de segundo orden de $v^2(t)$ respecto al tiempo. |

### 1.2 Energía

| Métrica (GUI) | Función (`metricas.py`) | Unidad | Ecuación | Descripción |
|---|---|---|---|---|
| Energía Relativa | `calcular_energia_relativa` | — (u.a.) | Ec. 7 | Suma de amplitudes al cuadrado $\sum v_i^2(t)$. |
| Energía V²s | `calcular_energia_v2s` | V²s | Ec. 8 | Energía relativa escalada por el periodo de muestreo $T_s$. |
| Energía Joules | `calcular_energia_j` | J | Ec. 9 | Energía disipada sobre una impedancia $R$ (`IMPEDANCE_R`, 50 Ω por defecto). |

### 1.3 Temporales / entre pulsos (series de eventos)

| Métrica (GUI) | Función (`metricas.py`) | Unidad | Ecuación | Descripción |
|---|---|---|---|---|
| Delta T | `calcular_delta_t` | s | Ec. 11 | Separación temporal directa entre pulsos consecutivos. |
| Log Delta T | `calcular_log_delta_t` | — | Ec. 13 | Logaritmo natural de Delta T (con protección epsilon). |

### 1.4 Tasas / métricas de ventana temporal $T_w$ (acumuladas)

| Métrica (GUI) | Función (`metricas.py`) | Unidad | Ecuación | Descripción |
|---|---|---|---|---|
| Tasa de Pulsos | `calcular_tasa_pulsos_array` | Hz | Ec. 15 | Densidad de disparos por segundo en la ventana $T_w$: $\lambda_w = N_{pulsos}/T_w$. |
| Tasa de Energía | `calcular_tasa_energia_array` | rel/s | Ec. 10 | Energía relativa total de la ventana dividida entre $T_w$: $\dot E_w = \sum E_{rel,i}/T_w$. |
| Tasa de Ráfagas | `calcular_tasa_rafagas_array` | Hz | Ec. 47 | Número de ráfagas detectadas por segundo: $R_{burst} = N_{ráfagas}/T_w$. |

Funciones auxiliares de ráfaga (no expuestas como métrica individual en la GUI, pero usadas por
las tasas anteriores): `calcular_rafagas` (Ec. 43–44, cuenta ráfagas con criterio
$\Delta t \le \tau = 10\,\text{ms}$ y $N_{min}=5$ pulsos), `calcular_duracion_rafagas` (Ec. 45) y
`calcular_energia_rafagas` (Ec. 46).

---

## 2. Métricas en el dominio de la frecuencia

| Métrica (GUI) | Función (`metricas.py`) | Unidad | Ecuación | Descripción |
|---|---|---|---|---|
| Frecuencia Eq. (Feq) | `calcular_feq` | MHz | — | Frecuencia equivalente: momento de segundo orden del espectro de potencia $\lvert FFT(v)\rvert^2$ (limitado a `FREQ_LIMIT_UHF`/`FREQ_LIMIT_AE`). |
| Frec. Aprox. | `calcular_f_aprox` | Hz | Ec. 23 | Frecuencia aproximada derivada de la tasa de cruces por cero: $f \approx ZCR \cdot f_s / 2$. |
| — (espectro base, panel FFT) | `welch_1ghz` | V²/Hz vs. Hz | — | Densidad espectral de potencia (Welch), vectorizada para matrices de señales; recorta al límite de frecuencia configurado. |

> `Frec. Aprox.` se deriva de una medida temporal (ZCR) pero su resultado es una estimación de
> frecuencia, por lo que se agrupa aquí junto al resto de métricas espectrales.

---

## Resumen por cantidad

| Dominio | # Métricas |
|---|---|
| Tiempo (amplitud/forma, energía, entre-pulsos, tasas) | 15 |
| Frecuencia | 3 |
