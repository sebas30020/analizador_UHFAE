import numpy as np

from metrics.spectral import compute_spectrum


def test_pure_sine_peaks_near_its_own_frequency():
    fs = 10_000.0
    f0 = 500.0
    n = 2000
    t = np.arange(n) / fs
    signal = np.sin(2 * np.pi * f0 * t)

    result = compute_spectrum(signal[np.newaxis, :], fs_hz=fs, freq_limit_hz=fs / 2)
    peak_freq = result.freqs_hz[np.argmax(result.mag2[0])]
    assert abs(peak_freq - f0) <= result.freqs_hz[1] - result.freqs_hz[0]  # dentro de 1 bin


def test_freq_limit_clips_output_range():
    fs = 10_000.0
    n = 1000
    signal = np.random.default_rng(0).normal(size=(3, n))
    limit = 1000.0
    result = compute_spectrum(signal, fs_hz=fs, freq_limit_hz=limit)
    assert result.freqs_hz.max() <= limit
    assert result.mag2.shape == (3, result.freqs_hz.shape[0])


def test_vectorized_matches_per_row_loop():
    fs = 5_000.0
    n = 512
    rng = np.random.default_rng(1)
    matrix = rng.normal(size=(4, n))

    batched = compute_spectrum(matrix, fs_hz=fs, freq_limit_hz=fs / 2)
    for i in range(4):
        single = compute_spectrum(matrix[i][np.newaxis, :], fs_hz=fs, freq_limit_hz=fs / 2)
        assert np.allclose(batched.mag2[i], single.mag2[0])


def test_spectral_truncation_exact_mathematical_equivalence():
    import scipy.fft as sfft

    fs = 20_000.0
    n = 1024
    rng = np.random.default_rng(42)
    matrix = rng.normal(size=(5, n))
    freq_limit = 3500.0

    result = compute_spectrum(matrix, fs_hz=fs, freq_limit_hz=freq_limit)

    # Reference calculation using old abs() ** 2 and full mask
    full_fft = sfft.rfft(matrix, axis=-1)
    full_mag2 = np.abs(full_fft) ** 2
    full_freqs = sfft.rfftfreq(n, d=1.0 / fs)
    mask = full_freqs <= freq_limit

    np.testing.assert_array_equal(result.freqs_hz, full_freqs[mask])
    np.testing.assert_allclose(result.mag2, full_mag2[..., mask], rtol=1e-12, atol=1e-12)


def test_spectral_truncation_edge_cases():
    fs = 10_000.0
    n = 200
    signal = np.ones((2, n))

    # freq_limit_hz >= Nyquist returns all frequencies
    res_all = compute_spectrum(signal, fs_hz=fs, freq_limit_hz=fs)
    assert res_all.freqs_hz.shape[0] == n // 2 + 1

    # freq_limit_hz < 0 returns empty frequencies
    res_empty = compute_spectrum(signal, fs_hz=fs, freq_limit_hz=-1.0)
    assert res_empty.freqs_hz.shape[0] == 0
    assert res_empty.mag2.shape == (2, 0)
