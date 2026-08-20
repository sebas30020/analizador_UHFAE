"""Reader concreto para archivos `.h5` de osciloscopio Keysight en memoria segmentada
(rama ``lectura_keysight``, esquema documentado en ``archivos_md/esquema_keysight_h5.md``).

```
FileType/KeysightH5FileType   = b"Keysight Waveform"     <- firma de reconocimiento
Frame/TheFrame                 = (Model, Serial, Date)     <- Date sin zona horaria
Waveforms/                     attrs: NumWaveforms
    "<canal>"/                 attrs: NumSegments, NumPoints, XInc, YInc, YOrg,
                                       YDispRange, MaxBandwidth, ...
        "<canal> Seg<N>Data"    int16 (NumPoints,)   attrs: SegmentedTimeTag (relativo
                                                              al primer segmento)
```

No hay chunks, ambientales ni eventos en este formato -- cada dataset ``Seg<N>Data`` es
un disparo completo del osciloscopio en memoria segmentada, ya en voltios tras aplicar
la escala del canal (``v = raw * YInc + YOrg``).

Decisiones tomadas con el usuario (ver ``archivos_md/PLAN_LECTURA_KEYSIGHT.md`` §2):
``vrange = YDispRange / 2`` (media escala vertical configurada, no el fondo de escala
completo), un único sensor lógico ``UHF_KS``, primer canal en orden con aviso si hay
más de uno, sin nivel de disparo por señal (``trigger = 0.0``, y
``SensorConfig.has_trigger_metadata = False`` para que la UI no finja un valor medido).

Trata el archivo origen como estrictamente de solo lectura (se abre con ``mode="r"``).
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Iterator

import h5py
import numpy as np

from core.models import EnvironmentalSeries, EventSeries, SensorName, load_sensor_configs
from data.readers.base import OriginReader, RawSignalBatch, stable_file_dataset_id

_logger = logging.getLogger("analizador.data.keysight_reader")

_SENSOR: SensorName = "UHF_KS"  # único sensor lógico que este origen ofrece (§4.1 del plan)
_SEG_NAME_RE = re.compile(r"Seg(\d+)Data$")
_DATE_FORMAT = "%d-%b-%Y %H:%M:%S"  # p. ej. "19-Aug-2026 16:03:40" (Frame/TheFrame.Date)


def _decode(value: object) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _is_keysight_segmented_file(f: h5py.File) -> bool:
    """Sniffing de contenido (``data/readers/factory.py``): la firma vive en
    ``FileType/KeysightH5FileType``, no en la extensión del archivo -- ambos formatos
    usan indistintamente ``.h5``/``.hdf5``."""
    node = f.get("FileType/KeysightH5FileType")
    if node is None:
        return False
    try:
        return _decode(node[()]) == "Keysight Waveform"
    except (TypeError, OSError):
        return False


class KeysightSegmentedReader(OriginReader):
    """Lee archivos Keysight en memoria segmentada como un único sensor lógico
    ``UHF_KS`` (un archivo = un experimento, mismo supuesto que ``HDF5Reader``)."""

    def __init__(self, path: str | Path):
        self._path = Path(path)
        self._file: h5py.File | None = None

    def __enter__(self) -> "KeysightSegmentedReader":
        self._file = h5py.File(self._path, mode="r")
        return self

    def __exit__(self, *exc_info: object) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    @property
    def _f(self) -> h5py.File:
        if self._file is None:
            raise RuntimeError(
                "KeysightSegmentedReader debe usarse como context manager (with KeysightSegmentedReader(...) as r:)"
            )
        return self._file

    @property
    def dataset_id(self) -> str:
        return stable_file_dataset_id(self._path)

    def _frame_fields(self) -> tuple[str, str, str]:
        """``(model, serial, date_str)`` de ``Frame/TheFrame``, o cadenas vacías si el
        archivo no trae ese grupo (fixture mínimo de prueba)."""
        frame = self._f.get("Frame/TheFrame")
        if frame is None:
            return "", "", ""
        record = frame[()]
        return _decode(record["Model"]), _decode(record["Serial"]), _decode(record["Date"])

    def list_experiments(self) -> list[str]:
        model, _serial, date_str = self._frame_fields()
        label = " ".join(part for part in (model, date_str) if part)
        return [label or self._path.stem]

    def _channel_names(self) -> list[str]:
        waveforms = self._f.get("Waveforms")
        if waveforms is None:
            return []
        # Orden alfabético explícito (no el de inserción del archivo): determinista y
        # reproducible entre relecturas, independiente de cómo el instrumento escribió
        # los grupos (§4.1 del plan, decisión D4 "primer canal en orden").
        return sorted(waveforms.keys())

    def _selected_channel(self) -> str | None:
        names = self._channel_names()
        if not names:
            return None
        if len(names) > 1:
            _logger.warning(
                "Keysight: %s trae %d canales (%s); se usa el primero en orden ('%s')",
                self._path, len(names), ", ".join(names), names[0],
            )
        return names[0]

    def get_experiment_attrs(self, experiment: str) -> dict:
        model, serial, date_str = self._frame_fields()
        channel = self._selected_channel()
        attrs: dict = {
            "model": model,
            "serial": serial,
            "date": date_str,
            "channel": channel,
            "n_channels_available": len(self._channel_names()),
        }
        if channel is not None:
            ch_attrs = dict(self._f["Waveforms"][channel].attrs)
            attrs.update(
                {
                    "n_segments": int(ch_attrs.get("NumSegments", 0)),
                    "n_points": int(ch_attrs.get("NumPoints", 0)),
                    "fs_hz": 1.0 / float(ch_attrs["XInc"]) if "XInc" in ch_attrs else None,
                    "y_disp_range_v": float(ch_attrs.get("YDispRange", 0.0)),
                    "max_bandwidth_hz": float(ch_attrs.get("MaxBandwidth", 0.0)),
                }
            )
        return attrs

    def available_sensors(self) -> list[SensorName]:
        return [_SENSOR] if self._channel_names() else []

    def sensor_config_overrides(self, experiment: str, sensor: SensorName) -> dict:
        """``fs_hz``/``n_samples`` efectivos del archivo (§4.3 del plan): el YAML solo
        documenta el valor nominal, este método aporta lo que el instrumento realmente
        grabó, señal por señal via los atributos del canal seleccionado."""
        if sensor != _SENSOR:
            return {}
        channel = self._selected_channel()
        if channel is None:
            return {}
        ch_attrs = self._f["Waveforms"][channel].attrs
        if "XInc" not in ch_attrs or "NumPoints" not in ch_attrs:
            return {}
        return {"fs_hz": 1.0 / float(ch_attrs["XInc"]), "n_samples": int(ch_attrs["NumPoints"])}

    def _epoch_offset_s(self) -> float:
        """Instante UNIX del primer segmento (``SegmentedTimeTag=0``), a partir de
        ``Frame.Date`` interpretado como hora local (el formato no lleva zona horaria,
        §2 decisión D5 del plan). Si no hay ``Frame`` o el texto no parsea, se usa
        ``0.0`` -- los ``SegmentedTimeTag`` (siempre relativos y monótonos) siguen
        siendo válidos como timestamps, solo pierden el anclaje a un reloj real; ningún
        gráfico depende de ese anclaje (usan minutos transcurridos desde ``t0``), salvo
        la etiqueta de reloj del panel de metadatos."""
        _model, _serial, date_str = self._frame_fields()
        if not date_str:
            return 0.0
        try:
            return datetime.strptime(date_str, _DATE_FORMAT).timestamp()
        except ValueError:
            _logger.warning("Keysight: no se pudo interpretar Frame.Date=%r, uso epoch=0", date_str)
            return 0.0

    def iter_signal_batches(self, experiment: str, sensor: SensorName) -> Iterator[RawSignalBatch]:
        if sensor != _SENSOR:
            return
        channel = self._selected_channel()
        if channel is None:
            return

        ch_group = self._f["Waveforms"][channel]
        ch_attrs = ch_group.attrs
        y_inc = float(ch_attrs["YInc"])
        y_org = float(ch_attrs["YOrg"])
        y_disp_range = float(ch_attrs["YDispRange"])
        vrange_value = y_disp_range / 2.0  # decisión D2: media escala, no el fondo de escala completo
        epoch0 = self._epoch_offset_s()

        seg_names = sorted(
            (n for n in ch_group.keys() if _SEG_NAME_RE.search(n)),
            key=lambda n: int(_SEG_NAME_RE.search(n).group(1)),  # type: ignore[union-attr]
        )
        if not seg_names:
            return

        config_path = Path(__file__).resolve().parents[2] / "config" / "sensors.yaml"
        block_n = load_sensor_configs(config_path)[_SENSOR].block_n_signals

        for start in range(0, len(seg_names), block_n):
            batch_names = seg_names[start : start + block_n]
            n = len(batch_names)
            data = np.empty((n, int(ch_attrs["NumPoints"])), dtype=np.float32)
            time_tags = np.empty(n, dtype=np.float64)
            for row, name in enumerate(batch_names):
                seg = ch_group[name]
                data[row] = seg[:].astype(np.float64) * y_inc + y_org
                time_tags[row] = float(seg.attrs["SegmentedTimeTag"])

            yield RawSignalBatch(
                data=data,
                timestamps=epoch0 + time_tags,
                trigger=np.zeros(n, dtype=np.float64),  # decisión D6: no registrado en el formato
                vrange=np.full(n, vrange_value, dtype=np.float64),
            )

    def get_environmental(self, experiment: str) -> EnvironmentalSeries:
        empty = np.array([], dtype=np.float64)
        return EnvironmentalSeries(timestamps=empty, temperature=empty.copy(), humidity=empty.copy())

    def get_events(self, experiment: str) -> EventSeries:
        return EventSeries(timestamps=np.array([], dtype=np.float64), event_type=np.array([], dtype="<U16"))
