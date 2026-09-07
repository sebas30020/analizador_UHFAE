"""Fusión de dos bases de datos (tests de adquisición) en un archivo HDF5 único.

Diseño (ver `plan` y `archivos_md/FUSION_BASES_DATOS_ENTREGA.md`):
- Capa de datos pura: sin Dash, sin caché, sin lock de ``AppState``.
- Formato de salida: mismo esquema que ``data/export.py``
  (``file_type = "AnalizadorUHFAE/FilteredExport"``, ``SCHEMA_VERSION = 2``), con todas
  las señales en la partición ``resultantes`` y ``filtradas`` creada vacía para preservar
  el invariante del formato.
- El desfase temporal se calcula una sola vez y se aplica a todo el archivo 2
  (señales, ambientales y eventos). El cálculo de ``delta`` se basa exclusivamente en las
  señales:
    t_fin_1 = max(timestamps de todas las señales del archivo 1)
    t_ini_2 = min(timestamps de todas las señales del archivo 2)
    delta   = (t_fin_1 - t_ini_2) + separacion
  donde ``separacion`` es la mediana de diferencias entre timestamps consecutivos del sensor
  con más señales del archivo 1 (o 0.0).
- Memoria acotada: escritura secuencial crear-y-anexar. Se procesa el archivo 1, se escribe
  y se libera; luego se procesa el archivo 2, se redimensionan los datasets HDF5 y se
  escribe a continuación. El pico de RAM es max(RAM(1), RAM(2)), no la suma.
- Procedencia: campos ``source_file_index`` y ``source_timestamp`` por señal, y metadatos
  raíz ``merge_sources``, ``merge_time_offsets_s``, ``merge_signal_counts``,
  ``merge_seam_overlap_s`` y ``experiment = "Fusión: exp1 + exp2"``.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

import h5py
import numpy as np

from core.models import EnvironmentalSeries, EventSeries, SensorName, load_sensor_configs
from data.export import (
    FILE_TYPE_ATTR,
    FILTRADAS_GROUP,
    RESULTANTES_GROUP,
    SCHEMA_VERSION,
    _GZIP_LEVEL,
    ExportPartition,
    chunk_rows,
    write_environmental,
    write_events,
    write_sensor_group_attrs,
)
from data.ingest import ingest_experiment
from data.readers.factory import open_reader
from utils.profiling import stage


@dataclass(frozen=True)
class MergeResult:
    """Resultado de la fusión de dos bases de datos."""

    destination: Path
    n_signals_total: int
    sensor_counts: dict[SensorName, int]
    delta_s: float
    seam_overlap_s: float
    time_offsets_s: list[float]


def merge_datasets(
    destination: str | Path,
    source1_path: str | Path,
    source2_path: str | Path,
    partition: ExportPartition = "resultantes",
    sensors_config_path: str | Path = "config/sensors.yaml",
) -> MergeResult:
    """Fusiona dos archivos de origen en un único archivo HDF5 compatible con
    ``FilteredExportReader``.

    Valida antes de escribir que ambos orígenes existan, sean distintos, contengan el mismo
    conjunto de sensores y que los parámetros fundamentales (``n_samples``, ``fs_hz``)
    coincidan.
    """
    path1 = Path(source1_path).resolve()
    path2 = Path(source2_path).resolve()

    if path1 == path2:
        raise ValueError("Las dos rutas de origen deben ser distintas.")

    if not path1.exists():
        raise FileNotFoundError(f"No existe el archivo de origen 1: {source1_path}")
    if not path2.exists():
        raise FileNotFoundError(f"No existe el archivo de origen 2: {source2_path}")

    # Validaciones estructurales previas
    with open_reader(path1, partition=partition) as r1, open_reader(path2, partition=partition) as r2:
        s1 = r1.available_sensors()
        s2 = r2.available_sensors()
        if not s1 or not s2:
            raise ValueError("Uno o ambos archivos no contienen sensores disponibles en la partición seleccionada.")
        if set(s1) != set(s2):
            raise ValueError(
                f"Los conjuntos de sensores no coinciden: archivo 1 tiene {s1}, archivo 2 tiene {s2}."
            )
        exp1 = r1.list_experiments()[0]
        exp2 = r2.list_experiments()[0]
        for sensor in s1:
            c1 = r1.sensor_config_overrides(exp1, sensor)
            c2 = r2.sensor_config_overrides(exp2, sensor)
            if c1.get("n_samples") != c2.get("n_samples"):
                raise ValueError(
                    f"Sensor {sensor}: n_samples no coincide ({c1.get('n_samples')} vs {c2.get('n_samples')})."
                )
            if c1.get("fs_hz") != c2.get("fs_hz"):
                raise ValueError(
                    f"Sensor {sensor}: fs_hz no coincide ({c1.get('fs_hz')} vs {c2.get('fs_hz')})."
                )

    base_configs = load_sensor_configs(sensors_config_path)

    # 1. Ingesta y escritura secuencial del Archivo 1
    with stage("merge.leer_origen_1", origen=str(path1)):
        with open_reader(path1, partition=partition) as r1:
            exp1_name = r1.list_experiments()[0]
            sensors = r1.available_sensors()
            ingest1 = ingest_experiment(r1, exp1_name, sensors)
            source1_dataset_id = r1.dataset_id
            c1_overrides = {s: r1.sensor_config_overrides(exp1_name, s) for s in sensors}

    n_signals_1 = sum(b.n_signals for b in ingest1.sensors.values())
    if n_signals_1 == 0:
        raise ValueError("El archivo 1 no contiene señales en la partición seleccionada.")

    signal_ts_max_1 = max(
        b.timestamps.max() for b in ingest1.sensors.values() if b.timestamps.shape[0] > 0
    )

    # Cadencia de costura: mediana de diferencias del sensor con más señales
    max_sensor_1 = max(sensors, key=lambda s: ingest1.sensors[s].timestamps.shape[0])
    ts_max_sensor_1 = ingest1.sensors[max_sensor_1].timestamps
    if ts_max_sensor_1.shape[0] >= 2:
        separacion = float(np.median(np.diff(ts_max_sensor_1)))
    else:
        separacion = 0.0

    env1 = ingest1.environmental
    events1 = ingest1.events
    exp1_title = ingest1.experiment
    norm_version = ingest1.normalization.version
    sensor_counts_1 = {s: ingest1.sensors[s].n_signals for s in sensors}

    dest_path = Path(destination)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    with stage("merge.escribir_tramo_1", destino=str(dest_path)):
        with h5py.File(dest_path, mode="w") as f:
            f.attrs["file_type"] = FILE_TYPE_ATTR
            f.attrs["schema_version"] = SCHEMA_VERSION
            f.attrs["normalization_version"] = norm_version
            f.attrs["exported_at"] = datetime.now(timezone.utc).isoformat()

            res_root = f.create_group(RESULTANTES_GROUP)
            filt_root = f.create_group(FILTRADAS_GROUP)

            for sensor in sensors:
                cfg = replace(base_configs[sensor], **c1_overrides[sensor]) if c1_overrides[sensor] else base_configs[sensor]

                # filtradas/<sensor>: grupo con atributos y datasets vacíos de 0 filas
                f_grp = filt_root.create_group(sensor)
                write_sensor_group_attrs(f_grp, cfg)
                f_m = cfg.n_samples
                f_grp.create_dataset("data", shape=(0, f_m), maxshape=(None, f_m), dtype=np.float32)
                f_grp.create_dataset("timestamps", shape=(0,), maxshape=(None,), dtype=np.float64)
                f_grp.create_dataset("trigger", shape=(0,), maxshape=(None,), dtype=np.float64)
                f_grp.create_dataset("vrange", shape=(0,), maxshape=(None,), dtype=np.float64)
                f_grp.create_dataset("valid_mask", shape=(0,), maxshape=(None,), dtype=np.uint8)
                f_grp.create_dataset("minmax", shape=(0, 2), maxshape=(None, 2), dtype=np.float32)
                f_grp.create_dataset("source_index", shape=(0,), maxshape=(None,), dtype=np.int64)
                f_grp.create_dataset("source_file_index", shape=(0,), maxshape=(None,), dtype=np.uint8)
                f_grp.create_dataset("source_timestamp", shape=(0,), maxshape=(None,), dtype=np.float64)

                # resultantes/<sensor>: datasets con maxshape=(None, ...)
                r_grp = res_root.create_group(sensor)
                write_sensor_group_attrs(r_grp, cfg)

                block1 = ingest1.sensors[sensor]
                n1 = block1.n_signals
                m = block1.n_samples if n1 > 0 else cfg.n_samples
                row_chunk = chunk_rows(n1, m)

                data_ds = r_grp.create_dataset(
                    "data",
                    shape=(n1, m),
                    maxshape=(None, m),
                    dtype=np.float32,
                    chunks=(row_chunk, m) if n1 > 0 else None,
                    compression="gzip",
                    compression_opts=_GZIP_LEVEL,
                )
                block_n_signals = cfg.block_n_signals
                for start in range(0, n1, block_n_signals):
                    stop = min(start + block_n_signals, n1)
                    data_ds[start:stop, :] = block1.rows(start, stop)

                r_grp.create_dataset("timestamps", data=block1.timestamps, maxshape=(None,))
                r_grp.create_dataset("trigger", data=block1.trigger, maxshape=(None,))
                r_grp.create_dataset("vrange", data=block1.vrange, maxshape=(None,))
                r_grp.create_dataset("valid_mask", data=block1.valid_mask.astype(np.uint8), maxshape=(None,))
                r_grp.create_dataset(
                    "minmax",
                    data=block1.minmax,
                    maxshape=(None, 2),
                    compression="gzip",
                    compression_opts=_GZIP_LEVEL,
                )
                r_grp.create_dataset("source_index", data=np.arange(n1, dtype=np.int64), maxshape=(None,))
                r_grp.create_dataset("source_file_index", data=np.zeros(n1, dtype=np.uint8), maxshape=(None,))
                r_grp.create_dataset("source_timestamp", data=block1.timestamps.copy(), maxshape=(None,))

    # Liberar memoria del bloque 1 antes de ingerir el bloque 2
    del ingest1

    # 2. Ingesta y anexado secuencial del Archivo 2
    with stage("merge.leer_origen_2", origen=str(path2)):
        with open_reader(path2, partition=partition) as r2:
            exp2_name = r2.list_experiments()[0]
            ingest2 = ingest_experiment(r2, exp2_name, sensors)
            source2_dataset_id = r2.dataset_id

    n_signals_2 = sum(b.n_signals for b in ingest2.sensors.values())
    if n_signals_2 == 0:
        raise ValueError("El archivo 2 no contiene señales en la partición seleccionada.")

    signal_ts_min_2 = min(
        b.timestamps.min() for b in ingest2.sensors.values() if b.timestamps.shape[0] > 0
    )

    delta = float((signal_ts_max_1 - signal_ts_min_2) + separacion)

    env2 = ingest2.environmental
    events2 = ingest2.events

    # Solape ambiental en costura (initial_dead_time del archivo 2)
    if env2.timestamps.shape[0] > 0:
        t_env2_min_shifted = float(env2.timestamps.min() + delta)
        if t_env2_min_shifted < signal_ts_max_1:
            seam_overlap_s = float(signal_ts_max_1 - t_env2_min_shifted)
        else:
            seam_overlap_s = 0.0
    else:
        seam_overlap_s = 0.0

    sensor_counts_2 = {s: ingest2.sensors[s].n_signals for s in sensors}
    sensor_counts_total = {s: sensor_counts_1[s] + sensor_counts_2[s] for s in sensors}
    n_total_all_sensors = sum(sensor_counts_total.values())

    with stage("merge.escribir_tramo_2", destino=str(dest_path)):
        with h5py.File(dest_path, mode="a") as f:
            res_root = f[RESULTANTES_GROUP]
            for sensor in sensors:
                r_grp = res_root[sensor]
                block2 = ingest2.sensors[sensor]
                n1 = sensor_counts_1[sensor]
                n2 = block2.n_signals
                n_tot = n1 + n2

                data_ds = r_grp["data"]
                m = data_ds.shape[1]
                data_ds.resize((n_tot, m))

                block_n_signals = base_configs[sensor].block_n_signals
                for start in range(0, n2, block_n_signals):
                    stop = min(start + block_n_signals, n2)
                    data_ds[n1 + start : n1 + stop, :] = block2.rows(start, stop)

                ts_ds = r_grp["timestamps"]
                ts_ds.resize((n_tot,))
                ts_ds[n1:n_tot] = block2.timestamps + delta

                trig_ds = r_grp["trigger"]
                trig_ds.resize((n_tot,))
                trig_ds[n1:n_tot] = block2.trigger

                vr_ds = r_grp["vrange"]
                vr_ds.resize((n_tot,))
                vr_ds[n1:n_tot] = block2.vrange

                vm_ds = r_grp["valid_mask"]
                vm_ds.resize((n_tot,))
                vm_ds[n1:n_tot] = block2.valid_mask.astype(np.uint8)

                mm_ds = r_grp["minmax"]
                mm_ds.resize((n_tot, 2))
                mm_ds[n1:n_tot, :] = block2.minmax

                si_ds = r_grp["source_index"]
                si_ds.resize((n_tot,))
                si_ds[n1:n_tot] = np.arange(n2, dtype=np.int64)

                sfi_ds = r_grp["source_file_index"]
                sfi_ds.resize((n_tot,))
                sfi_ds[n1:n_tot] = np.ones(n2, dtype=np.uint8)

                st_ds = r_grp["source_timestamp"]
                st_ds.resize((n_tot,))
                st_ds[n1:n_tot] = block2.timestamps

            # Concatenar y ordenar series ambientales
            env2_ts_shifted = env2.timestamps + delta if env2.timestamps.shape[0] > 0 else env2.timestamps
            merged_env_ts = np.concatenate([env1.timestamps, env2_ts_shifted])
            merged_temp = np.concatenate([env1.temperature, env2.temperature])
            merged_hum = np.concatenate([env1.humidity, env2.humidity])
            if merged_env_ts.shape[0] > 0:
                ord_env = np.argsort(merged_env_ts, kind="stable")
                merged_env_ts = merged_env_ts[ord_env]
                merged_temp = merged_temp[ord_env]
                merged_hum = merged_hum[ord_env]
            merged_env = EnvironmentalSeries(timestamps=merged_env_ts, temperature=merged_temp, humidity=merged_hum)
            write_environmental(f, merged_env)

            # Concatenar y ordenar series de eventos
            events2_ts_shifted = (
                events2.timestamps + delta if events2.timestamps.shape[0] > 0 else events2.timestamps
            )
            merged_ev_ts = np.concatenate([events1.timestamps, events2_ts_shifted])
            merged_ev_types = np.concatenate([events1.event_type, events2.event_type])
            if merged_ev_ts.shape[0] > 0:
                ord_ev = np.argsort(merged_ev_ts, kind="stable")
                merged_ev_ts = merged_ev_ts[ord_ev]
                merged_ev_types = merged_ev_types[ord_ev]
            merged_events = EventSeries(timestamps=merged_ev_ts, event_type=merged_ev_types)
            write_events(f, merged_events)

            # Atributos raíz de procedencia y trazabilidad
            f.attrs["source_dataset_id"] = f"{source1_dataset_id}+{source2_dataset_id}"
            f.attrs["experiment"] = f"Fusión: {exp1_title} + {ingest2.experiment}"
            f.attrs["merge_sources"] = [str(path1), str(path2)]
            f.attrs["merge_time_offsets_s"] = [0.0, float(delta)]
            f.attrs["merge_signal_counts"] = [
                int(sum(sensor_counts_1.values())),
                int(sum(sensor_counts_2.values())),
            ]
            f.attrs["merge_seam_overlap_s"] = float(seam_overlap_s)

    del ingest2

    return MergeResult(
        destination=dest_path,
        n_signals_total=n_total_all_sensors,
        sensor_counts=sensor_counts_total,
        delta_s=delta,
        seam_overlap_s=seam_overlap_s,
        time_offsets_s=[0.0, delta],
    )
