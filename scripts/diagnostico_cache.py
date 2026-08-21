"""Diagnóstico rápido del estado del caché (¿por qué la aplicación va lenta?).

No modifica nada: solo lee ``cache_data/`` y reporta cuántas métricas hay precalculadas,
para distinguir "el caché está frío" (todo se recalcula, segundos por métrica) de una
regresión real de rendimiento (mismos datos, mismo caché, y aun así lento).

Uso:

    .venv\\Scripts\\python.exe scripts\\diagnostico_cache.py
    .venv\\Scripts\\python.exe scripts\\diagnostico_cache.py RUTA_DEL_ARCHIVO.hdf5

Con una ruta, además dice si ESE archivo concreto tiene entradas en el caché — que es lo
que decide si abrirlo será instantáneo o pagará el cálculo en frío. Las cifras de
referencia con caché caliente y frío están en ``docs/RENDIMIENTO.md`` §1.
"""
from __future__ import annotations

import sqlite3
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

CACHE_DIR = PROJECT_ROOT / "cache_data"
DB_PATH = CACHE_DIR / "cache_index.sqlite"
PAYLOAD_PATH = CACHE_DIR / "cache_payload.h5"


def _mb(path: Path) -> str:
    return f"{path.stat().st_size / (1024 * 1024):.1f} MB" if path.exists() else "no existe"


def main() -> int:
    print("=" * 68)
    print("DIAGNÓSTICO DE CACHÉ")
    print("=" * 68)
    print(f"Carpeta      : {CACHE_DIR}")
    print(f"Índice SQLite: {_mb(DB_PATH)}")
    print(f"Payload HDF5 : {_mb(PAYLOAD_PATH)}")

    if not DB_PATH.exists():
        print()
        print(">>> EL CACHÉ ESTÁ VACÍO (no existe el índice).")
        print("    Cada métrica se calculará desde cero la primera vez: segundos por")
        print("    métrica y por sensor, no milisegundos. Es la causa más común de que")
        print("    la aplicación 'se sienta lenta' tras un clon nuevo o tras borrar")
        print("    cache_data/. Se llena solo a medida que se usan las métricas.")
        return 0

    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM cache_entries").fetchone()[0]
    print(f"Entradas     : {total}")

    if total == 0:
        print()
        print(">>> EL CACHÉ ESTÁ VACÍO (0 entradas). Ver nota de arriba.")
        conn.close()
        return 0

    print()
    print("Por dataset (los primeros 10):")
    rows = conn.execute(
        "SELECT dataset_id, COUNT(*) FROM cache_entries GROUP BY dataset_id ORDER BY 2 DESC LIMIT 10"
    ).fetchall()
    for dataset_id, n in rows:
        # dataset_id es "ruta:tamaño:mtime_ns" -- se muestra solo el nombre del archivo.
        nombre = dataset_id.rsplit(":", 2)[0]
        print(f"  {n:5d} entradas  {Path(nombre).name}")

    print()
    print("Por sensor:")
    for sensor, n in conn.execute("SELECT sensor, COUNT(*) FROM cache_entries GROUP BY sensor").fetchall():
        print(f"  {n:5d} entradas  {sensor}")

    if len(sys.argv) > 1:
        objetivo = Path(sys.argv[1])
        print()
        print(f"Archivo consultado: {objetivo}")
        if not objetivo.exists():
            print("  >>> NO EXISTE esa ruta; no puedo comprobar su caché.")
        else:
            from data.readers.base import stable_file_dataset_id

            dsid = stable_file_dataset_id(objetivo)
            n = conn.execute("SELECT COUNT(*) FROM cache_entries WHERE dataset_id = ?", (dsid,)).fetchone()[0]
            if n:
                metricas = Counter(
                    m for (m,) in conn.execute(
                        "SELECT metric_id FROM cache_entries WHERE dataset_id = ?", (dsid,)
                    )
                )
                print(f"  {n} entradas en caché para este archivo: {', '.join(sorted(metricas))}")
                print("  >>> Este archivo NO debería ir lento por caché frío.")
            else:
                print("  >>> 0 entradas para este archivo: TODO se calculará en frío.")
                print("      Si el archivo se movió, se renombró o se reescribió, su")
                print("      dataset_id (ruta:tamaño:mtime_ns) cambia y el caché anterior")
                print("      deja de aplicar aunque el contenido sea el mismo.")

    conn.close()
    print()
    print("Referencia (docs/RENDIMIENTO.md §1): 'rms' sobre UHF cuesta ~2,8 s en frío")
    print("contra ~3,4 ms desde caché. Un factor ~800x: con el caché frío, la misma")
    print("aplicación se siente lentísima sin que nada haya cambiado en el código.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
