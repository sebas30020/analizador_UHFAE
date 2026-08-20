# Feature: Mapas de separación 2D (Gráfica #4) y 3D (Gráfica #5)

## 0. Antes de escribir una sola línea de código

Este repositorio ya contiene la aplicación funcionando: el sistema de métricas, su cálculo, las Gráficas #1, #2 y #3, y la herramienta de filtrado. **No asumas nada sobre cómo está implementado: léelo.**

Explora el proyecto y responde (en texto, antes de programar):

1. ¿Dónde y cómo se define el catálogo de métricas que el usuario puede seleccionar? ¿Cómo se calcula el valor de una métrica para una señal dada?
2. ¿Cómo se renderizan hoy las Gráficas #1, #2 y #3? ¿Qué librería de graficado se usa? ¿Hay soporte 3D en ella?
3. ¿Cómo está implementada la máscara de filtrado? ¿Cuál es la única fuente de verdad del conjunto de señales visibles?
4. ¿Cómo se representa el *cluster* de una señal? ¿Existe ya un mapa de colores/leyenda por cluster?
5. ¿Cuál es el flujo exacto que hoy dispara "graficar esta señal individual en la Gráfica #2"? Quiero reutilizar ese mismo camino de código, no duplicarlo.
6. ¿Cómo se maneja el estado de la UI (qué métricas están graficadas, orden, selección actual)?

Luego preséntame **un plan por etapas** y espera mi visto bueno antes de implementar. Si algo queda ambiguo, **pregunta; no inventes.**

---

## 1. Qué se va a construir

Dos nuevas visualizaciones, en la misma ventana donde ya viven las gráficas de métricas:

- **Gráfica #4 — Mapa de separación 2D**: scatter donde el eje X y el eje Y son **dos métricas cualesquiera** del mismo catálogo que ya se usa para graficar métricas en el tiempo.
- **Gráfica #5 — Mapa de separación 3D**: scatter 3D donde X, Y y Z son **tres métricas cualesquiera** del mismo catálogo.

### Modelo de datos (crítico — no lo reinterpretes)

- **Un punto = una señal.** No es una serie temporal.
- Las coordenadas de ese punto son los valores escalares de las métricas seleccionadas **para esa señal**, exactamente los mismos valores que hoy se usan para ubicar esa señal en las gráficas de métricas existentes.
- **No reimplementes el cálculo de métricas.** Consume el resultado ya existente. Si no hay una función/servicio reutilizable, refactoriza para extraerlo, pero no dupliques fórmulas.
- Maneja explícitamente NaN / infinitos / métricas no disponibles para una señal: esos puntos se omiten y se reporta el conteo de omitidos en la UI (ej. "3 señales sin valor para <métrica>").

---

## 2. Ubicación y orden en la interfaz (requisito estricto)

Los mapas van **siempre al final**, después de todas las gráficas de métricas:

```
[Gráfica #1]
[Gráfica #2]
[Gráfica #3]
[Gráfica de métrica 1]
[Gráfica de métrica 2]
[Gráfica de métrica N]   <- las métricas crecen hacia abajo
[Gráfica #4 — Mapa 2D]   <- los mapas siempre quedan debajo
[Gráfica #5 — Mapa 3D]
```

- Si se grafica una métrica nueva, su gráfica se **inserta arriba de los mapas**, empujándolos hacia abajo. Nunca al revés.
- Si se elimina una métrica, el orden relativo se conserva y los mapas suben, siempre manteniéndose últimos.
- Los mapas **jamás** quedan intercalados entre gráficas de métricas.
- Implementa esto por **orden garantizado en el renderizado** (los mapas son un bloque de cola), no parcheando el DOM a mano.

---

## 3. Controles

Cada mapa es **completamente independiente** del otro:

**Gráfica #4 (2D)**
- Checkbox para habilitar / deshabilitar el mapa.
- Selector de métrica para eje X.
- Selector de métrica para eje Y.

**Gráfica #5 (3D)**
- Checkbox propio, independiente del anterior.
- Selectores de métrica para ejes X, Y y Z.

Reglas:
- Deshabilitado = el mapa no se renderiza (y no consume cómputo), pero su configuración de ejes se conserva para cuando se vuelva a habilitar.
- Los selectores ofrecen el **mismo catálogo de métricas** que ya existe. Si se agregan métricas al catálogo, aparecen aquí automáticamente.
- Si un eje no tiene métrica asignada, el mapa muestra un estado vacío informativo, no un error.
- Se permite repetir la misma métrica en dos ejes, pero muéstralo con una advertencia discreta.
- El estado (habilitado/deshabilitado + ejes elegidos) debe persistir igual que persiste el resto del estado de la app.

---

## 4. Integración con el filtrado (no negociable)

