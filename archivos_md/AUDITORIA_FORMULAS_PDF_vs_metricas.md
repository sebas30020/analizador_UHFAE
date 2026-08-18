# Auditoría: fórmulas de `metricas.py` vs. `Informe_variables_PD_linea_base_alarmas_v2 (1).pdf`

Contraste ecuación por ecuación entre la implementación existente
(`analizador_nuevo/metricas.py`) y el informe técnico de referencia (Ecuaciones 1–48), previo a
portar las fórmulas al nuevo registro de métricas (Fase 2). Incluye una verificación empírica
sobre datos reales de `med_5_ago_3.hdf5` para el hallazgo más relevante (§1).

**Resultado general:** las fórmulas matemáticas de `metricas.py` **coinciden exactamente** con las
ecuaciones del PDF en todos los casos que el PDF cubre (RMS, energías, kurtosis, skewness, crest
factor, ZCR, f_aprox, Δt, log Δt, tasas, ráfagas). Se encontraron **3 hallazgos** que requieren
decisión antes de fijar la versión `1` de los plugins de métrica, y 1 corrección de etiqueta.

---

## 1. Hallazgo principal — Falta corrección de línea base (Ecuación 4)

El PDF establece como **convención obligatoria** (no opcional) que todas las variables de forma de
onda se calculan sobre la señal **corregida por offset**, no sobre la señal cruda:

> Ecuación 4: `x_i[n] = v_i[n] - b_i`
> "Convención usada en este informe: las variables de forma de onda se calculan sobre `x_i[n]`...
> Si se usan señales sin corrección de offset, **RMS, energía, skewness, kurtosis y entropía
> pueden quedar sesgadas**."

**`metricas.py` no implementa esta resta en ningún punto.** Todas las funciones (`calcular_rms`,
`calcular_energia_relativa`, `calcular_shannon_entropy`, etc.) operan directamente sobre `data`
cruda, tal como llega del HDF5. Esto es distinto y adicional a la normalización por escala
vertical (§3.1 de `FASE0_DISENO...md`), que es una división, no una resta de offset.

**Matiz matemático:** de las 5 variables que el PDF señala como sesgables, **Skewness y Kurtosis
son en realidad invariantes a un offset constante** (ambas se calculan como momentos respecto a su
propia media `μ_i`, que absorbe cualquier offset constante — no importa si a `x_i[n]` se le suma
una constante, `μ_i` se desplaza igual y `(x-μ)` no cambia). El PDF es impreciso en ese punto. Las
que sí son sensibles de verdad son **RMS, energía (las tres variantes) y entropía de Shannon**
(y, por extensión, **Crest Factor**, **Rise Time** y **ZCR**, que dependen del pico/cruces por cero
absolutos, no de un informe explícito del PDF pero con el mismo problema de fondo).

**Verificación empírica sobre `med_5_ago_3.hdf5`** (9 trazas por sensor, comparando la media de la
región de cabecera de la traza contra la media de la traza completa):

| Sensor | Posición del pico | Offset observado (media cabecera vs. media completa) |
|---|---|---|
| UHF | Consistente: ≈ 20–28 % de la longitud de la traza (muestra 620–850 de 3000) | Pequeño, del orden del ruido (diferencias ~0.003–0.005 V, comparable a `head_std`) |
| AE | **Inconsistente**: el pico aparece en cualquier posición (432 a 7220 de 10000, sin patrón fijo) | También pequeño, del mismo orden que el ruido |

**Conclusión de la verificación:** en este archivo no hay un offset DC grande y sistemático — la
diferencia entre "antes del pulso" y "traza completa" es del orden de la desviación estándar del
ruido, no una desviación grande y consistente. Aun así, la corrección de línea base es la práctica
correcta exigida por el documento de referencia, y **el patrón de disparo difiere entre sensores**:
UHF tiene el pico centrado de forma razonablemente consistente (permite usar una ventana fija de
cabecera, p. ej. las primeras N muestras, como región de línea base), mientras que **AE no tiene
una posición de disparo fija dentro de la traza**, por lo que "usar las muestras previas al evento"
(recomendación del PDF) no es directamente aplicable con una ventana fija — requeriría localizar el
inicio real del pulso por traza (p. ej. por umbral o energía acumulada) antes de poder tomar
muestras "previas".

**Necesito que definas la estrategia antes de escribir `core/normalization.py` (Fase 1):**
- **Opción A — Ventana fija de cabecera por sensor:** usar las primeras `k` muestras de cada traza
  como estimador de `b_i` (media de esa ventana). Simple, funciona razonablemente para UHF por el
  patrón de disparo observado; menos fiable para AE.
- **Opción B — Detección de inicio de pulso:** estimar `b_i` con las muestras anteriores al primer
  cruce de un umbral (p. ej. sobre el ruido de fondo), específico por traza. Más correcto, más
  costoso, requiere calibrar el umbral por sensor.
