"""Adversarial stress test harness for Milestone 0 (M0 / R0 / Etapa 0).

Author: challenger_m0_1
Purpose: Empirically stress-test Worker M0 deliverables:
1. Memory footprint during synthetic dataset generation (10 000 - 20 000 signals).
2. Native Win32 memory measurement fidelity, allocation sensitivity, and platform fallbacks.
3. Exception robustness and thread-safety in stage() and record_timing().
"""
from __future__ import annotations

import gc
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

# Ensure project root is in path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmarks.generate_synthetic import generate_synthetic_dataset
from data.storage import CanonicalStore
from utils.profiling import (
    STAGE_CARGA_DATASET,
    STAGE_FILTRO_METRICAS,
    StageRecord,
    get_records,
    is_enabled,
    measure_process_memory,
    profiled,
    record_timing,
    reset_records,
    set_enabled,
    stage,
    stage_job,
    summarize,
)


# =============================================================================
# 1. EMPIRICAL STRESS TEST: MEMORY FOOTPRINT DURING SYNTHETIC GENERATION
# =============================================================================

def test_stress_synthetic_generator_memory_footprint_uhf_and_ae():
    """Generates 10 000 to 20 000 signals in chunks and verifies that resident RAM
    (RSS) is strictly bounded by chunk size and does not scale linearly with total N.
    """
    print("\n--- [TEST 1] Memory Footprint During Synthetic Generation ---")
    tmp_dir = Path(tempfile.mkdtemp(prefix="stress_m0_synthetic_"))

    try:
        # Case A: UHF 10 000 signals, chunk_size = 2500
        # 10 000 * 3000 * 4 = 120,000,000 bytes (114.4 MB raw matrix).
        # Chunk = 2500 * 3000 * 4 = 30,000,000 bytes (28.6 MB).
        uhf_file = tmp_dir / "stress_uhf_10k.hdf5"
        gc.collect()
        rss_start_uhf = measure_process_memory()["rss_bytes"]

        print(f"Generating 10 000 UHF signals (chunk_size=2500)... Start RSS: {rss_start_uhf / (1024*1024):.1f} MB")
        generate_synthetic_dataset(
            output_path=uhf_file,
            n_signals=10_000,
            sensors="UHF",
            chunk_size=2500,
            seed=42,
        )

        gc.collect()
        rss_end_uhf = measure_process_memory()["rss_bytes"]
        rss_peak_uhf = measure_process_memory()["peak_rss_bytes"]
        delta_uhf_mb = (rss_end_uhf - rss_start_uhf) / (1024 * 1024)
        print(f"UHF 10k Done. End RSS: {rss_end_uhf / (1024*1024):.1f} MB, Delta: {delta_uhf_mb:.1f} MB, Peak: {rss_peak_uhf / (1024*1024):.1f} MB")

        # Verify UHF file integrity via CanonicalStore
        assert uhf_file.exists()
        with CanonicalStore(uhf_file) as store:
            assert store.n_signals("UHF") == 10_000
            ts = store.get_all_timestamps("UHF")
            assert ts.shape == (10_000,)
            assert (np.diff(ts) >= 0).all(), "Timestamps must be non-decreasing"
            mm = store.get_all_minmax("UHF")
            assert mm.shape == (10_000, 2)
            assert (mm[:, 0] <= mm[:, 1]).all(), "min must be <= max"
            # Verify random access
            row, t, tr, vr = store.get_signal_row("UHF", 5432)
            assert row.shape == (3000,)
            assert vr == 0.5
            # Block access
            blk = store.get_block("UHF", 100, 200)
            assert blk.data.shape == (100, 3000)

        # Case B: UHF 20 000 signals, chunk_size = 5000
        # 20 000 * 3000 * 4 = 240,000,000 bytes (228.9 MB raw matrix).
        # Chunk = 5000 * 3000 * 4 = 60,000,000 bytes (57.2 MB).
        uhf_file_20k = tmp_dir / "stress_uhf_20k.hdf5"
        gc.collect()
        rss_start_20k = measure_process_memory()["rss_bytes"]

        print(f"Generating 20 000 UHF signals (chunk_size=5000)... Start RSS: {rss_start_20k / (1024*1024):.1f} MB")
        generate_synthetic_dataset(
            output_path=uhf_file_20k,
            n_signals=20_000,
            sensors="UHF",
            chunk_size=5000,
            seed=99,
        )

        gc.collect()
        rss_end_20k = measure_process_memory()["rss_bytes"]
        delta_20k_mb = (rss_end_20k - rss_start_20k) / (1024 * 1024)
        print(f"UHF 20k Done. End RSS: {rss_end_20k / (1024*1024):.1f} MB, Delta: {delta_20k_mb:.1f} MB")

        # Verify UHF 20k integrity
        with CanonicalStore(uhf_file_20k) as store:
            assert store.n_signals("UHF") == 20_000
            ts = store.get_all_timestamps("UHF")
            assert ts.shape == (20_000,)
            assert (np.diff(ts) >= 0).all()

        # Case C: AE 10 000 signals, chunk_size = 2000
        # 10 000 * 10 000 * 4 = 400,000,000 bytes (381.5 MB raw matrix).
        # Chunk = 2000 * 10 000 * 4 = 80,000,000 bytes (76.3 MB).
        # If the generator buffered all 10k signals, RSS would spike by >= 380 MB.
        ae_file = tmp_dir / "stress_ae_10k.hdf5"
        gc.collect()
        rss_start_ae = measure_process_memory()["rss_bytes"]

        print(f"Generating 10 000 AE signals (chunk_size=2000)... Start RSS: {rss_start_ae / (1024*1024):.1f} MB")
        generate_synthetic_dataset(
            output_path=ae_file,
            n_signals=10_000,
            sensors="AE",
            chunk_size=2000,
            seed=77,
        )

        gc.collect()
        rss_end_ae = measure_process_memory()["rss_bytes"]
        delta_ae_mb = (rss_end_ae - rss_start_ae) / (1024 * 1024)
        print(f"AE 10k Done. End RSS: {rss_end_ae / (1024*1024):.1f} MB, Delta: {delta_ae_mb:.1f} MB")

        # Key Invariant Assertion:
        # Delta RSS must be well below the raw uncompressed matrix size (381.5 MB).
        # In bounded chunked streaming, the resident memory increase is <= 1.8x chunk size (~140 MB max with gzip buffers).
        assert delta_ae_mb < 250.0, f"Resident memory leaked or spiked to {delta_ae_mb} MB (expected bounded chunk < 250 MB)"

        # Verify AE file integrity via CanonicalStore
        with CanonicalStore(ae_file) as store:
            assert store.n_signals("AE") == 10_000
            ts = store.get_all_timestamps("AE")
            assert ts.shape == (10_000,)
            assert (np.diff(ts) >= 0).all()
            blk = store.get_block("AE", 50, 100)
            assert blk.data.shape == (50, 10000)

        print("[TEST 1 PASSED] Generator chunking confirmed: resident RAM remains bounded.")

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# =============================================================================
# 2. EMPIRICAL STRESS TEST: NATIVE WIN32 MEMORY MEASUREMENT
# =============================================================================

