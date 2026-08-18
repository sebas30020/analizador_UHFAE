# Mejora de visualización en las gráficas #1 y #2

## 0. Reconocimiento previo del código

Antes de escribir código, explora el proyecto que está en este directorio y establece por ti mismo el contexto. Concretamente, identifica y confirma:

- Qué stack y qué librería de gráficas se usan, y cómo se construyen y renderizan las figuras.
- Qué módulos/archivos generan las gráficas **#1** y **#2**, y dónde se define el layout de la GUI y sus controles.
- Qué métrica muestra cada gráfica, qué representa el eje X (tiempo o índice de señal) y qué el eje Y.
- Cómo se representan los **eventos**: de dónde salen los datos, cómo se dibujan las líneas verticales y qué elementos accesorios llevan (etiquetas, anotaciones, bandas, entradas de leyenda).
- Cómo se distinguen internamente las **métricas individuales** (un punto por señal) de las **métricas de grupo** (agregadas por ventana temporal o por cantidad de señales), y cuáles son los modos de agrupación ya implementados.
- El volumen típico de datos por gráfica, porque condiciona la elección de la estrategia de suavizado y la necesidad de cachear.
- El patrón que ya sigue el proyecto para los controles de la GUI y para propagar su estado hasta el redibujado, de modo que lo nuevo se integre con ese patrón en lugar de introducir uno paralelo.

Si después de leer el código queda alguna ambigüedad real que cambie el diseño de la solución, pregunta antes de implementar en lugar de asumir.

## 1. Objetivo general

Implementar dos mejoras de visualización sobre las gráficas #1 y #2, sin alterar el cálculo de las métricas ni la lógica de agrupación ya existente. Ambas deben ser **controlables desde la GUI** y **reversibles en caliente**: el usuario activa y desactiva sin recargar la aplicación ni recalcular los datos de origen.

---

## 2. Tarea 1 — Control de visibilidad de los eventos

### 2.1 Problema

Hoy en las gráficas #1 y #2 se dibujan simultáneamente las métricas y los eventos. Los eventos se representan como líneas verticales que, cuando son numerosas, saturan el gráfico y entorpecen la lectura y el análisis de las métricas.

### 2.2 Requerimiento

Agregar a la GUI una variable de control que permita alternar la visualización de los eventos.

### 2.3 Especificación

- **Tipo de control:** switch o checkbox booleano, siguiendo el componente que ya use el proyecto para controles equivalentes.
- **Etiqueta sugerida:** `Mostrar eventos`.
- **Estado por defecto:** activado, para no alterar el comportamiento actual.
- **Alcance:** un único control que afecta a las gráficas #1 y #2 a la vez. Si al leer el código resulta más natural un control por gráfica, tómalo y documenta la decisión.
- **Elementos afectados:** debe ocultarse **todo** lo asociado al evento, no solo la línea: línea vertical, etiqueta o anotación de texto, marcador, banda sombreada y su entrada en la leyenda.

### 2.4 Restricciones técnicas

- Conmutar el control **no** debe recalcular ni volver a consultar los datos de métricas: solo cambia la visibilidad de las trazas y anotaciones de eventos.
- El zoom, el paneo y el rango seleccionado por el usuario **deben preservarse** al conmutar. Si la librería redibuja la figura completa, conserva explícitamente el estado de los ejes mediante el mecanismo que ofrezca esa librería.
- El estado del control persiste durante la sesión y se mantiene coherente cuando el usuario cambia otros filtros de la GUI.
- Si no hay eventos en el rango visible, el control sigue presente; puede deshabilitarse o indicar "sin eventos".

---

## 3. Tarea 2 — Unión de los puntos de las métricas

### 3.1 Problema

Las métricas se dibujan como puntos sueltos. Se requiere unirlos con líneas para poder evaluar comportamiento y tendencia, pero los dos casos son distintos:

- En las **métricas individuales** (un punto por señal), unir directamente genera una línea caótica en sierra: hay muchas señales y mucha variabilidad, así que la línea cruda no aporta información legible.
- En las **métricas de grupo**, la agregación ya reúne muchas señales y reduce el ruido, de modo que la unión directa sí es informativa por sí sola.

### 3.2 Requerimiento A — Métricas individuales: línea con suavizado

