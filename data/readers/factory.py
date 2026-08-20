"""Elección de lector por contenido del archivo, no por extensión (rama
``lectura_keysight``): ``med_5_ago_3.hdf5`` y los archivos Keysight de
``D:\\data\\data\\main\\ruido\\`` usan indistintamente ``.h5``/``.hdf5``, así que la
extensión no distingue el esquema.

Único punto de la capa de presentación que sabe que existe más de un formato de origen
-- ``ui/state.py::load_dataset`` llama a :func:`open_reader` y a partir de ahí solo habla
con la interfaz :class:`~data.readers.base.OriginReader` (§10.3, prueba de fuego #3 del
PROMPT maestro: "cambiar el motor de origen no toca nada aguas arriba de la capa de
datos").
"""
from __future__ import annotations

from pathlib import Path

import h5py

from data.readers.base import OriginReader
from data.readers.hdf5_reader import HDF5Reader
from data.readers.keysight_reader import KeysightSegmentedReader, _is_keysight_segmented_file


def _looks_like_chunked_experiment(f: h5py.File) -> bool:
    """Esquema ``med_5_ago_3.hdf5``: un grupo raíz por experimento con subgrupos
    ``chunk_*`` (``esquema_med_5_ago_3.md`` §1)."""
    for name in f.keys():
        node = f[name]
        if isinstance(node, h5py.Group) and any(k.startswith("chunk_") for k in node.keys()):
            return True
    return False


def open_reader(path: str | Path) -> OriginReader:
    """Inspecciona ``path`` y devuelve la implementación de :class:`OriginReader` que
    corresponde, ya sin abrir -- el llamador la usa como context manager
    (``with open_reader(path) as reader:``), igual que si hubiera instanciado el reader
    concreto directamente.
    """
    path = Path(path)
    with h5py.File(path, mode="r") as f:
        if _is_keysight_segmented_file(f):
            return KeysightSegmentedReader(path)
        if _looks_like_chunked_experiment(f):
            return HDF5Reader(path)

    raise ValueError(
        f"No se reconoce el esquema de '{path}': no es un archivo Keysight en memoria "
        "segmentada (falta FileType/KeysightH5FileType) ni un experimento con chunks "
        "(esquema_med_5_ago_3.md, falta un grupo raíz con subgrupos 'chunk_*')."
    )