def test_stress_native_win32_memory_measurement_and_allocations():
    """Verifies that measure_process_memory() produces genuine, positive values,
    matches independent Win32 kernel calls, and accurately captures dynamic allocations.
    """
    print("\n--- [TEST 2] Native Win32 Memory Measurement & Allocation Tracking ---")

    # 1. Plausibility check
    mem0 = measure_process_memory()
    assert isinstance(mem0, dict)
    assert "rss_bytes" in mem0 and "peak_rss_bytes" in mem0
    rss0 = mem0["rss_bytes"]
    peak0 = mem0["peak_rss_bytes"]
    assert rss0 > 10 * 1024 * 1024, f"RSS {rss0} bytes is implausibly low for a running Python runtime"
    assert peak0 >= rss0, f"Peak {peak0} must be >= current RSS {rss0}"

    # 2. Independent validation against direct Win32 API call
    if os.name == "nt":
        import ctypes
        import ctypes.wintypes

        class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.wintypes.DWORD),
                ("PageFaultCount", ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t),
            ]

        # We create an independent ctypes handle or use c_void_p to avoid class collision with k32 cached argtypes
        kernel32_fresh = ctypes.WinDLL("kernel32.dll")
        kernel32_fresh.GetCurrentProcess.restype = ctypes.wintypes.HANDLE
        kernel32_fresh.K32GetProcessMemoryInfo.argtypes = [
            ctypes.wintypes.HANDLE,
            ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
            ctypes.wintypes.DWORD,
        ]
        kernel32_fresh.K32GetProcessMemoryInfo.restype = ctypes.wintypes.BOOL

        proc = kernel32_fresh.GetCurrentProcess()
        counters = PROCESS_MEMORY_COUNTERS_EX()
        counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
        ok = kernel32_fresh.K32GetProcessMemoryInfo(proc, ctypes.byref(counters), counters.cb)
        assert bool(ok)
        independent_rss = int(counters.WorkingSetSize)
        independent_peak = int(counters.PeakWorkingSetSize)

        mem_check = measure_process_memory()
        # RSS can fluctuate by small amounts between calls due to thread activity, but must match closely (< 1 MB)
        diff_rss = abs(mem_check["rss_bytes"] - independent_rss)
        assert diff_rss < 1024 * 1024, f"Discrepancy with direct Win32 API: {diff_rss} bytes"
        print(f"Verified against independent Win32 call: RSS={mem_check['rss_bytes']} vs {independent_rss} (diff={diff_rss} B)")

    # 3. Dynamic Allocation Sensitivity Test: Allocate 80 MB buffer and touch it
    TARGET_BYTES = 80 * 1024 * 1024
    N_FLOATS = TARGET_BYTES // 4

    gc.collect()
    mem_before = measure_process_memory()

    # Allocate and touch memory to ensure Windows commits and pages in the working set
    big_buffer = np.ones(N_FLOATS, dtype=np.float32)
    big_buffer += 1.0  # write access to force page faults into WorkingSet

    mem_during = measure_process_memory()
    allocated_rss_delta = mem_during["rss_bytes"] - mem_before["rss_bytes"]
    allocated_peak_delta = mem_during["peak_rss_bytes"] - mem_before["peak_rss_bytes"]

    print(f"Allocated {TARGET_BYTES / (1024*1024):.1f} MB: RSS delta={allocated_rss_delta / (1024*1024):.1f} MB, Peak delta={allocated_peak_delta / (1024*1024):.1f} MB")

    # Assert that memory measurement captured the allocation (at least 70 MB of the 80 MB committed)
    assert allocated_rss_delta >= 70 * 1024 * 1024, f"Failed to capture allocation: measured delta {allocated_rss_delta / (1024*1024):.1f} MB"
    assert mem_during["peak_rss_bytes"] >= mem_during["rss_bytes"]

    # Release memory and verify peak monotonicity
    del big_buffer
    gc.collect()
    mem_after = measure_process_memory()
    # In Win32, PeakWorkingSetSize is strictly monotonic (never decreases)
    assert mem_after["peak_rss_bytes"] >= mem_during["peak_rss_bytes"], "PeakWorkingSetSize decreased after freeing memory!"

    # 4. Fallback resilience test: simulate POSIX and failure paths
    with patch("os.name", "posix"):
        with patch.dict("sys.modules", {"resource": MagicMock()}):
            import resource
            resource.getrusage.return_value.ru_maxrss = 45000
            posix_mem = measure_process_memory()
            assert posix_mem["rss_bytes"] > 0
            assert posix_mem["peak_rss_bytes"] > 0

    # Simulate Win32 API failure returning False
    with patch("utils.profiling._measure_windows_memory", return_value={"rss_bytes": 0, "peak_rss_bytes": 0}):
        safe_mem = measure_process_memory()
        assert safe_mem == {"rss_bytes": 0, "peak_rss_bytes": 0}

    print("[TEST 2 PASSED] Win32 memory measurement verified: accurate, allocation-sensitive, and resilient.")