- **Representación:** dibujar los puntos crudos con opacidad reducida (aprox. `0.25–0.35`) o marcadores pequeños, y superponer la **línea suavizada** como elemento principal, bien contrastado. El usuario debe seguir viendo la dispersión real por debajo de la tendencia.
- **Estrategia de suavizado:** implementar al menos una y, si es viable, ofrecer un selector. Evalúa entre:
  - Media móvil (*rolling mean*): simple y predecible.
  - Mediana móvil (*rolling median*): robusta ante outliers; conviene si las señales tienen picos espurios.
  - EWMA / suavizado exponencial: reacciona más rápido a cambios recientes.
  - Savitzky–Golay: preserva mejor la forma de los picos legítimos.
  - LOWESS / LOESS: buen ajuste local, pero costoso con muchos puntos.
  - Spline / PCHIP: **solo** si el objetivo es estético; deja claro que no reduce ruido, únicamente curva la unión.
- **Recomendación de partida:** media o mediana móvil centrada, por bajo costo computacional e interpretabilidad; queda abierto cambiarla después.
- **Control de intensidad:** exponer en la GUI un slider o input numérico para el tamaño de ventana o el factor de suavizado, con rango razonable y valor por defecto acorde al volumen real de datos que hayas observado en el proyecto. Etiqueta sugerida: `Suavizar métricas individuales`.

### 3.3 Requerimiento B — Métricas de grupo: línea directa y suavizado opcional

- Unir los puntos de las métricas agrupadas (por ventana temporal o por cantidad de señales) con una línea directa, para poder evaluar tendencia.
- Ofrecer **además** un control que permita aplicar suavizado también a esa línea de tendencia. Etiqueta sugerida: `Suavizar tendencia agrupada`.
- **Por defecto ese suavizado va desactivado**, ya que la agregación previa reduce el ruido; el usuario lo activa a conveniencia.
- Al activarse debe reutilizar la misma implementación de suavizado del punto anterior, sin duplicar lógica.

### 3.4 Consideraciones de datos

- **Orden:** garantizar que los puntos estén ordenados por el eje X antes de unirlos, o la línea se cruzará sobre sí misma.
- **Huecos:** por defecto, **no** unir a través de gaps significativos: romper la línea en lugar de trazar una recta larga que sugiera una continuidad inexistente. Considerar un umbral de gap máximo configurable.
- **Nulos / NaN:** decidir explícitamente entre interpolar, omitir o romper la línea, y documentar la elección.
- **Espaciado irregular del eje X:** si las muestras no son equiespaciadas, una ventana móvil por número de puntos distorsiona el resultado; preferir ventanas basadas en tiempo cuando el eje X sea temporal.
- **Bordes:** manejar el suavizado en los extremos de la serie (ventana incompleta) sin artefactos ni recorte visual.

---

## 4. Requisitos de GUI

- Agrupar los nuevos controles junto a los ya existentes de las gráficas, en un bloque coherente (por ejemplo un panel de "Opciones de visualización"), respetando el estilo, el espaciado y los componentes que el proyecto ya usa.
- Deshabilitar o atenuar los controles dependientes cuando no apliquen; por ejemplo, el slider de intensidad cuando el suavizado esté apagado.
- Cada control con etiqueta clara y, si el concepto no es obvio, un tooltip breve sobre su efecto.
- Los cambios se reflejan de inmediato en las gráficas, sin botón "Aplicar", salvo que el costo de recálculo lo justifique.

### 4.1 Convenciones de redacción del texto visible

Estas reglas aplican a **todo** el texto que llegue a la interfaz: etiquetas de controles, botones, títulos de gráficas y ejes, leyendas, tooltips, mensajes de estado, advertencias y errores.

- **Registro formal.** Redacción seria, técnica y neutra. Sin coloquialismos, sin emojis, sin signos de exclamación, sin humor ni comentarios en primera persona.
- **Nomenclatura de acciones en infinitivo.** Los botones y controles de acción se nombran con verbo en infinitivo seguido del objeto: `Graficar datos`, `Seleccionar señales`, `Filtrar`, `Elegir métrica`, `Exportar resultados`, `Mostrar eventos`, `Suavizar métricas individuales`. Evitar formas imperativas, gerundios o sustantivos sueltos donde corresponde una acción.
- **Sin filtraciones de la documentación interna.** El texto de la interfaz **no** debe contener referencias al proceso de desarrollo ni a la documentación del repositorio: nada de "fase 7 del proyecto", "sprint", "milestone", "ver README", "TODO", "pendiente de la iteración anterior", números de issue, nombres de rama o cualquier otra jerga de planificación tomada de los archivos `.md`. Esa información pertenece a la documentación, no a la GUI. La interfaz solo describe qué hace el control y qué significa el dato.
- **Tooltips.** Deben explicar el efecto del control sobre la visualización o el dato, en una o dos frases, en el mismo registro formal. No deben remitir a documentos internos ni describir cómo está implementado.
- **Auditoría del texto existente.** Además de aplicar estas reglas al texto nuevo, revisa el texto ya presente en el código de la GUI y en las gráficas #1 y #2. Si encuentras etiquetas, títulos, tooltips o mensajes que incumplan lo anterior —por informalidad, por no estar en infinitivo cuando corresponde, o por arrastrar información de planificación del proyecto— corrígelos. Reporta en la nota de entrega qué textos cambiaste y por qué.
- **Consistencia.** Un mismo concepto se nombra siempre igual en toda la interfaz. Si el proyecto ya usa un término para una entidad (señal, evento, métrica, agrupación), respétalo en lugar de introducir sinónimos.
- **Idioma.** Mantener el idioma que ya use la interfaz del proyecto, sin mezclar idiomas dentro de una misma pantalla.

