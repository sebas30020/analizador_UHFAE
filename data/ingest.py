"""Desempaquetado de chunks -> matriz global (FASE0_DISENO_Analizador_UHF_AE.md §2.2, Fase 1).

El concepto de "chunk" desaparece aquí: todo lo aguas arriba de este módulo (motor de
métricas, agrupamiento, UI) opera exclusivamente sobre la :class:`~core.models.SignalBlock`
resultante, ordenada cronológicamente de forma estricta.

Nota de escalado (riesgo de rendimiento documentado): esta implementación construye la
matriz global concatenada en RAM antes de persistirla vía ``data/storage.py``. Es una
decisión razonable bajo el supuesto de máquina de desarrollo con >= 16 GB de RAM
(FASE0_DISENO...md §10, supuesto #7) y datasets del orden de cientos de MB a ~1 GB por
sensor (tamaño real observado en ``med_5_ago_3.hdf5``). Si en el futuro los datasets
superan el presupuesto de RAM disponible, este módulo debería pasar a un modo de
escritura incremental por bloques directamente contra ``storage.py`` sin ensamblar el
array completo en memoria — no necesario para el alcance de la Fase 1.
"""
from __future__ import annotations

from pathlib import Path

import h5py
import numpy as np

from core.models import EnvironmentalSeries, EventSeries, IngestResult, NormalizationInfo, SensorName, SignalBlock
from core.normalization import NORMALIZATION_VERSION, compute_valid_mask
from data.readers.base import OriginReader
from data.storage import SCHEMA_VERSION, CanonicalStore, _chunk_rows, _write_environmental, _write_events
from utils.profiling import stage


def compute_minmax(data: np.ndarray) -> np.ndarray:
    """Par (min, max) por señal — se calcula una sola vez en ingesta y se persiste
    (PROMPT maestro §5.1: "nunca se recalcula en tiempo de render")."""
    if data.shape[0] == 0:
        return np.empty((0, 2), dtype=np.float32)
    return np.stack([data.min(axis=1), data.max(axis=1)], axis=1).astype(np.float32)


