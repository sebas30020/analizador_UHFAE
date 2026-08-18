# Nueva funcionalidad: línea horizontal de referencia en las gráficas #3

## 0. Reconocimiento previo del código

Antes de escribir código, explora el proyecto que está en este directorio y establece por ti mismo el contexto. Identifica y confirma:

- Qué stack y qué librería de gráficas se usan, y cómo se construyen y renderizan las figuras.
- Qué módulos generan las gráficas **#3**, cuántas hay, y cómo se instancian: si es una gráfica por métrica, una figura con subgráficas, o un componente que se repite.
- Cómo se estructuran los datos de cada métrica y en qué unidad está el eje temporal (segundos, milisegundos, timestamps absolutos, tiempo relativo al inicio).
- **Cómo se define el "inicio del experimento"** en el proyecto: si existe una marca explícita, si es el `t = 0` del eje o si es el timestamp del primer dato disponible. Esta definición es crítica porque fija el límite inferior del intervalo de cálculo.
- Si las series ya vienen ordenadas por tiempo y si hay un índice temporal aprovechable.
- Dónde se define el layout de la GUI, y qué patrón sigue el proyecto para declarar un control, propagar su estado y disparar el redibujado.
- La arquitectura y las convenciones de código vigentes: separación entre cálculo, transformación y presentación; estilo de funciones; manejo de estado; uso de caché si lo hay.
- Los controles ya existentes de visualización, para integrar los nuevos en el mismo bloque y no crear un panel paralelo.

**Esta funcionalidad debe seguir la misma filosofía de programación que ya tiene el programa.** No introduzcas un paradigma, una capa de abstracción ni un mecanismo de estado distintos de los que el proyecto ya usa. Si detectas una ambigüedad real que cambie el diseño de la solución, pregunta antes de implementar en lugar de asumir.

---

## 1. Objetivo

Incorporar en cada gráfica **#3** una línea horizontal de referencia cuyo valor es el **promedio de la métrica calculado sobre los datos comprendidos entre el inicio del experimento y el minuto `t`**, siendo `t` un parámetro definido por el usuario desde la GUI.

El propósito es disponer de una referencia visual estable —una línea base— contra la cual comparar el comportamiento posterior de la métrica.

---

## 2. Especificación funcional

### 2.1 Cálculo del valor

- **Intervalo de cálculo:** desde el inicio del experimento hasta el minuto `t`. Es un intervalo acumulado desde el origen, no una ventana móvil.
- **Estadístico:** media aritmética de los valores de la métrica cuyas marcas temporales caen dentro de ese intervalo.
- **Límite superior:** incluir las muestras con tiempo menor o igual a `t`. Documenta la convención adoptada.
- **Una línea por métrica.** Cada gráfica #3 corresponde a una métrica con su propio conjunto de datos, de modo que **cada una tendrá su propio valor de promedio y, por lo tanto, su propia línea horizontal**. No se comparte ni se promedia entre métricas.
- **Un único `t` global.** El mismo valor de intervalo se aplica simultáneamente a todas las métricas. Cambiar `t` recalcula todas las líneas a la vez.
- **Fuente de los datos:** el promedio debe calcularse sobre los datos de la métrica, de forma **independiente de la configuración de agrupación o suavizado activa en la interfaz**. La línea de referencia no debe cambiar de valor solo porque el usuario modifique cómo se visualizan los puntos. Si al leer el código detectas una razón de peso para hacerlo de otro modo, exponla antes de decidir.
- **Valores nulos o NaN:** excluirlos del promedio, no tratarlos como cero. Documentar el criterio.

### 2.2 Casos borde

- **`t` mayor que la duración del experimento:** usar todos los datos disponibles. No es un error; puede indicarse discretamente en la interfaz.
- **`t` menor o igual que cero, o sin muestras dentro del intervalo:** no dibujar la línea e informar la situación con un mensaje breve y formal, sin bloquear el resto de la gráfica.
- **Métrica sin datos:** la gráfica correspondiente simplemente no muestra línea; las demás sí.
- **Una sola muestra en el intervalo:** el promedio es ese valor; es válido y debe dibujarse.

### 2.3 Representación gráfica