## 5. Requisitos no funcionales

- **Rendimiento — requisito duro.** Estos cambios **no deben degradar la velocidad de cálculo actual del programa**. La implementación debe igualar o superar los benchmarks de rendimiento ya establecidos en el proyecto; en el peor de los casos, quedar muy cerca de ellos, y solo con una justificación explícita.
  - Localiza los benchmarks, pruebas de rendimiento o mediciones de referencia que existan en el repositorio y úsalos como criterio. Si el proyecto no los tiene formalizados, mide el estado actual **antes** de tocar el código para disponer de una línea base comparable.
  - Mide antes y después sobre el mismo conjunto de datos y reporta ambos números. Presta atención al tiempo de cálculo de métricas, al tiempo de construcción de la figura y al tiempo de respuesta de la interfaz al conmutar un control.
  - El cálculo de métricas y la agrupación no deben quedar en un camino más lento que el actual: el suavizado es una capa de presentación posterior y no debe insertarse dentro de esas rutas.
  - El suavizado debe ser vectorizado, sin bucles punto a punto. Si el volumen lo amerita, cachear el resultado y recalcular solo cuando cambie la ventana o el conjunto de datos subyacente, de modo que mover un control no reprocese datos que no cambiaron.
  - Cuando los controles nuevos estén en su estado por defecto, el costo adicional respecto de la versión actual debe ser despreciable.
  - Si alguna estrategia de suavizado no puede cumplir esto con el volumen real de datos del proyecto (por ejemplo LOWESS sobre series muy grandes), descártala y elige una alternativa que sí lo cumpla, dejándolo documentado.
- **No regresión:** con los valores por defecto (eventos visibles, suavizado de grupo apagado), la aplicación debe verse y comportarse esencialmente igual que hoy, salvo por la incorporación de las líneas de unión.
- **Separación de responsabilidades:** el suavizado es una transformación de presentación. Impleméntalo como función pura y reutilizable, aislada de la construcción de la figura y del cálculo de métricas, para que ambas gráficas y ambos tipos de métrica la compartan.
- **Sin cambios** en el cálculo de métricas ni en la lógica de agrupación existente.

## 6. Criterios de aceptación

1. Existe un control en la GUI que oculta y muestra los eventos en las gráficas #1 y #2; al conmutarlo desaparecen y reaparecen las líneas verticales junto con sus etiquetas y entradas de leyenda.
2. Conmutar ese control no altera el zoom, el paneo ni el rango seleccionado, y no dispara recálculo de métricas.
3. Las métricas individuales se muestran unidas por una línea suavizada, con los puntos crudos visibles pero atenuados por debajo.
4. Existe un control que ajusta la intensidad del suavizado de las métricas individuales, con efecto visible de inmediato.
5. Las métricas de grupo se muestran con sus puntos unidos por una línea.
6. Existe un control, apagado por defecto, que aplica suavizado también a la línea de las métricas de grupo.
7. Huecos y nulos se manejan según el comportamiento documentado, sin líneas que crucen vacíos largos de forma engañosa.
8. Con la configuración por defecto no hay regresión visual ni funcional respecto de la versión actual.
9. Todo el texto visible de la interfaz —el nuevo y el preexistente en la GUI y en las gráficas #1 y #2— está en registro formal, nombra las acciones con verbo en infinitivo y no contiene referencias a fases, sprints, documentos internos ni cualquier otra jerga de planificación del proyecto.
10. Las mediciones de rendimiento posteriores al cambio igualan o superan la línea base, y los números de antes y después están reportados.

## 7. Entregables

- Implementación de ambas tareas, integrada con los patrones ya presentes en el proyecto.
- Nota breve que documente: la estrategia de suavizado elegida y por qué, el manejo de gaps y nulos, los textos de interfaz corregidos durante la auditoría, y cualquier decisión de diseño tomada ante una ambigüedad del enunciado.
- Tabla comparativa de rendimiento antes y después del cambio, con el conjunto de datos y el método de medición utilizados.
