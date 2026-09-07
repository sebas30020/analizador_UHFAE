"""Generador de datasets sintéticos masivos en formato canónico HDF5 (R0 / Etapa 0).

Permite generar datasets sintéticos de 50k, 250k y 1M+ señales para UHF y AE
respetando el esquema canónico definido en ``data/storage.py`` y verificado por
:class:`~data.storage.CanonicalStore`.

Invariante Crítico de Memoria:
La generación se realiza estrictamente en bloques de tamaño acotado (chunks de
5 000 a 10 000 señales) escribiendo directamente a los datasets HDF5 preasignados,
garantizando una huella de memoria RAM constante (~60-200 MB) y evitando cualquier
posibilidad de :class:`MemoryError` (incluso para 1M de señales AE, cuya matriz
descomprimida requeriría 40 GB contiguos en RAM).
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import h5py
import numpy as np

from core.models import (
    EnvironmentalSeries,
    EventSeries,
    SensorConfig,
    SensorName,
    load_sensor_configs,
)
from data.storage import SCHEMA_VERSION, _chunk_rows, _GZIP_LEVEL

DEFAULT_CHUNK_SIZE = 5000
BENCHMARK_SIZES: tuple[int, ...] = (50_000, 250_000, 1_000_000)
CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "sensors.yaml"


def _generate_sensor_chunk(
    sensor: SensorName,
    config: SensorConfig,
    start_idx: int,
    chunk_n: int,
    total_signals: int,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Genera un lote acotado de señales físicamente plausibles para un sensor.

    Retorna una tupla: (data, timestamps, trigger, vrange, valid_mask, minmax).
    """
    m = config.n_samples
    t_signal = np.arange(m, dtype=np.float32) * float(config.dt_s)
    t_signal = t_signal.reshape(1, m)

    # Cadencia de repetición promedio de pulsos (ej. 10 ms = 100 pulsos/s)
    dt_pulse_s = 0.01
    base_ts = np.arange(start_idx, start_idx + chunk_n, dtype=np.float64) * dt_pulse_s
    jitter = rng.uniform(-0.001, 0.001, chunk_n).astype(np.float64)
    timestamps = np.maximum(0.0, base_ts + jitter)

    # Rangos de tensión y niveles de disparo
    vrange_val = 0.5 if sensor == "UHF" else 1.0
    vrange = np.full(chunk_n, vrange_val, dtype=np.float64)
    trigger_val = 0.02 if sensor == "UHF" else 0.05
    trigger = np.full(chunk_n, trigger_val, dtype=np.float64)

    # Máscara de validez: 99.8% válidas, raras señales saturadas o anómalas
    valid_mask = (rng.uniform(0.0, 1.0, chunk_n) >= 0.002).astype(np.uint8)

    # Fórmulas físicas vectorizadas por sensor:
    if sensor in ("UHF", "UHF_KS"):
        # Descarga parcial UHF: pulso gaussiano ultracorto con oscilación amortiguada
        t_center = rng.uniform(0.2 * config.duration_s, 0.4 * config.duration_s, (chunk_n, 1)).astype(np.float32)
        sigma = rng.uniform(1.5e-8, 3.5e-8, (chunk_n, 1)).astype(np.float32)
        f_res = rng.uniform(2.0e8, 5.0e8, (chunk_n, 1)).astype(np.float32)
        amp = rng.uniform(0.05, 0.40, (chunk_n, 1)).astype(np.float32)

        dt = t_signal - t_center
        envelope = np.exp(-0.5 * (dt / sigma) ** 2)
        carrier = np.sin(2.0 * np.pi * f_res * dt)
        noise = rng.normal(0.0, 0.004, (chunk_n, m)).astype(np.float32)
        data = (amp * envelope * carrier + noise).astype(np.float32)

    elif sensor == "AE":
        # Emisión acústica: tiempo de subida rápido seguido de decaimiento exponencial resonante
        t_start = rng.uniform(0.1 * config.duration_s, 0.25 * config.duration_s, (chunk_n, 1)).astype(np.float32)
        tau_decay = rng.uniform(0.002, 0.006, (chunk_n, 1)).astype(np.float32)
        f_acoustic = rng.uniform(2.5e4, 4.5e4, (chunk_n, 1)).astype(np.float32)
        amp = rng.uniform(0.08, 0.45, (chunk_n, 1)).astype(np.float32)

        dt = t_signal - t_start
        heaviside = (dt >= 0.0).astype(np.float32)
        envelope = heaviside * np.exp(-dt / np.maximum(tau_decay, 1e-6))
        carrier = np.sin(2.0 * np.pi * f_acoustic * dt)
        noise = rng.normal(0.0, 0.003, (chunk_n, m)).astype(np.float32)
        data = (amp * envelope * carrier + noise).astype(np.float32)

    else:
        # Fallback genérico senoidal amortiguado
        dt = t_signal - (0.3 * config.duration_s)
        data = (0.2 * np.exp(-dt / 0.01) * np.sin(2.0 * np.pi * 1000.0 * dt)).astype(np.float32)

    # Min/max vectorizado por fila
    minmax = np.column_stack([data.min(axis=1), data.max(axis=1)]).astype(np.float32)

    return data, timestamps, trigger, vrange, valid_mask, minmax


