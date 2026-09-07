"""Contrato del arranque WSGI de producción (Etapa 6, plan §F).

Lo que se protege aquí no es "que arranque" — eso lo dice waitress — sino la única
decisión que rompe la aplicación en silencio si alguien la cambia: **un solo proceso**.
El estado vive en singletons de proceso (``AppState``, el ``LoadedDataset``, los
registries de referencias y mapas). Servir con varios *workers* de proceso no falla
ruidosamente: el usuario carga un dataset, la siguiente petición cae en otro worker y ve
"ningún archivo cargado".
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_run_server():
    """Carga ``scripts/run_server.py``, que no es un paquete importable."""
    spec = importlib.util.spec_from_file_location(
        "run_server_under_test", _REPO_ROOT / "scripts" / "run_server.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_configuracion_es_monoproceso_en_loopback():
    run_server = _load_run_server()

    assert run_server.HOST == "127.0.0.1"
    assert run_server.PORT == 8050
    assert run_server.THREADS == 8
    # Hilos, no procesos: waitress no expone ninguna opción de workers y este módulo
    # tampoco debe inventarse una.
    assert not any(
        nombre.lower() in {"workers", "processes", "n_workers"} for nombre in vars(run_server)
    )


def test_main_sirve_el_wsgi_de_flask_con_los_parametros_declarados(monkeypatch):
    run_server = _load_run_server()
    llamadas: list[tuple[object, dict]] = []

    class _AppFalsa:
        server = object()

    monkeypatch.setattr(run_server, "create_app", lambda: _AppFalsa())

    waitress = pytest.importorskip("waitress")
    monkeypatch.setattr(
        waitress, "serve", lambda app, **kwargs: llamadas.append((app, kwargs))
    )

    run_server.main()

    assert len(llamadas) == 1
    app, kwargs = llamadas[0]
    # El WSGI es el Flask subyacente (``.server``), no el objeto Dash.
    assert app is _AppFalsa.server
    assert kwargs == {"host": "127.0.0.1", "port": 8050, "threads": 8}


def test_el_entrypoint_del_modulo_no_duplica_el_proceso():
    """``ui.app.main`` con ``debug=True`` levanta el reloader de Werkzeug, que duplica el
    proceso — con el dataset cargado, el doble de RAM (plan §F.3)."""
    fuente = (_REPO_ROOT / "ui" / "app.py").read_text(encoding="utf-8")
    assert "debug=False" in fuente
    assert "debug=True" not in fuente


@pytest.mark.skipif("waitress" not in sys.modules and importlib.util.find_spec("waitress") is None,
                    reason="waitress no instalado")
def test_waitress_esta_pinneado_en_requirements():
    requirements = (_REPO_ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "waitress==" in requirements
