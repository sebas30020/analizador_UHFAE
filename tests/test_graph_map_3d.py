"""Gráfica #5 -- Mapa de separación 3D (``archivos_md/prompt-mapas2d3d.md``).

Mismo patrón que ``tests/test_graph_map_2d.py``: ``MapDataset`` sintético, sin Dash ni
caché de por medio.
"""
import numpy as np

from ui.components.graph_map_3d import build_map_3d_figure
from ui.components.graph_map_common import DUPLICATE_AXIS_WARNING
from viz.maps import MapDataset


def _dataset(x, y, z, signal_indices=None, omitted=None) -> MapDataset:
    x = np.asarray(x, dtype=np.float64)
    signal_indices = np.arange(x.shape[0]) if signal_indices is None else np.asarray(signal_indices)
    return MapDataset(
        signal_indices=signal_indices,
        coords={"x": x, "y": np.asarray(y, dtype=np.float64), "z": np.asarray(z, dtype=np.float64)},
        omitted_counts=omitted or {"x": 0, "y": 0, "z": 0},
        axis_labels={"x": "Vmax (V)", "y": "RMS (V)", "z": "Vpp (V)"},
    )


def test_basic_scatter3d_one_trace_per_point():
    dataset = _dataset([1.0, 2.0], [3.0, 4.0], [5.0, 6.0])
    fig = build_map_3d_figure(dataset)
    # Dos trazas SIEMPRE: puntos + resaltado (vacía si no hay selección) -- ver
    # docstring de build_map_2d_figure sobre por qué el conteo es constante.
    assert len(fig.data) == 2
    trace = fig.data[0]
    assert trace.type == "scatter3d"
    assert trace.mode == "markers"
    assert np.array_equal(trace.z, [5.0, 6.0])


def test_scene_axis_titles_come_from_dataset_labels():
    dataset = _dataset([1.0], [2.0], [3.0])
    fig = build_map_3d_figure(dataset)
    assert fig.layout.scene.xaxis.title.text == "Vmax (V)"
    assert fig.layout.scene.yaxis.title.text == "RMS (V)"
    assert fig.layout.scene.zaxis.title.text == "Vpp (V)"


def test_hovertemplate_shows_signal_index_and_all_three_axis_values():
    dataset = _dataset([1.0], [2.0], [3.0])
    fig = build_map_3d_figure(dataset)
    tpl = fig.data[0].hovertemplate
    assert "Señal %{customdata}" in tpl
    assert "%{x" in tpl and "%{y" in tpl and "%{z" in tpl


def test_selected_signal_adds_highlight_trace():
    dataset = _dataset([1.0, 2.0], [3.0, 4.0], [5.0, 6.0], signal_indices=[10, 11])
    fig = build_map_3d_figure(dataset, selected_signal_index=11)
    assert len(fig.data) == 2
    highlight = fig.data[1]
    assert highlight.type == "scatter3d"
    assert highlight.x[0] == 2.0 and highlight.y[0] == 4.0 and highlight.z[0] == 6.0


def test_selected_signal_not_in_map_leaves_highlight_trace_empty():
    dataset = _dataset([1.0], [2.0], [3.0], signal_indices=[10])
    fig = build_map_3d_figure(dataset, selected_signal_index=999)
    assert len(fig.data) == 2
    highlight = fig.data[1]
    assert list(highlight.x) == [] and list(highlight.y) == [] and list(highlight.z) == []


def test_duplicate_axis_warning_adds_annotation():
    dataset = _dataset([1.0], [1.0], [1.0])
    fig = build_map_3d_figure(dataset, same_metric_warning=True)
    assert DUPLICATE_AXIS_WARNING in fig.layout.annotations[0].text


def test_omitted_counts_produce_info_annotation():
    dataset = _dataset([1.0], [2.0], [3.0], omitted={"x": 0, "y": 0, "z": 2})
    fig = build_map_3d_figure(dataset)
    assert "2 sin valor para Vpp (V)" in fig.layout.annotations[0].text


def test_empty_signal_set_shows_no_points_message():
    dataset = _dataset([], [], [])
    fig = build_map_3d_figure(dataset)
    assert "Sin señales" in fig.layout.annotations[0].text


def test_uirevision_is_stable_across_rebuilds_with_same_value():
    dataset = _dataset([1.0], [2.0], [3.0])
    fig1 = build_map_3d_figure(dataset, uirevision="UHF|dataset-1|x:a|y:b|z:c")
    fig2 = build_map_3d_figure(dataset, uirevision="UHF|dataset-1|x:a|y:b|z:c")
    assert fig1.layout.uirevision == fig2.layout.uirevision == "UHF|dataset-1|x:a|y:b|z:c"