def ingest_sensor(
    reader: OriginReader,
    experiment: str,
    sensor: SensorName,
    *,
    canonical_store_path: str | Path | None = None,
    h5_group: h5py.Group | None = None,
) -> SignalBlock:
    """Concatena todos los lotes de ``sensor`` en ``experiment``, ordena por timestamp
    absoluto de forma estricta y calcula ``valid_mask``/``minmax``.

    Elimina la copia completa de la matriz por permutación (``data[order]``) y
    acumula ``compute_minmax`` por lote para no duplicar el pico de memoria RAM.
    Soporta escritura directa en streaming a HDF5 cuando se provee ``h5_group``
    o ``canonical_store_path``.
    """
    if canonical_store_path is not None and h5_group is None:
        c_path = Path(canonical_store_path)
        with h5py.File(c_path, mode="a") as f:
            grp_name = sensor
            if grp_name in f:
                del f[grp_name]
            grp = f.create_group(grp_name)
            ingest_sensor(reader, experiment, sensor, h5_group=grp)
        store = CanonicalStore(c_path)
        return store.create_lazy_block(sensor)

    if h5_group is not None:
        with stage("ingesta.sensor", sensor=sensor) as ctx:
            mm_parts: list[np.ndarray] = []
            ts_parts: list[np.ndarray] = []
            trig_parts: list[np.ndarray] = []
            vr_parts: list[np.ndarray] = []

            for batch in reader.iter_signal_batches(experiment, sensor):
                k = batch.data.shape[0]
                if k == 0:
                    continue
                m = batch.data.shape[1]
                if "data" not in h5_group:
                    r_chunk = _chunk_rows(k, m)
                    ds = h5_group.create_dataset(
                        "data", shape=(k, m), maxshape=(None, m), dtype=np.float32,
                        chunks=(r_chunk, m), compression="gzip", compression_opts=4,
                    )
                    ds[0:k, :] = batch.data
                else:
                    ds = h5_group["data"]
                    curr = ds.shape[0]
                    ds.resize((curr + k, m))
                    ds[curr : curr + k, :] = batch.data

                mm_parts.append(compute_minmax(batch.data))
                ts_parts.append(batch.timestamps)
                trig_parts.append(batch.trigger)
                vr_parts.append(batch.vrange)

            if not ts_parts:
                ctx["n_senales"] = 0
                if "data" not in h5_group:
                    h5_group.create_dataset("data", shape=(0, 0), dtype=np.float32)
                h5_group.create_dataset("timestamps", shape=(0,), dtype=np.float64)
                h5_group.create_dataset("trigger", shape=(0,), dtype=np.float64)
                h5_group.create_dataset("vrange", shape=(0,), dtype=np.float64)
                h5_group.create_dataset("valid_mask", shape=(0,), dtype=np.uint8)
                h5_group.create_dataset("minmax", shape=(0, 2), dtype=np.float32)
                h5_group.attrs["is_chronological"] = True
                return SignalBlock(
                    data=np.empty((0, 0), dtype=np.float32),
                    timestamps=np.array([], dtype=np.float64),
                    trigger=np.array([], dtype=np.float64),
                    vrange=np.array([], dtype=np.float64),
                    valid_mask=np.array([], dtype=bool),
                    minmax=np.empty((0, 2), dtype=np.float32),
                )

            timestamps = np.concatenate(ts_parts).astype(np.float64)
            trigger = np.concatenate(trig_parts).astype(np.float64)
            vrange = np.concatenate(vr_parts).astype(np.float64)
            minmax = np.concatenate(mm_parts, axis=0).astype(np.float32)
            n = timestamps.shape[0]
            ctx["n_senales"] = n

            is_chronological = bool(np.all(timestamps[:-1] <= timestamps[1:])) if n > 1 else True
            if is_chronological:
                h5_group.attrs["is_chronological"] = True
                valid_mask = compute_valid_mask(trigger, vrange)
            else:
                order = np.argsort(timestamps, kind="stable")
                timestamps = timestamps[order]
                trigger = trigger[order]
                vrange = vrange[order]
                minmax = minmax[order]
                valid_mask = compute_valid_mask(trigger, vrange)
                h5_group.create_dataset("order", data=order, dtype=np.int64)
                h5_group.attrs["is_chronological"] = False

            h5_group.create_dataset("timestamps", data=timestamps)
            h5_group.create_dataset("trigger", data=trigger)
            h5_group.create_dataset("vrange", data=vrange)
            h5_group.create_dataset("valid_mask", data=valid_mask.astype(np.uint8))
            h5_group.create_dataset("minmax", data=minmax, compression="gzip", compression_opts=4)

            data_ds = h5_group["data"]
            m = int(data_ds.shape[1]) if data_ds.ndim == 2 else 0
            order_ds = h5_group["order"] if "order" in h5_group else None

            def _read_rows(s: int, e: int) -> np.ndarray:
                if s >= e or s >= n:
                    return np.empty((0, m), dtype=np.float32)
                s = max(0, s)
                e = min(n, e)
                if order_ds is None:
                    return data_ds[s:e, :]
                phys = order_ds[s:e]
                if len(phys) == 1 or bool(np.all(np.diff(phys) > 0)):
                    return data_ds[phys, :]
                sort_p = np.argsort(phys)
                sorted_chunk = data_ds[phys[sort_p], :]
                inv_p = np.empty_like(sort_p)
                inv_p[sort_p] = np.arange(len(sort_p))
                return sorted_chunk[inv_p, :]

            def _read_row(idx: int) -> np.ndarray:
                phys_i = int(order_ds[idx]) if order_ds is not None else idx
                return data_ds[phys_i, :]

            return SignalBlock(
                data=None,
                timestamps=timestamps,
                trigger=trigger,
                vrange=vrange,
                valid_mask=valid_mask,
                minmax=minmax,
                row_source=_read_rows,
                single_row_source=_read_row,
                n_samples=m,
            )

    with stage("ingesta.sensor", sensor=sensor) as ctx:
        data_parts: list[np.ndarray] = []
        mm_parts = []
        ts_parts = []
        trigger_parts = []
        vrange_parts = []

        for batch in reader.iter_signal_batches(experiment, sensor):
            data_parts.append(batch.data)
            mm_parts.append(compute_minmax(batch.data))
            ts_parts.append(batch.timestamps)
            trigger_parts.append(batch.trigger)
            vrange_parts.append(batch.vrange)

        if not data_parts:
            ctx["n_senales"] = 0
            return SignalBlock(
                data=np.empty((0, 0), dtype=np.float32),
                timestamps=np.array([], dtype=np.float64),
                trigger=np.array([], dtype=np.float64),
                vrange=np.array([], dtype=np.float64),
                valid_mask=np.array([], dtype=bool),
                minmax=np.empty((0, 2), dtype=np.float32),
            )

        data = np.concatenate(data_parts, axis=0)
        if data.dtype != np.float32:
            data = data.astype(np.float32)
        del data_parts
        timestamps = np.concatenate(ts_parts).astype(np.float64)
        trigger = np.concatenate(trigger_parts).astype(np.float64)
        vrange = np.concatenate(vrange_parts).astype(np.float64)
        minmax = np.concatenate(mm_parts, axis=0).astype(np.float32)

        is_chronological = bool(np.all(timestamps[:-1] <= timestamps[1:])) if timestamps.shape[0] > 1 else True
        if is_chronological:
            valid_mask = compute_valid_mask(trigger, vrange)
            ctx["n_senales"] = data.shape[0]
            return SignalBlock(
                data=data,
                timestamps=timestamps,
                trigger=trigger,
                vrange=vrange,
                valid_mask=valid_mask,
                minmax=minmax,
            )

        order = np.argsort(timestamps, kind="stable")
        timestamps = timestamps[order]
        trigger = trigger[order]
        vrange = vrange[order]
        minmax = minmax[order]
        valid_mask = compute_valid_mask(trigger, vrange)
        ctx["n_senales"] = data.shape[0]

        return SignalBlock(
            data=data,
            timestamps=timestamps,
            trigger=trigger,
            vrange=vrange,
            valid_mask=valid_mask,
            minmax=minmax,
            order=order,
        )


