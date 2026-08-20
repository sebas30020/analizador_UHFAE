"""Registro de proceso de los ``MapDataset`` que los mapas #4/#5 actualmente muestran
(``archivos_md/prompt-mapas2d3d.md``), para que el resaltado de la señal seleccionada
(§5.3 del prompt: "la señal actualmente seleccionada aparece resaltada en #4 y en #5
simultáneamente") se pueda actualizar al navegar sin volver a pasar por el caché ni por
el motor de métricas -- mismo patrón que ``ui/reference_registry.py``.

``ui/callbacks/sensor_window_callbacks.py::_on_refresh_maps`` es el único escritor:
sustituye la entrada de cada mapa cada vez que lo recalcula (habilitado, ejes, dataset o
filtro). El callback de resaltado (``_on_refresh_map_highlight``), disparado únicamente
por ``nav-index``, es un lector puro que nunca dispara ese recálculo -- así navegar
entre señales (Anterior/Siguiente, auto-play, clic en cualquier gráfica) no reconstruye
la nube de miles de puntos en cada paso, solo mueve la traza de resaltado.
"""
from __future__ import annotations

from viz.maps import MapDataset

MapId = str  # "2d" | "3d"


class MapDatasetRegistry:
    def __init__(self) -> None:
        self._datasets: dict[MapId, MapDataset | None] = {"2d": None, "3d": None}

    def set(self, map_id: MapId, dataset: MapDataset | None) -> None:
        self._datasets[map_id] = dataset

    def get(self, map_id: MapId) -> MapDataset | None:
        return self._datasets.get(map_id)


_registry = MapDatasetRegistry()


def get_map_registry() -> MapDatasetRegistry:
    return _registry