- **Opción C — No corregir offset (mantener el comportamiento actual de `metricas.py`):**
  documentar explícitamente la desviación respecto al PDF y aceptar el riesgo, dado que el offset
  medido en los datos reales es pequeño (del orden del ruido). Es la opción de menor esfuerzo y la
  más fiel al código ya validado del proyecto anterior.
- **Opción D — Restar la mediana (o un percentil bajo) de toda la traza como aproximación robusta
  al offset**, sin necesitar localizar el pulso ni asumir una ventana fija. Funciona igual para
  UHF y AE, a costa de ser una aproximación (mezcla algo de la energía del propio pulso si este
  ocupa una fracción grande de la ventana, advertencia que el propio PDF hace).

Mi recomendación técnica era la Opción D, pero **el usuario decidió la Opción C**: no se corrige
offset de línea base. `core/normalization.py` implementa únicamente la división por `vrange`
(§3.1 de `FASE0_DISENO...md`), igual que el `metricas.py` ya validado de `analizador_nuevo`. Esta
desviación respecto a la Ecuación 4 del PDF queda documentada aquí y en el docstring del módulo de
normalización — justificada por la verificación empírica de §1 (offset del orden del ruido en los
datos reales) y por mantener paridad con el código ya validado.

---

## 2. Hallazgo — Régimen "grupo" es más amplio de lo que reflejaba el diseño de Fase 0

El PDF define, con ecuación propia, el valor representativo de **6 variables puntuales** dentro de
una ventana `w`, siempre como **mediana**:

| Variable puntual | Ecuación del valor de grupo (mediana) |
|---|---|
| Δt | Ec. 12: `Δt_w = mediana{Δt_i : i∈I_w}` |
| log Δt (ℓ) | Ec. 14: `ℓ_w = mediana{ℓ_i : i∈I_w}` |
| Entropía Shannon (normalizada) | Ec. 21: `H_w = mediana{H_i,norm : i∈I_w}` |
| Kurtosis | Ec. 28: `K_w = mediana{K_i : i∈I_w}` |
| Crest Factor | Ec. 31: `CF_w = mediana{CF_i : i∈I_w}` |
| Skewness | Ec. 33: `S_w = mediana{S_i : i∈I_w}` |

El diseño de Fase 0 (`FASE0_DISENO...md` §2.3) solo registró **3** métricas de régimen "grupo"
(Tasa de Pulsos, Tasa de Energía, Tasa de Ráfagas), porque son las únicas que no se derivan
trivialmente de "reducir" una métrica puntual. Las 6 de la tabla de arriba **no son métricas
nuevas**: son la métrica puntual ya existente, reducida por mediana sobre las señales de la
ventana. El propio `metricas.py` ya trae las utilidades genéricas para esto
(`calcular_mediana_grupo`, `calcular_percentil_grupo`), coherente con el `reduction='mean'|'median'|'sum'`
que ya usaba `agrupar_por_ventanas_temporales` en `analizador_nuevo`.

**Recomendación (ajuste de arquitectura, no de fórmulas):** en el contrato de plugin (§8 de
`FASE0_DISENO...md`), el régimen "grupo" se resuelve en dos mecanismos, no uno:
1. **Reducción genérica** — cualquier métrica puntual puede solicitarse en régimen "grupo"
   aplicando un reductor (`mediana` por defecto, coherente con el PDF; `percentil`, `media` como
   alternativas) sobre sus valores puntuales dentro de la ventana. No requiere un plugin nuevo por
   variable.
2. **Métricas de grupo intrínsecas** — Tasa de Pulsos, Tasa de Energía, Tasa de Ráfagas, que no son
   la reducción de una métrica puntual sino una fórmula propia a nivel de ventana (Ec. 15, 10, 47).

Esto **no cambia el conteo de "métricas nuevas a implementar"** (sigue habiendo 17 puntuales + 3 de
grupo intrínsecas), solo aclara que las 17 puntuales quedan automáticamente disponibles también en
régimen "grupo" vía el reductor genérico, sin trabajo adicional — es una simplificación, no una
carga extra.

---

## 3. Hallazgo — Etiqueta de unidad incorrecta para Entropía Shannon

`calcular_shannon_entropy` en `metricas.py` calcula y retorna **`H_i,norm`** (Ecuación 20 del PDF:
`H_i / log2(B)`, valor adimensional en [0,1]), **no** `H_i` (Ecuación 19, en bits). El código hace
la división por `log2(bins)` explícitamente antes de retornar.

Sin embargo, `analizador_datos.py` (`get_metrics_config`) etiqueta esta métrica con unidad
`' (bits)'`, que solo sería correcta para `H_i` sin normalizar. **La fórmula está bien calculada
(coincide con Ec. 20)**, es solo la unidad mostrada en la GUI antigua la que es incorrecta.

