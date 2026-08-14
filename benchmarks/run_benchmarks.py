"""Benchmarks de los objetivos medibles del PROMPT maestro §9.2, **separados UHF/AE**.

    python -m benchmarks.run_benchmarks --dataset "D:\\data\\data\\main\\med_5_ago_3.hdf5"

Mide, contra un archivo `.hdf5` real:

1. Ingesta y construcción de la matriz global -> throughput (señales/s). Sin objetivo.
2. Render inicial de la gráfica tipo #1 -> objetivo < 2 s.
3. Cálculo de una métrica puntual sobre el dataset completo, caché frío. Sin objetivo.
4. Lectura de una métrica desde caché -> objetivo < 200 ms.
5. Aplicación de un filtro con propagación a todas las gráficas -> objetivo < 200 ms.
6. Cambio de señal en la gráfica tipo #2 -> objetivo < 100 ms (UHF) / < 250 ms (AE).

Reglas de la medición, para que dos corridas sean comparables:

- **Caché siempre frío al arrancar**: el backend se crea en un directorio temporal
  propio que se borra al terminar, nunca en ``cache_data/`` del proyecto. Así "caché
  frío" es frío de verdad y la corrida no ensucia el caché real del usuario.
- **El precalentamiento en segundo plano se apaga** (``warmup_on_load=False``): si no,
  competiría por CPU con lo que se está midiendo y volvería el resultado irreproducible.
- **Se mide lo que espera el navegador**, no solo lo que construye Python: donde la
  operación termina en una figura, el tiempo incluye ``plotly.io.to_json`` -- una figura
  construida pero no serializada todavía no está en pantalla.
- Cada operación repetible se mide con ``repeats`` muestras tras descartar una de
  calentamiento, y se reporta la **mediana** (ver ``benchmarks/harness.py``).
"""
from __future__ import annotations

import argparse
import json
import platform
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from functools import partial
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import plotly.io as pio

from benchmarks.harness import BenchmarkResult, format_markdown_table, measure, measure_once, to_json_dict
from cache.service import get_or_compute_group_intrinsic, get_or_compute_puntual
from core.grouping import GroupingMode
from core.normalization import normalize
from metrics.registry import get_metric
from ui.components.graph_metric import build_metric_figure
from ui.components.graph_signal import build_signal_figure
from ui.components.graph_timeseries import build_timeseries_figure
from ui.state import AppState
from utils.profiling import get_records, reset_records, set_enabled

# Métricas representativas: una de dominio tiempo (barata, sin FFT) y una de dominio
# frecuencia (paga la FFT centralizada) -- el par muestra el rango real de costo, que un
# solo número escondería.
METRICA_TIEMPO = "rms"
METRICA_FRECUENCIA = "feq"
METRICA_GRUPO = "tasa_pulsos"
AGRUPAMIENTO: tuple[GroupingMode, float] = ("by_time", 60.0)

OBJETIVO_RENDER_G1_MS = 2000.0
OBJETIVO_CACHE_MS = 200.0
OBJETIVO_FILTRO_MS = 200.0
OBJETIVO_CAMBIO_SENAL_MS = {"UHF": 100.0, "AE": 250.0}


def _serializar(fig: Any) -> int:
    """Serializa la figura como lo hace Dash al responder un callback. Retorna el tamaño
    en bytes para poder correlacionar tiempo con volumen transferido."""
    return len(pio.to_json(fig))


