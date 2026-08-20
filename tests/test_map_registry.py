"""``ui/map_registry.py`` -- registro de proceso de los ``MapDataset`` que los mapas
#4/#5 muestran, para el resaltado en vivo (§5.3 del prompt) sin recalcular. Mismo
patrón que ``ui/reference_registry.py``.
"""
import numpy as np

from ui.map_registry import MapDatasetRegistry
from viz.maps import MapDataset


def _dataset() -> MapDataset:
    return MapDataset(
        signal_indices=np.array([1, 2, 3]),
        coords={"x": np.array([1.0, 2.0, 3.0]), "y": np.array([4.0, 5.0, 6.0])},
        omitted_counts={"x": 0, "y": 0},
        axis_labels={"x": "a", "y": "b"},
    )


def test_new_registry_starts_empty():
    registry = MapDatasetRegistry()
    assert registry.get("2d") is None
    assert registry.get("3d") is None


def test_set_and_get_roundtrip():
    registry = MapDatasetRegistry()
    dataset = _dataset()
    registry.set("2d", dataset)
    assert registry.get("2d") is dataset
    assert registry.get("3d") is None


def test_set_none_clears_entry():
    registry = MapDatasetRegistry()
    registry.set("2d", _dataset())
    registry.set("2d", None)
    assert registry.get("2d") is None


def test_2d_and_3d_entries_are_independent():
    registry = MapDatasetRegistry()
    d2 = _dataset()
    d3 = _dataset()
    registry.set("2d", d2)
    registry.set("3d", d3)
    assert registry.get("2d") is d2
    assert registry.get("3d") is d3
