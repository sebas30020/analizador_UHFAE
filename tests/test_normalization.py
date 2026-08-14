import numpy as np
import pytest

from core.normalization import NORMALIZATION_VERSION, compute_valid_mask, normalize


def test_valid_mask_flags_zero_and_negative_vrange():
    trigger = np.array([0.1, 0.1, 0.1, 0.1])
    vrange = np.array([1.0, 0.0, -1.0, np.nan])
    mask = compute_valid_mask(trigger, vrange)
    assert mask.tolist() == [True, False, False, False]


def test_valid_mask_flags_nan_trigger():
    trigger = np.array([0.1, np.nan])
    vrange = np.array([1.0, 1.0])
    mask = compute_valid_mask(trigger, vrange)
    assert mask.tolist() == [True, False]


def test_valid_mask_shape_mismatch_raises():
    with pytest.raises(ValueError):
        compute_valid_mask(np.array([0.1, 0.2]), np.array([1.0]))


def test_normalize_divides_by_own_vrange_per_row():
    data = np.array([[2.0, 4.0], [10.0, 20.0]], dtype=np.float32)
    vrange = np.array([2.0, 5.0])
    result = normalize(data, vrange)
    assert np.allclose(result, [[1.0, 2.0], [2.0, 4.0]])


def test_normalize_marks_invalid_rows_as_nan():
    data = np.array([[2.0, 4.0], [10.0, 20.0]], dtype=np.float32)
    vrange = np.array([2.0, 0.0])
    mask = compute_valid_mask(np.array([0.1, 0.1]), vrange)
    result = normalize(data, vrange, mask)
    assert np.allclose(result[0], [1.0, 2.0])
    assert np.isnan(result[1]).all()


def test_normalization_version_is_stable_string():
    # Cambiar este valor invalida todo el caché existente (clave de caché, §7 FASE0).
    assert NORMALIZATION_VERSION == "v1_divide_by_vrange"