**Acción para el nuevo registro:** declarar la unidad de `shannon_entropy` como adimensional
(`""`, rango `[0, 1]`), no `"bits"`, en el contrato del plugin.

---

## 4. Hallazgo — Métricas de tasa: el `T_w` nunca debe inferirse de los timestamps observados

Las tres métricas de tasa (`calcular_tasa_pulsos_array`, `calcular_tasa_energia_array`,
`calcular_tasa_rafagas_array`) tienen, en `metricas.py`, un *fallback* cuando no se pasa `T_w`
explícito: usan `timestamps[-1] - timestamps[0]` (el span observado de los datos) como
denominador. Esto **no coincide con las Ecuaciones 10, 15 y 47 del PDF**, donde `T_w` es siempre la
**duración declarada de la ventana** (p. ej. 60 s fijos), no el span real de los pulsos dentro de
ella. Si una ventana de 60 s solo tiene pulsos agrupados en sus primeros 5 s, usar el span
observado (5 s) en vez de la duración declarada (60 s) **infla la tasa calculada por un factor de
12x** — un error significativo, no cosmético.

**Acción para el nuevo motor de agrupamiento (`core/grouping.py`):** al invocar estas métricas en
régimen "grupo", el motor **debe pasar siempre `T_w` explícito** — la duración de la ventana
definida por el criterio de agrupamiento (§4.2 del PROMPT maestro), nunca dejar que la métrica
infiera el intervalo desde los timestamps. El *fallback* actual de `metricas.py` no se porta al
nuevo registro; se elimina esa rama y `T_w` pasa a ser un parámetro obligatorio para estas 3
métricas cuando se invocan en régimen grupo.

---

## 5. Sin hallazgos — resto de fórmulas verificadas exactas

Verificación exacta contra el PDF (mismo resultado numérico, misma normalización N vs N-1,
población vs muestral):

| Métrica | Ecuación PDF | Verificación |
|---|---|---|
| RMS | Ec. 29 | ✅ Exacta (`np.mean`, divisor N) |
| Energía Relativa | Ec. 7 | ✅ Exacta |
| Energía V²s | Ec. 8 | ✅ Exacta |
| Energía Joules | Ec. 9 | ✅ Exacta |
| Kurtosis (Pearson Ec.26 / exceso Ec.27) | Ec. 26–27 | ✅ Exacta (`scipy.stats.kurtosis`, `bias=True`, conversión Fisher→Pearson +3 correcta) |
| Skewness | Ec. 32 | ✅ Exacta (`scipy.stats.skew`, `bias=True`, momento poblacional) |
| Crest Factor | Ec. 30 | ✅ Exacta |
| Entropía Shannon (valor) | Ec. 16–20 | ✅ Fórmula exacta (ver etiqueta incorrecta en §3) |
| ZCR | Ec. 22 | ✅ Coincide, con nota menor (ver abajo) |
| Frec. Aprox. | Ec. 23 | ✅ Exacta |
| Δt | Ec. 11 | ✅ Exacta (primer pulso definido como 0 por convención, el PDF lo deja indefinido para i=1) |
| Log Δt | Ec. 13 | ✅ Exacta |
| Ráfagas: conteo, duración, energía | Ec. 43–46 | ✅ Exactas |
| Tasa de pulsos / energía / ráfagas (fórmula) | Ec. 15, 10, 47 | ✅ Fórmula exacta; ver caveat de `T_w` en §4 |

**Nota menor sobre ZCR:** cuando se usa zona muerta (`dead_zone > 0`), el código filtra las
muestras dentro de la zona muerta antes de contar cruces, pero **sigue dividiendo por
`n_points - 1`** (el total original), no por el número de transiciones realmente evaluadas tras el
filtrado. El PDF no es explícito sobre si el denominador debe reducirse también — es una zona gris
del propio documento fuente, no una desviación clara. Se porta el comportamiento actual sin cambios
(división siempre por `N_i - 1`), documentado explícitamente en el docstring del plugin.

**Fuera del alcance del PDF (extensiones propias de `analizador_nuevo`, sin ecuación de referencia
que contrastar, se portan tal cual):** VPP, Vmax, Rise Time, Tiempo Eq. (Teq), Frecuencia Eq. (Feq).

---

## 6. Resumen de acciones

1. ~~Decidir estrategia de corrección de línea base~~ — **Resuelto: Opción C (no corregir),
   documentado en `core/normalization.py`.**
2. Ajustar el contrato de régimen "grupo" para incluir el reductor genérico por mediana (§2) —
   se aplica al escribir `metrics/registry.py` y `metrics/engine.py` (Fase 2).
3. Corregir la unidad de `shannon_entropy` a adimensional en el nuevo registro (§3) — Fase 2.
4. Eliminar el *fallback* de `T_w` inferido en las 3 métricas de tasa; `core/grouping.py` siempre lo
   provee explícito (§4) — Fase 2.