- Los mapas consumen **la misma máscara de filtrado** que el resto de la aplicación.
- Con filtrado activo, **solo** se grafican las señales que pasan la máscara. Nunca todas.
- El cambio de filtro se refleja en los mapas **en vivo**, sin necesidad de re-seleccionar ejes ni re-habilitar el mapa.
- No crees una segunda fuente de verdad ni una copia del conjunto de señales.

---

## 5. Funcionalidades cruzadas a cablear (todas)

1. **Click en un punto → Gráfica #2.** Al seleccionar un punto (una señal) en el mapa 2D o 3D, esa señal se grafica individualmente en el tiempo en la Gráfica #2, usando el mismo mecanismo que ya existe hoy.
2. **Color y forma por cluster + leyenda.** Reutiliza la paleta/criterio de cluster ya existente en la app para mantener coherencia visual. La leyenda permite mostrar/ocultar clusters, y ese toggle afecta al mapa correspondiente.
3. **Resaltado bidireccional.** La señal actualmente seleccionada (venga de la Gráfica #2, de otra gráfica, o del otro mapa) aparece resaltada en #4 y en #5 simultáneamente. El hover también se propaga entre #4 y #5.
4. **Selección por lazo / caja que alimenta el filtrado.** Poder encerrar un conjunto de puntos y usarlo para crear o actualizar la máscara de filtrado, integrándose con la herramienta de filtrado existente (no un filtro paralelo). Debe poder deshacerse / limpiarse.
5. **Exportación.** Exportar imagen del mapa y exportar los datos graficados (identificador de señal, cluster, y valor por eje) en el mismo formato/flujo que ya use la app para exportar.
6. **Tooltip** al pasar sobre un punto: identificador de la señal, su cluster y los valores de cada eje.
7. **Navegación**: zoom y paneo en 2D; zoom, paneo y rotación en 3D; botón de reset de vista en ambos.

---

## 6. Fuera de alcance (explícitamente NO aplicar a #4 y #5)

No tiene sentido en un mapa de separación, así que **estos controles no deben aparecer** en las Gráficas #4 y #5 (ocultos, no simplemente deshabilitados):

- Línea de referencia
- Promedio / línea de promedio
- Línea de tendencia
- Cualquier otro control cuyo significado dependa del eje temporal

Si encuentras controles adicionales de este tipo en el código, lístamelos y confirma conmigo antes de decidir si aplican o no.

---

## 7. Restricciones técnicas

- **Reutiliza la librería de graficado que ya usa el proyecto.** Si no soporta 3D, propóneme opciones con pros/contras y **espera aprobación antes de instalar cualquier dependencia nueva.**
- Sigue las convenciones existentes del repo: estructura de carpetas, nomenclatura, patrón de estado, estilos/tema.
- **Cero regresiones**: las Gráficas #1, #2, #3, las gráficas de métricas y el filtrado deben seguir funcionando exactamente igual.
- Rendimiento: considera el caso de muchas señales (decimación, renderizado acelerado, o memoización de los datos derivados). No recalcules métricas en cada render.
- Cambios incrementales y revisables, con commits lógicos y separados.

---

## 8. Criterios de aceptación

- [ ] Puedo habilitar el mapa 2D sin habilitar el 3D, y viceversa.
- [ ] Puedo elegir libremente cualquier métrica del catálogo en cada eje de cada mapa, de forma independiente.
- [ ] Cada punto corresponde a una señal, con coordenadas consistentes con las métricas ya mostradas en el resto de la app.
- [ ] Con filtrado activo, los mapas muestran únicamente las señales que pasan la máscara, y se actualizan en vivo.
- [ ] Al graficar una métrica adicional, su gráfica aparece debajo de las demás métricas y **arriba** de los mapas; los mapas siguen siendo los últimos.
- [ ] Al hacer click en un punto de #4 o #5, esa señal se grafica individualmente en la Gráfica #2.
- [ ] Los puntos están coloreados por cluster, con leyenda funcional.
- [ ] La selección se resalta de forma bidireccional entre #2, #4 y #5.
- [ ] La selección por lazo/caja alimenta la herramienta de filtrado existente.
- [ ] Puedo exportar imagen y datos de cada mapa.
- [ ] Los controles de línea de referencia, promedio y tendencia no aparecen en #4 ni #5.
- [ ] Nada de lo que funcionaba antes se rompió.

---

## 9. Cómo quiero que trabajes

1. Primero el reconocimiento del punto 0 y el plan por etapas → espera mi aprobación.
2. Implementa por etapas, mostrándome qué archivos tocas y por qué.
3. Al terminar cada etapa, dime qué probar manualmente.
4. Si en cualquier momento una decisión de diseño no está determinada por este documento ni por el código existente, **pregúntame antes de decidir.**