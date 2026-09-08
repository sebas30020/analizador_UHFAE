"""Gráfica tipo #2 -- diezmado en vista completa (rama ``lectura_keysight``).

El literal ``sensor_config.name == "AE"`` que decidía cuándo diezmar se reemplazó por
``SensorConfig.decimate_full_view`` (config/sensors.yaml). Estas pruebas cuidan que ese
cambio no altere el comportamiento de UHF/AE (no-regresión) y que el sensor nuevo
``UHF_KS`` siga el mismo criterio que AE.
"""
from __future__ import annotations

import numpy as np

from core.models import SensorConfig, load_sensor_configs
from ui.components.graph_signal import build_signal_figure


def _config(
    name: str,
    n_samples: int,
    fs_hz: float,
    axis_scale: float,
    decimate_full_view: bool,
    full_resolution_span: float | None = None,
) -> SensorConfig:
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
        full_resolution_span=full_resolution_span,
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


def test_uhf_ks_real_file_full_resolution_span():
    # UHF_KS con la forma del archivo real (1 000 003 muestras, fs=5 GHz, 200 µs totales, umbral 2 µs)
    cfg = _config(
        "UHF_KS",
        n_samples=1_000_003,
        fs_hz=5.0e9,
        axis_scale=1.0e6,
        decimate_full_view=True,
        full_resolution_span=2.0,
    )
    sig = np.zeros(1_000_003, dtype=np.float32)

    # Vista completa (200 µs) -> diezmada
    _fig, is_dec = build_signal_figure(cfg, sig, n_pixels=1600)
    assert is_dec

    # Ventana de 1.5 µs (< 2 µs) -> NO diezmada (a pesar de tener 7500 muestras > 1600 píxeles)
    _fig, is_dec = build_signal_figure(cfg, sig, x_range_natural_units=(10.0, 11.5), n_pixels=1600)
    assert not is_dec

    # Ventana de 3.0 µs (> 2 µs) -> diezmada
    _fig, is_dec = build_signal_figure(cfg, sig, x_range_natural_units=(10.0, 13.0), n_pixels=1600)
    assert is_dec


def test_ae_with_full_resolution_span():
    cfg = _config(
        "AE",
        n_samples=10000,
        fs_hz=1.0e5,
        axis_scale=1.0e3,
        decimate_full_view=True,
        full_resolution_span=50.0,
    )
    sig = np.zeros(10000, dtype=np.float32)

    # Vista completa (100 ms) -> diezmada
    _fig, is_dec = build_signal_figure(cfg, sig, n_pixels=1600)
    assert is_dec

    # Zoom a 40 ms (<= 50 ms) -> NO diezmada (4000 muestras > 1600 píxeles)
    _fig, is_dec = build_signal_figure(cfg, sig, x_range_natural_units=(0.0, 40.0), n_pixels=1600)
    assert not is_dec

    # Zoom a 60 ms (> 50 ms) -> diezmada (6000 muestras > 1600 píxeles)
    _fig, is_dec = build_signal_figure(cfg, sig, x_range_natural_units=(0.0, 60.0), n_pixels=1600)
    assert is_dec


def test_decimation_guard_when_samples_below_pixels_even_if_span_exceeds():
    # Sensor con decimate_full_view=True y full_resolution_span pequeño, pero menos muestras que n_pixels:
    # no debe diezmar (is_decimated=False) para no mentir en el panel de metadatos.
    cfg = _config(
        "AE",
        n_samples=500,
        fs_hz=1.0e5,
        axis_scale=1.0e3,
        decimate_full_view=True,
        full_resolution_span=1.0,
    )
    sig = np.zeros(500, dtype=np.float32)

    # Vista completa (5 ms > 1.0 ms umbral, pero solo 500 muestras <= 1600 píxeles)
    _fig, is_dec = build_signal_figure(cfg, sig, n_pixels=1600)
    assert not is_dec


def test_load_sensor_configs_reads_full_resolution_span():
    configs = load_sensor_configs("config/sensors.yaml")
    assert configs["UHF_KS"].full_resolution_span == 2.0
    assert configs["AE"].full_resolution_span == 50.0
    assert configs["UHF"].full_resolution_span is None
