"""Gráfica tipo #2 -- diezmado en vista completa (rama ``lectura_keysight``).

El literal ``sensor_config.name == "AE"`` que decidía cuándo diezmar se reemplazó por
``SensorConfig.decimate_full_view`` (config/sensors.yaml). Estas pruebas cuidan que ese
cambio no altere el comportamiento de UHF/AE (no-regresión) y que el sensor nuevo
``UHF_KS`` siga el mismo criterio que AE.
"""
from __future__ import annotations

import numpy as np

from core.models import SensorConfig
from ui.components.graph_signal import build_signal_figure


def _config(name: str, n_samples: int, fs_hz: float, axis_scale: float, decimate_full_view: bool) -> SensorConfig:
    return SensorConfig(
        name=name,  # type: ignore[arg-type]
        hdf5_group="x",
        fs_hz=fs_hz,
        n_samples=n_samples,
        freq_limit_hz=1.0,
        axis_unit="µs",
        axis_scale=axis_scale,
        target_block_bytes=1,
        decimate_full_view=decimate_full_view,
    )


def test_uhf_never_decimates_full_view_even_with_many_samples():
    cfg = _config("UHF", n_samples=5000, fs_hz=3.0e9, axis_scale=1.0e6, decimate_full_view=False)
    _fig, is_decimated = build_signal_figure(cfg, np.zeros(5000, dtype=np.float32), n_pixels=1600)
    assert not is_decimated


def test_ae_decimates_full_view_above_pixel_budget():
    cfg = _config("AE", n_samples=10000, fs_hz=1.0e5, axis_scale=1.0e3, decimate_full_view=True)
    _fig, is_decimated = build_signal_figure(cfg, np.zeros(10000, dtype=np.float32), n_pixels=1600)
    assert is_decimated


def test_ae_does_not_decimate_when_zoomed_below_pixel_budget():
    cfg = _config("AE", n_samples=10000, fs_hz=1.0e5, axis_scale=1.0e3, decimate_full_view=True)
    # Ventana de 1000 muestras (10 de las 100 ms totales) -- ya cabe en los píxeles.
    _fig, is_decimated = build_signal_figure(
        cfg, np.zeros(10000, dtype=np.float32), x_range_natural_units=(0.0, 10.0), n_pixels=1600
    )
    assert not is_decimated


def test_uhf_ks_decimates_full_view_like_ae():
    cfg = _config("UHF_KS", n_samples=20000, fs_hz=2.0e10, axis_scale=1.0e6, decimate_full_view=True)
    _fig, is_decimated = build_signal_figure(cfg, np.zeros(20000, dtype=np.float32), n_pixels=1600)
    assert is_decimated