def run(dataset_path: Path, repeats: int) -> tuple[list[BenchmarkResult], dict[str, Any]]:
    resultados: list[BenchmarkResult] = []
    cache_dir = Path(tempfile.mkdtemp(prefix="benchmark_cache_"))
    state: AppState | None = None

    set_enabled(True)   # las etapas de utils/profiling dan el desglose por sensor de la ingesta
    reset_records()

    try:
        state = AppState(cache_dir=cache_dir, warmup_on_load=False)

        # --- 1. Ingesta (una sola vez: no es repetible sin volver a leer del disco) ----
        ingesta_ms, dataset = measure_once(lambda: state.load_dataset(dataset_path))
        sensores = [s for s, b in dataset.blocks.items() if b.data.shape[0] > 0]
        n_total = sum(dataset.blocks[s].data.shape[0] for s in sensores)

        por_sensor = {
            str(r.fields.get("sensor")): r for r in get_records("ingesta.sensor") if r.fields.get("sensor")
        }
        for sensor in sensores:
            n = dataset.blocks[sensor].data.shape[0]
            record = por_sensor.get(sensor)
            ms = record.duration_ms if record is not None else float("nan")
            resultados.append(
                BenchmarkResult(
                    operacion="Ingesta (matriz global)", sensor=sensor,
                    mediana_ms=ms, min_ms=ms, max_ms=ms, repeticiones=1,
                    objetivo_ms=None,
                    notas={
                        "n_senales": n,
                        "throughput_senales_por_s": round(n / (ms / 1000.0), 1) if ms and ms == ms else None,
                        "n_muestras_por_senal": int(dataset.blocks[sensor].data.shape[1]),
                    },
                )
            )
        set_enabled(False)  # a partir de aquí el reloj lo lleva el arnés, no las etapas

        for sensor in sensores:
            block = dataset.blocks[sensor]
            cfg = dataset.sensor_configs[sensor]
            mask = state.get_active_mask(sensor)

            # --- 2. Render inicial de la gráfica tipo #1 -------------------------------
            def render_g1() -> int:
                fig = build_timeseries_figure(
                    cfg, block, dataset.environmental, dataset.events, dataset.t0, mask
                )
                return _serializar(fig)

            bytes_json = render_g1()
            mediana, mn, mx = measure(render_g1, repeats=repeats)
            resultados.append(
                BenchmarkResult(
                    operacion="Render gráfica #1 (dataset completo)", sensor=sensor,
                    mediana_ms=mediana, min_ms=mn, max_ms=mx, repeticiones=repeats,
                    objetivo_ms=OBJETIVO_RENDER_G1_MS,
                    notas={"n_senales_dibujadas": int((block.valid_mask & mask).sum()), "bytes_json": bytes_json},
                )
            )

            # --- 3. Métrica puntual sobre el dataset completo, caché frío --------------
            def calcular(mid: str) -> Any:
                return get_or_compute_puntual(state.cache, block, cfg, dataset.dataset_id, mid)

            for metric_id in (METRICA_TIEMPO, METRICA_FRECUENCIA):
                ms, _ = measure_once(partial(calcular, metric_id))
                resultados.append(
                    BenchmarkResult(
                        operacion=f"Métrica puntual '{metric_id}' (caché frío)", sensor=sensor,
                        mediana_ms=ms, min_ms=ms, max_ms=ms, repeticiones=1,
                        objetivo_ms=None,
                        notas={
                            "dominio": get_metric(metric_id).dominio,
                            "n_senales": int(block.valid_mask.sum()),
                            "senales_por_s": round(int(block.valid_mask.sum()) / (ms / 1000.0), 1),
                        },
                    )
                )

            # --- 4. Lectura desde caché (ya está caliente por el paso anterior) --------
            for metric_id in (METRICA_TIEMPO, METRICA_FRECUENCIA):
                mediana, mn, mx = measure(partial(calcular, metric_id), repeats=repeats)
                resultados.append(
                    BenchmarkResult(
                        operacion=f"Lectura desde caché '{metric_id}'", sensor=sensor,
                        mediana_ms=mediana, min_ms=mn, max_ms=mx, repeticiones=repeats,
                        objetivo_ms=OBJETIVO_CACHE_MS,
                        notas={"n_puntos": int(block.valid_mask.sum())},
                    )
                )

            # --- 5. Cambio de señal en la gráfica tipo #2 ------------------------------
            indices = np.linspace(0, block.data.shape[0] - 1, 8, dtype=np.int64)
            contador = {"i": 0}

            def cambio_senal() -> int:
                idx = int(indices[contador["i"] % len(indices)])
                contador["i"] += 1
                fila = normalize(block.data[[idx]], block.vrange[[idx]])[0]
                fig, _ = build_signal_figure(cfg, fila)
                return _serializar(fig)

            mediana, mn, mx = measure(cambio_senal, repeats=repeats)
            resultados.append(
                BenchmarkResult(
                    operacion="Cambio de señal (gráfica #2)", sensor=sensor,
                    mediana_ms=mediana, min_ms=mn, max_ms=mx, repeticiones=repeats,
                    objetivo_ms=OBJETIVO_CAMBIO_SENAL_MS.get(sensor),
                    notas={"n_muestras": int(block.data.shape[1]), "diezmada": sensor == "AE"},
                )
            )

            # --- 6. Filtro con propagación a todas las gráficas de la ventana ---------
            # Peor caso realista de la Fase 6: excluir un tramo y repintar la ventana
            # entera -- gráfica #1 + una gráfica #3 puntual (filtro posterior sobre el
            # caché) + una gráfica #3 de grupo (bypass de caché, recálculo real).
            modo, ventana = AGRUPAMIENTO
            n = block.data.shape[0]
            a_excluir = np.arange(n // 4, n // 4 + max(1, n // 20), dtype=np.int64)

            def filtro_con_propagacion() -> None:
                state.apply_filter(sensor, a_excluir)
                activa = state.get_active_mask(sensor)
                _serializar(
                    build_timeseries_figure(cfg, block, dataset.environmental, dataset.events, dataset.t0, activa)
                )
                ts, vals = get_or_compute_puntual(
                    state.cache, block, cfg, dataset.dataset_id, METRICA_TIEMPO, active_mask=activa
                )
                _serializar(
                    build_metric_figure(ts, vals, dataset.events, dataset.t0, label=METRICA_TIEMPO)
                )
                tg, vg, part = get_or_compute_group_intrinsic(
                    state.cache, block, cfg, dataset.dataset_id, METRICA_GRUPO, modo, ventana, active_mask=activa
                )
                _serializar(
                    build_metric_figure(tg, vg, dataset.events, dataset.t0, label=METRICA_GRUPO, is_partial=part)
                )
                state.undo_filter(sensor)

            mediana, mn, mx = measure(filtro_con_propagacion, repeats=repeats)
            state.reset_filters(sensor)
            resultados.append(
                BenchmarkResult(
                    operacion="Filtro + propagación (#1 + 2 gráficas #3)", sensor=sensor,
                    mediana_ms=mediana, min_ms=mn, max_ms=mx, repeticiones=repeats,
                    objetivo_ms=OBJETIVO_FILTRO_MS,
                    notas={"n_excluidas": int(a_excluir.size), "metrica_grupo": METRICA_GRUPO},
                )
            )

        metadata = {
            "fecha_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "dataset": str(dataset_path),
            "dataset_id": dataset.dataset_id,
            "n_senales_total": n_total,
            "ingesta_total_ms": round(ingesta_ms, 1),
            "throughput_total_senales_por_s": round(n_total / (ingesta_ms / 1000.0), 1),
            "repeticiones": repeats,
            "python": platform.python_version(),
            "plataforma": f"{platform.system()} {platform.release()}",
            "procesador": platform.processor(),
        }
        return resultados, metadata
    finally:
        if state is not None:
            state.cache.close()
        shutil.rmtree(cache_dir, ignore_errors=True)
        set_enabled(False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmarks §9.2 del analizador UHF/AE")
    parser.add_argument("--dataset", required=True, type=Path, help="Ruta al archivo .hdf5 de origen")
    parser.add_argument("--repeats", type=int, default=5, help="Muestras por operación repetible (default: 5)")
    parser.add_argument("--json", type=Path, default=None, help="Escribe el reporte completo en este archivo JSON")
    parser.add_argument("--markdown", type=Path, default=None, help="Escribe la tabla markdown en este archivo")
    args = parser.parse_args()

    if not args.dataset.exists():
        raise SystemExit(f"No existe el dataset: {args.dataset}")

    resultados, metadata = run(args.dataset, args.repeats)
    tabla = format_markdown_table(resultados)

    print(f"\nDataset: {metadata['dataset']}")
    print(f"Señales totales: {metadata['n_senales_total']} · ingesta {metadata['ingesta_total_ms']:.0f} ms "
          f"({metadata['throughput_total_senales_por_s']:.0f} señales/s)")
    print(f"{metadata['plataforma']} · Python {metadata['python']} · mediana de {metadata['repeticiones']} muestras\n")
    print(tabla)

    incumplidos = [r for r in resultados if r.cumple is False]
    print(f"\n{len(incumplidos)} objetivo(s) sin cumplir." if incumplidos else "\nTodos los objetivos con umbral se cumplen.")
    for r in incumplidos:
        print(f"  - {r.operacion} [{r.sensor}]: {r.mediana_ms:.0f} ms (objetivo < {r.objetivo_ms:.0f} ms)")

    if args.json:
        args.json.write_text(json.dumps(to_json_dict(resultados, metadata), indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nJSON escrito en {args.json}")
    if args.markdown:
        args.markdown.write_text(tabla + "\n", encoding="utf-8")
        print(f"Tabla escrita en {args.markdown}")


if __name__ == "__main__":
    main()