- Línea **horizontal**, trazada a lo ancho de todo el lienzo de la gráfica, no solo sobre el tramo del intervalo de cálculo.
- **Estilo punteado** y con **transparencia** (opacidad reducida, orientativamente `0.4–0.6`), de modo que sirva de referencia sin competir visualmente con la serie de la métrica.
- Debe quedar por detrás de la serie principal en el orden de dibujado.
- Incluir el valor numérico del promedio en la leyenda, en una etiqueta junto a la línea o en el tooltip, con un número de decimales coherente con el resto de la interfaz y con la unidad de la métrica cuando corresponda.
- El estilo debe respetar la paleta y las convenciones visuales que el proyecto ya usa. No introducir colores nuevos si existe un color de referencia o auxiliar ya definido.

### 2.4 Controles en la GUI

Dos controles, ubicados en el mismo bloque de opciones de visualización que ya usan las gráficas:

1. **Selector de visibilidad.** Switch o checkbox que dibuja y elimina la línea del gráfico. Etiqueta sugerida: `Mostrar línea de referencia`. Estado por defecto: desactivado, para no alterar la vista actual del programa.
2. **Definición del intervalo.** Campo numérico —opcionalmente acompañado de un slider— para fijar `t` en minutos. Etiqueta sugerida: `Definir intervalo de referencia (min)`. Debe declarar la unidad de forma explícita, tener un valor por defecto razonable según la duración típica de los experimentos que observes en el proyecto, y validar la entrada (numérico, positivo, dentro de un rango admisible).

Comportamiento:

- El campo de intervalo se deshabilita o atenúa cuando el selector de visibilidad está desactivado.
- Los cambios se reflejan de inmediato en todas las gráficas #3, sin botón de aplicación.
- Conmutar la visibilidad **no** debe recalcular el promedio si `t` no cambió, ni alterar el zoom, el paneo o el rango seleccionado por el usuario.
- El estado de ambos controles persiste durante la sesión y se mantiene coherente al cambiar otros filtros.

---

## 3. Convenciones de redacción del texto visible

Aplican a todo el texto que llegue a la interfaz: etiquetas, botones, títulos, leyendas, tooltips y mensajes de estado o error.

- **Registro formal.** Redacción seria, técnica y neutra. Sin coloquialismos, emojis, signos de exclamación ni comentarios en primera persona.
- **Acciones en infinitivo.** Los controles se nombran con verbo en infinitivo seguido del objeto: `Mostrar línea de referencia`, `Definir intervalo de referencia`, `Graficar datos`, `Seleccionar señales`, `Filtrar`, `Exportar resultados`. Sin imperativos, gerundios ni sustantivos sueltos donde corresponde una acción.
- **Sin filtraciones de la documentación interna.** El texto de la interfaz no debe contener referencias al proceso de desarrollo ni a los archivos `.md` del repositorio: nada de "fase 7 del proyecto", "sprint", "milestone", "ver README", "TODO", números de issue ni nombres de rama. La interfaz solo describe qué hace el control y qué significa el dato.
- **Consistencia terminológica.** Si el proyecto ya nombra una entidad de cierta forma (señal, evento, métrica, agrupación, experimento), respétala en lugar de introducir sinónimos.
- **Idioma.** Mantener el idioma que ya use la interfaz, sin mezclar idiomas en una misma pantalla.
- **Auditoría.** Si al integrar los controles encuentras texto preexistente que incumpla estas reglas, corrígelo y repórtalo en la nota de entrega.

---

## 4. Rendimiento — requisito duro

**Esta funcionalidad no debe introducir absolutamente ningún cuello de botella adicional.** La velocidad de procesamiento actual del programa debe conservarse.

### 4.1 Estrategia de implementación

