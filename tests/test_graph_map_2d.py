"""Gráfica #4 -- Mapa de separación 2D (``archivos_md/prompt-mapas2d3d.md``).

Como ``tests/test_graph_metric.py``: ejercita ``build_map_2d_figure`` directamente con
un ``MapDataset`` sintético, sin pasar por Dash ni por el caché -- la construcción del
dataset ya está cubierta en ``tests/test_maps.py``.
"""
import numpy as np

from ui.components.graph_map_2d import build_map_2d_figure
from ui.components.graph_map_common import (
    AXIS_NOT_ASSIGNED_MESSAGE,
    DUPLICATE_AXIS_WARNING,
    build_empty_map_figure,
)
from viz.maps import MapDataset


def _dataset(x, y, signal_indices=None, omitted=None) -> MapDataset:
    x = np.asarray(x, dtype=np.float64)
    signal_indices = np.arange(x.shape[0]) if signal_indices is None else np.asarray(signal_indices)
    return MapDataset(
        signal_indices=signal_indices,
        coords={"x": x, "y": np.asarray(y, dtype=np.float64)},
        omitted_counts=omitted or {"x": 0, "y": 0},
        axis_labels={"x": "Vmax (V)", "y": "RMS (V)"},
    )


def test_basic_scatter_one_trace_per_point():
    dataset = _dataset([1.0, 2.0, 3.0], [4.0, 5.0, 6.0])
    fig = build_map_2d_figure(dataset)
    assert len(fig.data) == 1
    trace = fig.data[0]
    assert trace.mode == "markers"
    assert np.array_equal(trace.x, [1.0, 2.0, 3.0])
    assert np.array_equal(trace.y, [4.0, 5.0, 6.0])
    assert np.array_equal(trace.customdata, [0, 1, 2])


def test_axis_titles_come_from_dataset_labels():
    dataset = _dataset([1.0], [2.0])
    fig = build_map_2d_figure(dataset)
    assert fig.layout.xaxis.title.text == "Vmax (V)"
    assert fig.layout.yaxis.title.text == "RMS (V)"


def test_hovertemplate_shows_signal_index_and_both_axis_values():
    dataset = _dataset([1.0], [2.0])
    fig = build_map_2d_figure(dataset)
    tpl = fig.data[0].hovertemplate
    assert "Señal %{customdata}" in tpl
    assert "Vmax (V)" in tpl and "%{x" in tpl
    assert "RMS (V)" in tpl and "%{y" in tpl


def test_selected_signal_adds_highlight_trace():
    dataset = _dataset([1.0, 2.0, 3.0], [4.0, 5.0, 6.0], signal_indices=[10, 11, 12])
    fig = build_map_2d_figure(dataset, selected_signal_index=11)
    assert len(fig.data) == 2
    highlight = fig.data[1]
    assert highlight.x[0] == 2.0 and highlight.y[0] == 5.0
    assert highlight.showlegend is False


def test_selected_signal_not_in_map_adds_no_highlight():
    # La señal seleccionada pudo quedar excluida del mapa (filtrada, o con NaN en algún
    # eje) -- no debe fallar, simplemente no hay nada que resaltar.
    dataset = _dataset([1.0, 2.0], [4.0, 5.0], signal_indices=[10, 11])
    fig = build_map_2d_figure(dataset, selected_signal_index=999)
    assert len(fig.data) == 1


def test_no_selection_means_no_highlight_trace():
    dataset = _dataset([1.0, 2.0], [4.0, 5.0])
    fig = build_map_2d_figure(dataset)
    assert len(fig.data) == 1


def test_omitted_counts_produce_info_annotation():
    dataset = _dataset([1.0, 2.0], [4.0, 5.0], omitted={"x": 0, "y": 3})
    fig = build_map_2d_figure(dataset)
    assert len(fig.layout.annotations) == 1
    assert "3 sin valor para RMS (V)" in fig.layout.annotations[0].text


def test_no_omitted_no_warning_means_no_annotation():
    dataset = _dataset([1.0], [2.0])
    fig = build_map_2d_figure(dataset)
    assert len(fig.layout.annotations) == 0


def test_duplicate_axis_warning_adds_annotation():
    dataset = _dataset([1.0, 2.0], [1.0, 2.0])
    fig = build_map_2d_figure(dataset, same_metric_warning=True)
    assert len(fig.layout.annotations) == 1
    assert DUPLICATE_AXIS_WARNING in fig.layout.annotations[0].text


def test_empty_signal_set_shows_no_points_message():
    dataset = _dataset([], [])
    fig = build_map_2d_figure(dataset)
    assert len(fig.data) == 1
    assert len(fig.layout.annotations) == 1
    assert "Sin señales" in fig.layout.annotations[0].text


def test_uirevision_is_stable_across_rebuilds_with_same_value():
    dataset = _dataset([1.0], [2.0])
    fig1 = build_map_2d_figure(dataset, uirevision="UHF|dataset-1|x:vmax|y:rms")
    fig2 = build_map_2d_figure(dataset, uirevision="UHF|dataset-1|x:vmax|y:rms")
    assert fig1.layout.uirevision == fig2.layout.uirevision == "UHF|dataset-1|x:vmax|y:rms"


# --- Estado vacío informativo (eje sin métrica asignada) -------------------------------

def test_empty_map_figure_shows_informative_message_not_error():
    fig = build_empty_map_figure()
    assert len(fig.data) == 0
    assert fig.layout.annotations[0].text == AXIS_NOT_ASSIGNED_MESSAGE


def test_empty_map_figure_accepts_custom_message():
    fig = build_empty_map_figure("mensaje personalizado")
    assert fig.layout.annotations[0].text == "mensaje personalizado"
