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
