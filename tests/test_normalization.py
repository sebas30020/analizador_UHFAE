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


def test_normalize_matches_float64_reference_within_float32_ulp():
    # Regresión de memoria: normalize() ya no promueve a float64 internamente (ver plan
    # de corrección de agotamiento de memoria). El resultado debe seguir coincidiendo
    # con la referencia float64->float32 dentro del redondeo de un ULP de float32.
    rng = np.random.default_rng(0)
    data = rng.normal(scale=1000.0, size=(200, 16)).astype(np.float32)
    vrange = rng.uniform(0.1, 50.0, size=200)

    result = normalize(data, vrange)
    reference = (data.astype(np.float64) / vrange[:, np.newaxis]).astype(np.float32)

    assert np.allclose(result, reference, rtol=1e-6, atol=0.0)
    assert result.dtype == np.float32


def test_normalize_does_not_mutate_input_data():
    data = np.array([[2.0, 4.0], [10.0, 20.0]], dtype=np.float32)
    data_copy = data.copy()
    vrange = np.array([2.0, 5.0])

    normalize(data, vrange)

    assert np.array_equal(data, data_copy)