def ingest_environmental(reader: OriginReader, experiment: str) -> EnvironmentalSeries:
    """Serie ambiental completa del experimento (delegada al reader, ya ordenada)."""
    return reader.get_environmental(experiment)


def ingest_events(reader: OriginReader, experiment: str) -> EventSeries:
    """Serie de eventos completa del experimento (delegada al reader, ya ordenada)."""
    return reader.get_events(experiment)


def ingest_experiment(
    reader: OriginReader,
    experiment: str,
    sensors: list[SensorName],
    *,
    canonical_store_path: str | Path | None = None,
) -> IngestResult:
    """Orquesta la ingesta completa de un experimento: todas las matrices de sensor,
    ambientales y eventos, listas para persistir con ``data/storage.py``."""
    with stage("ingesta.experimento", experimento=experiment) as ctx:
        if canonical_store_path is not None:
            c_path = Path(canonical_store_path)
            with h5py.File(c_path, mode="w") as f:
                f.attrs["dataset_id"] = reader.dataset_id
                f.attrs["experiment"] = experiment
                f.attrs["normalization_version"] = NORMALIZATION_VERSION
                f.attrs["schema_version"] = SCHEMA_VERSION

                for sensor in sensors:
                    grp = f.create_group(sensor)
                    ingest_sensor(reader, experiment, sensor, h5_group=grp)

                _write_environmental(f, ingest_environmental(reader, experiment))
                _write_events(f, ingest_events(reader, experiment))

            store = CanonicalStore(c_path)
            sensor_blocks = {sensor: store.create_lazy_block(sensor) for sensor in sensors}
            ctx["n_senales"] = sum(b.n_signals for b in sensor_blocks.values())
            return IngestResult(
                dataset_id=reader.dataset_id,
                experiment=experiment,
                sensors=sensor_blocks,
                environmental=store.get_environmental(),
                events=store.get_events(),
                normalization=NormalizationInfo(version=NORMALIZATION_VERSION),
            )

        sensor_blocks = {sensor: ingest_sensor(reader, experiment, sensor) for sensor in sensors}
        ctx["n_senales"] = sum(b.n_signals for b in sensor_blocks.values())
        return IngestResult(
            dataset_id=reader.dataset_id,
            experiment=experiment,
            sensors=sensor_blocks,
            environmental=ingest_environmental(reader, experiment),
            events=ingest_events(reader, experiment),
            normalization=NormalizationInfo(version=NORMALIZATION_VERSION),
        )