- **Cálculo vectorizado.** Nada de bucles punto a punto sobre las muestras.
- **Corte del intervalo en tiempo logarítmico.** Si la serie está ordenada por tiempo, localizar el índice de corte correspondiente a `t` mediante búsqueda binaria en lugar de recorrer o filtrar la serie completa en cada cambio.
- **Sumas acumuladas.** Evaluar precalcular una única vez, por métrica, la suma acumulada de los valores y el conteo acumulado de muestras válidas. Con eso, obtener el promedio para cualquier `t` se reduce a dos lecturas indexadas, y mover el control deja de ser proporcional al tamaño de los datos.
- **Caché.** Cachear el resultado por métrica e intervalo. El promedio se recalcula únicamente cuando cambia `t` o cuando cambia el conjunto de datos subyacente. Conmutar la visibilidad no dispara ningún recálculo, solo cambia la visibilidad de la traza.
- **Antirrebote en la entrada.** El campo numérico debe aplicar un retardo breve antes de recalcular, para no disparar un recálculo por cada pulsación de tecla.
- **Independencia de las rutas críticas.** El cálculo de métricas y la lógica de agrupación existentes no deben quedar en un camino más lento que el actual. La línea de referencia es una capa añadida y no debe insertarse dentro de esas rutas.
- **Costo nulo cuando está apagada.** Con el selector desactivado, que es el estado por defecto, el costo adicional respecto de la versión actual debe ser despreciable: no calcular promedios que no se van a mostrar.

### 4.2 Benchmarks específicos de esta tarea

Localiza los benchmarks o pruebas de rendimiento que ya existan en el repositorio y sigue su mismo formato y convenciones. Si el proyecto no los tiene formalizados, mide el estado actual **antes** de modificar el código para disponer de una línea base comparable. Añade mediciones específicas para:

1. **Cálculo del promedio por métrica**, en función del número de muestras dentro del intervalo, contrastando la implementación ingenua con la estrategia elegida.
2. **Recálculo completo al cambiar `t`**, con todas las gráficas #3 presentes, es decir el costo total de actualizar todas las líneas de una sola vez.
3. **Conmutación del selector de visibilidad**, que debe ser prácticamente instantánea y no depender del tamaño de los datos.
4. **Tiempo de construcción y renderizado de las gráficas #3** con la línea activada frente a desactivada.
5. **Comparación contra la línea base** con la funcionalidad apagada, para demostrar que no hay regresión en el estado por defecto.
6. **Costo en memoria** de las estructuras precalculadas y de la caché, en función del volumen de datos.

Reporta cada medición con el conjunto de datos utilizado, el método de medición y los valores antes y después. Si alguna estrategia no cumple el presupuesto de tiempo con el volumen real de datos del proyecto, descártala, elige una alternativa que sí lo cumpla y déjalo documentado.

---

## 5. Criterios de aceptación

1. Cada gráfica #3 muestra su propia línea horizontal, calculada con los datos de su propia métrica.
2. El valor de cada línea corresponde al promedio de la métrica entre el inicio del experimento y el minuto `t`, verificable manualmente contra un cálculo independiente sobre el mismo conjunto de datos.
3. Un único control de intervalo en la GUI fija `t` en minutos y su cambio actualiza simultáneamente las líneas de todas las métricas.
4. La línea se dibuja punteada, con transparencia, a lo ancho de todo el lienzo y por detrás de la serie principal.
5. El valor numérico del promedio es consultable desde la interfaz.
6. Un selector muestra y elimina la línea del gráfico; está desactivado por defecto.
7. Conmutar el selector no recalcula el promedio ni altera el zoom, el paneo o el rango seleccionado.
8. El valor de la línea no cambia al modificar la configuración de agrupación o suavizado de la visualización.
9. Los casos borde se comportan según lo especificado, sin excepciones no controladas ni gráficas en blanco.
10. Todo el texto visible cumple las convenciones de redacción del apartado 3.
11. Los benchmarks del apartado 4.2 están implementados y sus resultados demuestran que no hay degradación respecto de la línea base.
12. La implementación respeta la estructura y las convenciones de código ya presentes en el proyecto.

---

## 6. Entregables

- Implementación de la funcionalidad, integrada con los patrones existentes del proyecto.
- Benchmarks específicos de la tarea, en el formato que ya use el repositorio.
- Nota breve que documente: la definición de "inicio del experimento" adoptada, la convención del límite superior del intervalo, el tratamiento de nulos, la fuente de datos usada para el promedio, la estrategia de cálculo y caché elegida, y cualquier decisión tomada ante una ambigüedad del enunciado.
- Tabla comparativa de rendimiento antes y después, con el conjunto de datos y el método de medición utilizados.