def _generate_environmental_series(duration_s: float, rng: np.random.Generator) -> EnvironmentalSeries:
    """Genera serie ambiental sintética (temperatura, humedad)."""
    # 1 muestra ambiental cada 5 segundos
    cadence_s = 5.0
    k = max(10, int(math.ceil(duration_s / cadence_s)))
    timestamps = np.arange(k, dtype=np.float64) * cadence_s
    # Temperatura ambiente ~22-26 °C con ruido térmico suave
    t_base = 24.0 + 2.0 * np.sin(2.0 * np.pi * timestamps / max(duration_s, 3600.0))
    temperature = (t_base + rng.normal(0.0, 0.2, k)).astype(np.float64)
    # Humedad relativa ~45-55 %
    h_base = 50.0 - 5.0 * np.sin(2.0 * np.pi * timestamps / max(duration_s, 3600.0))
    humidity = (h_base + rng.normal(0.0, 0.5, k)).astype(np.float64)
    return EnvironmentalSeries(timestamps=timestamps, temperature=temperature, humidity=humidity)


def _generate_events_series(duration_s: float, rng: np.random.Generator) -> EventSeries:
    """Genera marcas de evento sintéticas ("CALIBRACION", "TEST", "SHOT")."""
    n_events = max(3, int(duration_s / 60.0))
    timestamps = np.sort(rng.uniform(1.0, max(duration_s - 1.0, 2.0), n_events)).astype(np.float64)
    event_names = np.array(["CALIBRACION", "REGIMEN_ALTO", "TRANSITORIO"], dtype=object)
    event_types = rng.choice(event_names, size=n_events).astype(object)
    return EventSeries(timestamps=timestamps, event_type=event_types)


