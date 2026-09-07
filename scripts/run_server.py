"""Arranque de la aplicación con servidor WSGI de producción (waitress).

Etapa 6 del plan maestro (``archivos_md/PLAN_OPTIMIZACION_Y_FILTRADO.md`` §F). Sustituye
al servidor de desarrollo de Werkzeug que arranca ``app.run()``, con dos efectos reales y
uno que **no** hay que atribuirle:

- Quita el aviso de "development server" y da una capa HTTP robusta (§F.1).
- No hace *buffering* completo de la respuesta como Werkzeug, lo que junto con
  ``compress=True`` (Etapa 4) reduce la latencia y la RAM transitoria de las figuras de
  1-3 MB (§F.3).
- **No acelera el cálculo.** El cuello de botella medido está en Python puro — ingesta,
  métricas, FFT, pintado — y ningún WSGI lo toca (§F.2). Eso lo resuelve sacar el cómputo
  de los callbacks (Etapa 1), no este archivo.

**Monoproceso con N hilos: es un requisito, no una preferencia** (§F.5). La aplicación
mantiene el estado en singletons de proceso (``ui/state.py::AppState``, el
``LoadedDataset`` de ~1 GB, ``ui/reference_registry.py`` y ``ui/map_registry.py``). Con
varios *workers* de proceso — gunicorn, uvicorn — cada uno tendría su propia copia del
estado y peticiones consecutivas caerían en *workers* distintos: el usuario cargaría un
dataset y la siguiente interacción vería "ningún archivo cargado". waitress es además
nativo de Windows, a diferencia de gunicorn. Si algún día hiciera falta multiproceso,
primero hay que externalizar el estado (Redis / ``diskcache``), y eso es otro proyecto.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui.app import create_app  # noqa: E402

HOST = "127.0.0.1"
"""Solo *loopback*: la herramienta es de un solo usuario en una sola máquina."""

PORT = 8050
"""Mismo puerto que el servidor de desarrollo, para no romper enlaces ni marcadores."""

THREADS = 8
"""Hilos de un **único** proceso (§F.5). Los callbacks son CPU-bound y sueltan el GIL en
NumPy/HDF5, así que unos pocos hilos bastan para que la interfaz siga respondiendo
mientras un cálculo largo corre en segundo plano (Etapa 1)."""


def main() -> None:
    from waitress import serve

    serve(create_app().server, host=HOST, port=PORT, threads=THREADS)


if __name__ == "__main__":
    main()
