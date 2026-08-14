"""Script auxiliar de desarrollo: arranca el servidor Dash sin el reloader de debug
(evita procesos hijo duplicados al lanzarlo en background para pruebas manuales)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ui.app import create_app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=False, host="127.0.0.1", port=8050)
