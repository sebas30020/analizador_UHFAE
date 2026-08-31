# Fusión de dos bases de datos en una (entrega)

Sección "Fusionar bases de datos" en el panel de control: permite seleccionar dos archivos HDF5
independientes (originales o exportaciones filtradas) y combinarlos en un archivo único compatible
con `FilteredExportReader` y `open_reader`.

## 1. Decisiones de diseño y restricciones

1. **Orden cronológico semántico (Archivo 1 → Archivo 2)**:
   El archivo 2 se coloca inmediatamente a continuación del archivo 1. Los timestamps absolutos
   no importan; lo que importa es la duración relativa y acumulativa del experimento. El archivo 2
   conserva intactas sus distancias internas pero se desplaza en bloque mediante un `delta`.

2. **Cálculo del desfase temporal (`delta`) exclusivo por señales**:
   Se calcula una sola vez y se aplica a todas las señales, ambientales y eventos del archivo 2:
   ```
   t_fin_1  = máximo timestamp de TODAS las SEÑALES del archivo 1
   t_ini_2  = mínimo timestamp de TODAS las SEÑALES del archivo 2
   delta    = (t_fin_1 - t_ini_2) + separacion
   ```
   `separacion` = mediana de diferencias consecutivas del sensor con más señales del archivo 1 (o 0.0).
   Basarse solo en señales evita abrir huecos visuales irreales cuando los sensores ambientales se
   detienen minutos después de las señales.

3. **Solape ambiental en la costura**:
   Debido a que adquisiciones reales (`initial_dead_time_s = 300.0`) inician ambientales ~5 min antes
   de la primera señal, al aplicar el desfase `delta` esos puntos ambientales caen sobre la cola del
   archivo 1. Esto es natural y correcto en datos asíncronos. La función calcula la duración de dicho
   solape (`merge_seam_overlap_s`) y lo reporta en el mensaje de éxito en la GUI.

4. **Escritura secuencial en streaming (Pico de RAM acotado)**:
   Para no duplicar el uso de memoria en datasets grandes, el archivo 1 se ingiere, escribe en HDF5
   (con datasets `maxshape=(None, M)`) y se libera de la RAM con `del`. Luego se ingiere el archivo 2,
   se redimensionan los datasets HDF5 y se anexan sus filas. El pico de RAM es `max(RAM(1), RAM(2))`.

5. **Invariante de formato y procedencia (Esquema v2)**:
   El archivo fusionado se escribe bajo `SCHEMA_VERSION = 2` y contiene:
   - Partición `resultantes`: todas las señales concatenadas.
   - Partición `filtradas`: creada para todos los sensores pero con 0 filas para cumplir el
     invariante de que cada sensor tenga grupo en ambas particiones.
   - Por señal: `source_file_index` (0 o 1) y `source_timestamp` (timestamp original antes del desfase).
   - En atributos raíz: `merge_sources`, `merge_time_offsets_s`, `merge_signal_counts`,
     `merge_seam_overlap_s` y `experiment = "Fusión: exp1 + exp2"`.

## 2. Cambios realizados por módulo

- **`data/merge.py`** (nuevo): `merge_datasets(destino, ruta1, ruta2, partition, ...)` implementa
  la validación estructural previa (sensores idénticos, mismos `n_samples` y `fs_hz`), cálculo
  de `delta` y solape, e inserción secuencial.
- **`data/export.py`**: Helpers extraídos (`chunk_rows`, `write_sensor_group_attrs`,
  `write_environmental`, `write_events`), subida a `SCHEMA_VERSION = 2`.
- **`ui/state.py`**: `start_merge_status`, `finish_merge_status`, `merge_status`.
- **`ui/callbacks/helpers.py`**: `default_merge_filename`, `format_merge_starting_message`,
  `format_merge_done_message`, `format_merge_error_message`.
- **`ui/components/control_panel.py`**: Sección "Fusionar bases de datos" con selectores de archivos 1 y 2,
  etiquetas de rutas, botón de acción y mensaje de estado.
- **`ui/components/sensor_window.py`**: Almacenamiento en `dcc.Store` (`merge-path-1`, `merge-path-2`)
  e intervalo `merge-status-poll`.
- **`ui/callbacks/sensor_window_callbacks.py`**: Callbacks de selección de archivos y lanzamiento
  del hilo en segundo plano `_launch_merge_thread`.
- **`tests/test_merge.py`** (nuevo): Pruebas exhaustivas de orden cronológico, conservación de distancias,
  reapertura por particiones, solape ambiental, procedencia, validaciones de rechazo y callbacks GUI.