# =============================================================================
# 3. EMPIRICAL STRESS TEST: EXCEPTION ROBUSTNESS & EDGE CASES
# =============================================================================

def test_stress_stage_and_record_timing_exception_robustness():
    """Stress-tests stage() and record_timing() against exceptions, track_memory error paths,
    special character formatting, and thread-safety.
    """
    print("\n--- [TEST 3] Exception Robustness in stage() and record_timing() ---")
    set_enabled(True)
    reset_records()

    class CustomCriticalException(BaseException):
        pass

    # 1. Verify standard exception propagation and record fields
    for exc_cls, exc_arg in [
        (ValueError, "invalid argument"),
        (RuntimeError, "kernel failure"),
        (ZeroDivisionError, "division by zero"),
        (KeyError, "missing_key"),
    ]:
        with pytest.raises(exc_cls):
            with stage("failing_stage", test_field="value"):
                raise exc_cls(exc_arg)

        records = get_records("failing_stage")
        assert len(records) > 0
        last_rec = records[-1]
        assert last_rec.fields["error"] == exc_cls.__name__
        assert last_rec.fields["test_field"] == "value"
        assert last_rec.duration_s >= 0.0

    # 2. Verify BaseException (e.g. KeyboardInterrupt, SystemExit, Custom)
    with pytest.raises(CustomCriticalException):
        with stage("critical_stage"):
            raise CustomCriticalException("hard interrupt")

    crit_rec = get_records("critical_stage")[-1]
    assert crit_rec.fields["error"] == "CustomCriticalException"

    # 3. Exception with track_memory=True: memory must still be measured in finally
    reset_records()
    with pytest.raises(RuntimeError):
        with stage("stage_memory_crash", track_memory=True, custom_id=999):
            _ = [i for i in range(100_000)]
            raise RuntimeError("crash after memory allocation")

    crash_recs = get_records("stage_memory_crash")
    assert len(crash_recs) == 1
    cr = crash_recs[0]
    assert cr.fields["error"] == "RuntimeError"
    assert cr.fields["custom_id"] == 999
    assert "rss_bytes" in cr.fields
    assert "peak_rss_bytes" in cr.fields
    assert "rss_delta_bytes" in cr.fields
    assert cr.fields["rss_bytes"] > 0

    # 4. Stressing format_line() with unusual values
    rec_weird = StageRecord(
        name="weird.fields",
        duration_s=0.123,
        fields={
            "empty": "",
            "spaces": "value with spaces",
            "newlines": "line1\nline2",
            "equals": "a=b=c",
            "none_val": None,
            "int_val": 42,
            "float_val": 3.14159,
            "bool_val": True,
        },
    )
    line = rec_weird.format_line()
    assert isinstance(line, str)
    assert "etapa=weird.fields" in line
    assert "duracion_ms=123.0" in line
    assert "int_val=42" in line
    assert "bool_val=True" in line

    # 5. Stressing record_timing() with boundary conditions
    record_timing("empty_meta", 0.05, None)
    record_timing("zero_duration", 0.0, {"tag": "instant"})
    record_timing("negative_duration", -1.0, {"tag": "negative"})
    record_timing("nan_duration", float("nan"), {"tag": "nan"})
    record_timing("inf_duration", float("inf"), {"tag": "inf"})
    record_timing("", 0.01, {"empty_name": True})

    # Mutation safety: caller modifies dict after record_timing
    original_dict = {"original_key": "original_val"}
    rec_isolated = record_timing("isolation_test", 0.02, original_dict)
    original_dict["original_key"] = "tampered_val"
    original_dict["new_key"] = "injected"
    assert rec_isolated.fields["original_key"] == "original_val"
    assert "new_key" not in rec_isolated.fields

    # 6. Summarize edge cases
    resumen = summarize()
    assert isinstance(resumen, dict)
    assert "empty_meta" in resumen
    assert resumen["empty_meta"]["n"] == 1.0

    # Summarize with empty list
    assert summarize([]) == {}

    # 7. Multi-threaded stress: verify collector thread safety
    reset_records()
    N_THREADS = 10
    N_PER_THREAD = 100

    def worker_thread(tid: int):
        for i in range(N_PER_THREAD):
            with stage(f"thread_stage_{tid}", track_memory=False, iter=i):
                time.sleep(0.0001)
            record_timing("thread_record", 0.001, {"tid": tid, "i": i})

    threads = [threading.Thread(target=worker_thread, args=(t,)) for t in range(N_THREADS)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    all_thread_records = get_records()
    expected_total = N_THREADS * N_PER_THREAD * 2
    assert len(all_thread_records) == expected_total, f"Expected {expected_total} records, got {len(all_thread_records)}"

    print("[TEST 3 PASSED] Exception robustness, boundary handling, and thread-safety confirmed.")


if __name__ == "__main__":
    print("=== STARTING ADVERSARIAL STRESS TEST SUITE FOR MILESTONE 0 ===")
    test_stress_native_win32_memory_measurement_and_allocations()
    test_stress_stage_and_record_timing_exception_robustness()
    test_stress_synthetic_generator_memory_footprint_uhf_and_ae()
    print("\n=== ALL ADVERSARIAL STRESS TESTS COMPLETED SUCCESSFULLY (100% PASS) ===")