def generate_synthetic_dataset(
    output_path: str | Path,
    n_signals: int,
    sensors: SensorName | Sequence[SensorName] = "UHF",
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    seed: int = 42,
    dataset_id: str | None = None,
    experiment: str = "Synthetic_PD_Benchmark",
    config_path: str | Path | None = None,
) -> Path:
    """Genera un archivo HDF5 canónico con ``n_signals`` señales por sensor.

    La escritura se realiza en streaming chunk a chunk directamente sobre HDF5,
    manteniendo la memoria RAM en $O(\\text{chunk\\_size})$ sin importar si
    ``n_signals`` es 50k, 250k o 1M.

    Parameters
    ----------
    output_path : str | Path
        Ruta destino del archivo ``.hdf5``. Se sobrescribe si ya existe.
    n_signals : int
        Número total de señales a generar por sensor.
    sensors : SensorName | Sequence[SensorName]
        Sensor o lista de sensores a incluir ("UHF", "AE", o tupla).
    chunk_size : int
        Tamaño de bloque en memoria (default: 5 000 señales).
    seed : int
        Semilla para reproducibilidad estricta.
    dataset_id : str | None
        Identificador del dataset canónico.
    experiment : str
        Nombre del experimento sintético.
    config_path : str | Path | None
        Ruta a ``config/sensors.yaml`` (opcional).

    Returns
    -------
    Path
        Ruta canónica del archivo HDF5 generado.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    sensor_list: list[SensorName] = [sensors] if isinstance(sensors, str) else list(sensors)
    cfg_file = Path(config_path) if config_path else CONFIG_PATH
    sensor_configs = load_sensor_configs(cfg_file)

    rng = np.random.default_rng(seed)
    ds_id = dataset_id or f"synthetic:{experiment}:{n_signals}:{seed}"

    # Duración total estimada del experimento sintético (dt = 10 ms por señal)
    duration_s = max(n_signals * 0.01, 10.0)

    with h5py.File(output_path, mode="w") as f:
        # Atributos raíz canónicos (idénticos a data/storage.py)
        f.attrs["dataset_id"] = ds_id
        f.attrs["experiment"] = experiment
        f.attrs["normalization_version"] = "v1_divide_by_vrange"
        f.attrs["schema_version"] = SCHEMA_VERSION

        for sensor in sensor_list:
            if sensor not in sensor_configs:
                raise ValueError(f"Sensor desconocido: {sensor}. Disponibles: {list(sensor_configs.keys())}")

            config = sensor_configs[sensor]
            m = config.n_samples
            row_chunk = _chunk_rows(n_signals, m)

            grp = f.create_group(sensor)
            grp.attrs["fs_hz"] = config.fs_hz
            grp.attrs["n_samples"] = config.n_samples
            grp.attrs["freq_limit_hz"] = config.freq_limit_hz
            grp.attrs["dt_s"] = config.dt_s

            # Pre-asignar datasets HDF5 chunked y comprimidos
            ds_data = grp.create_dataset(
                "data",
                shape=(n_signals, m),
                dtype="float32",
                chunks=(row_chunk, m) if n_signals > 0 else None,
                compression="gzip",
                compression_opts=_GZIP_LEVEL,
            )
            ds_ts = grp.create_dataset("timestamps", shape=(n_signals,), dtype="float64")
            ds_trig = grp.create_dataset("trigger", shape=(n_signals,), dtype="float64")
            ds_vr = grp.create_dataset("vrange", shape=(n_signals,), dtype="float64")
            ds_mask = grp.create_dataset("valid_mask", shape=(n_signals,), dtype="uint8")
            ds_mm = grp.create_dataset(
                "minmax",
                shape=(n_signals, 2),
                dtype="float32",
                compression="gzip",
                compression_opts=_GZIP_LEVEL,
            )

            # Llenado iterativo por chunks sin exceder el presupuesto de RAM
            for start in range(0, n_signals, chunk_size):
                stop = min(start + chunk_size, n_signals)
                k = stop - start
                data_k, ts_k, trig_k, vr_k, mask_k, mm_k = _generate_sensor_chunk(
                    sensor, config, start, k, n_signals, rng
                )
                ds_data[start:stop, :] = data_k
                ds_ts[start:stop] = ts_k
                ds_trig[start:stop] = trig_k
                ds_vr[start:stop] = vr_k
                ds_mask[start:stop] = mask_k
                ds_mm[start:stop, :] = mm_k

        # Series ambientales y eventos
        env = _generate_environmental_series(duration_s, rng)
        grp_env = f.create_group("environmental")
        grp_env.create_dataset("timestamps", data=env.timestamps)
        grp_env.create_dataset("temperature", data=env.temperature)
        grp_env.create_dataset("humidity", data=env.humidity)

        ev = _generate_events_series(duration_s, rng)
        grp_ev = f.create_group("events")
        grp_ev.create_dataset("timestamps", data=ev.timestamps)
        dt_vlen = h5py.special_dtype(vlen=str)
        grp_ev.create_dataset("type", data=ev.event_type.astype(object), dtype=dt_vlen)

    return output_path


def generate_benchmark_suite(
    output_dir: str | Path,
    sizes: tuple[int, ...] = BENCHMARK_SIZES,
    sensors: tuple[SensorName, ...] = ("UHF", "AE"),
    *,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    seed: int = 42,
) -> dict[str, Path]:
    """Genera la suite canónica de datasets sintéticos para evaluación de rendimiento.

    Genera archivos para cada tamaño (50k, 250k, 1M) y combinaciones de sensores.
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    generated: dict[str, Path] = {}

    for size in sizes:
        label = f"{size // 1000}k" if size < 1_000_000 else f"{size // 1_000_000}M"
        for sensor in sensors:
            key = f"synthetic_{sensor}_{label}"
            file_path = out_dir / f"{key}.hdf5"
            generate_synthetic_dataset(
                output_path=file_path,
                n_signals=size,
                sensors=sensor,
                chunk_size=chunk_size,
                seed=seed,
                experiment=f"Benchmark_{sensor}_{label}",
            )
            generated[key] = file_path

    return generated


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generador de datasets sintéticos canónicos HDF5 en streaming (R0 / Etapa 0)"
    )
    parser.add_argument(
        "--signals",
        type=int,
        default=50_000,
        help="Número de señales a generar por sensor (default: 50 000). Opciones típicas: 50000, 250000, 1000000",
    )
    parser.add_argument(
        "--sensor",
        choices=["UHF", "AE", "ALL"],
        default="UHF",
        help="Sensor a simular (default: UHF)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("data_synthetic"),
        help="Directorio de salida para los archivos .hdf5 generados (default: data_synthetic/)",
    )
    parser.add_argument(
        "--output-path",
        type=Path,
        default=None,
        help="Ruta específica del archivo .hdf5 de salida (invalida el nombre generado por omisión)",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=DEFAULT_CHUNK_SIZE,
        help=f"Tamaño de chunk en RAM para escritura streaming (default: {DEFAULT_CHUNK_SIZE})",
    )
    parser.add_argument(
        "--suite",
        action="store_true",
        help="Genera la suite completa de benchmarks (50k, 250k y 1M señales)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Semilla del generador pseudoaleatorio (default: 42)",
    )

    args = parser.parse_args()

    if args.suite:
        target_sensors: tuple[SensorName, ...] = ("UHF", "AE") if args.sensor == "ALL" else (args.sensor,)
        print(f"Generando suite completa {BENCHMARK_SIZES} señales para sensores {target_sensors} en {args.output_dir}...")
        suite = generate_benchmark_suite(
            output_dir=args.output_dir,
            sizes=BENCHMARK_SIZES,
            sensors=target_sensors,
            chunk_size=args.chunk_size,
            seed=args.seed,
        )
        for key, p in suite.items():
            size_mb = p.stat().st_size / (1024 * 1024)
            print(f"  - {key}: {p} ({size_mb:.1f} MB)")
        print("Suite sintética completada con éxito.")
        return

    target_sensor_list: tuple[SensorName, ...] = ("UHF", "AE") if args.sensor == "ALL" else (args.sensor,)
    label = f"{args.signals // 1000}k" if args.signals < 1_000_000 else f"{args.signals // 1_000_000}M"
    default_filename = f"synthetic_{args.sensor}_{label}.hdf5"
    out_file = args.output_path or (args.output_dir / default_filename)

    print(f"Generando dataset sintético ({args.signals} señales, sensor={args.sensor}) en {out_file}...")
    generate_synthetic_dataset(
        output_path=out_file,
        n_signals=args.signals,
        sensors=target_sensor_list,
        chunk_size=args.chunk_size,
        seed=args.seed,
    )
    size_mb = out_file.stat().st_size / (1024 * 1024)
    print(f"Archivo generado con éxito: {out_file} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
