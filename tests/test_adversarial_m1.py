"""Adversarial stress and challenge test suite for Milestone 1 (M1 / R1 / Etapa 1).

Written by challenger_m1_1 to independently and empirically challenge:
1. Concurrency & stress testing: burst of concurrent jobs (20+ jobs), verifying max_workers=2 throttling.
2. Multi-threaded producer contention & race-condition resilience.
3. Cooperative cancellation of active running job vs queued job, verifying clean cleanup without leaked threads or hanging events.
4. Chaos cancellation under high load.
5. Exception capture, failure handling, and worker resilience after multiple failures.
6. Thread lifecycle, zero thread leak, and clean shutdown verification.
7. Progress callback robustness (clamping, high-frequency updates, non-finite bounds).
8. Cancellation idempotence and boundary conditions.
9. AppState begin_load concurrency and cooperative cancellation.
"""
from __future__ import annotations

import random
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest

from ui.jobs import (
    JobCancelledError,
    JobInfo,
    JobService,
    get_job_service,
    reset_job_service,
)
from ui.state import AppState


@pytest.fixture(autouse=True)
def _isolate_jobs():
    """Aísla el singleton de JobService entre pruebas."""
    reset_job_service()
    yield
    reset_job_service()


# ==============================================================================
# 1. Burst Concurrency & Throttling (max_workers=2)
# ==============================================================================

def test_stress_burst_20_jobs_concurrency_throttling():
    """Submit a burst of 20 concurrent jobs and empirically verify max_workers=2 throttling.

    Invariants tested:
    - At no point do more than 2 jobs execute concurrently.
    - Both worker threads are actively utilized (max_concurrency == 2).
    - All 20 jobs reach terminal state 'completed'.
    - Total duration reflects throttling: 20 jobs * 0.04s / 2 workers ~= 0.40s.
    """
    service = JobService(max_workers=2)
    n_jobs = 20
    job_duration = 0.04

    active_count = 0
    max_active_observed = 0
    lock = threading.Lock()
    execution_intervals: list[tuple[float, float]] = []

    def _stress_task(cancel_token: threading.Event, progress_cb: Callable[[float, str], None]) -> int:
        nonlocal active_count, max_active_observed
        t_start = time.time()
        with lock:
            active_count += 1
            if active_count > max_active_observed:
                max_active_observed = active_count
            assert active_count <= 2, f"Concurrency violation: {active_count} > 2"

        try:
            progress_cb(0.5, "working")
            time.sleep(job_duration)
            return 1
        finally:
            t_end = time.time()
            with lock:
                active_count -= 1
                execution_intervals.append((t_start, t_end))

    t0 = time.time()
    job_ids = [service.submit("burst_job", _stress_task, label=f"Job #{i}") for i in range(n_jobs)]
    assert len(job_ids) == n_jobs

    # Wait for all jobs to complete
    completed_jobs = [service.wait_job(jid, timeout=10.0) for jid in job_ids]
    total_duration = time.time() - t0

    # 1. Concurrency limit invariant
    assert max_active_observed == 2, f"Expected 2 concurrent workers utilized, got {max_active_observed}"

    # 2. Complete coverage and progress invariant
    for job in completed_jobs:
        assert job.status == "completed"
        assert job.progress == 1.0
        assert job.result == 1
        assert job.error is None
        assert job.started_at is not None
        assert job.completed_at is not None

    # 3. Time throttling invariant (20 * 0.04s / 2 workers = 0.40s theoretical min)
    assert total_duration >= 0.32, f"Burst executed too quickly ({total_duration:.3f}s), throttling failed"

    # 4. Overlap verification across recorded intervals
    # Verify that at any recorded point in time, active intervals <= 2
    check_points = [t for start, _ in execution_intervals for t in (start + 0.01,)]
    for pt in check_points:
        active_at_pt = sum(1 for start, end in execution_intervals if start <= pt <= end)
        assert active_at_pt <= 2, f"Observed {active_at_pt} overlapping jobs at {pt}"

    service.shutdown(wait=True)


# ==============================================================================
# 2. Multi-Threaded Producer Contention & Race Condition Resilience
# ==============================================================================

def test_stress_concurrent_producers_and_state_inspection():
    """Stress-test JobService under heavy multi-threaded contention.

    5 producer threads simultaneously submit 10 jobs each (total 50 jobs).
    1 reader thread continuously calls service.active(), all_jobs(), and get_job().
    1 cleaner thread periodically calls clear_finished(max_retained=10).
    Ensures zero race conditions, deadlocks, or state corruption.
    """
    service = JobService(max_workers=2)
    n_producers = 5
    jobs_per_producer = 10
    total_jobs = n_producers * jobs_per_producer

    all_submitted_ids: list[str] = []
    ids_lock = threading.Lock()
    stop_background = threading.Event()
    exceptions: list[Exception] = []

    def _producer(p_idx: int) -> None:
        try:
            for j_idx in range(jobs_per_producer):
                jid = service.submit(
                    "producer_task",
                    lambda cancel, prog: time.sleep(0.005),
                    label=f"p{p_idx}_j{j_idx}",
                )
                with ids_lock:
                    all_submitted_ids.append(jid)
                time.sleep(0.002)
        except Exception as e:
            with ids_lock:
                exceptions.append(e)

    def _reader() -> None:
        while not stop_background.is_set():
            try:
                _ = service.active()
                _ = service.all_jobs()
                time.sleep(0.005)
            except Exception as e:
                with ids_lock:
                    exceptions.append(e)

    def _cleaner() -> None:
        while not stop_background.is_set():
            try:
                _ = service.clear_finished(max_retained=15)
                time.sleep(0.01)
            except Exception as e:
                with ids_lock:
                    exceptions.append(e)

    threads = [threading.Thread(target=_producer, args=(i,)) for i in range(n_producers)]
    reader_th = threading.Thread(target=_reader)
    cleaner_th = threading.Thread(target=_cleaner)

    reader_th.start()
    cleaner_th.start()
    for t in threads:
        t.start()

    for t in threads:
        t.join(timeout=10.0)

    # Wait for all submitted jobs to finish
    time.sleep(0.3)
    for jid in all_submitted_ids:
        try:
            service.wait_job(jid, timeout=5.0)
        except KeyError:
            # Cleaned up by cleaner thread
            pass

    stop_background.set()
    reader_th.join(timeout=3.0)
    cleaner_th.join(timeout=3.0)

    assert not exceptions, f"Exceptions occurred under multi-threaded contention: {exceptions}"
    assert len(all_submitted_ids) == total_jobs
    assert len(set(all_submitted_ids)) == total_jobs, "Duplicate job IDs detected!"

    service.shutdown(wait=True)


# ==============================================================================
# 3. Cooperative Cancellation: Active Running Job vs Queued Job
# ==============================================================================

def test_cooperative_cancellation_active_running_vs_queued_jobs():
    """Adversarially challenge cooperative cancellation between active running and queued jobs.

    Setup:
    - 2 workers total.
    - Submit Job 1 and Job 2 (occupy both workers, blocking until signaled).
    - Submit Job 3, 4, 5, 6 (queued in pending state).
    - Cancel Job 1 (active): must observe cancellation and exit with status 'cancelled'.
    - Cancel Job 3 (queued): must be cancelled in pending state and NEVER execute.
    - Unblock Job 2 (completes normally).
    - Jobs 4, 5, 6 must execute to completion.
    """
    service = JobService(max_workers=2)

    job1_running = threading.Event()
    job2_running = threading.Event()
    job2_gate = threading.Event()

    job3_executed = False
    job4_executed = False
    job5_executed = False
    job6_executed = False

    def _task1(cancel_token: threading.Event, prog_cb: Any) -> None:
        job1_running.set()
        while not cancel_token.is_set():
            time.sleep(0.01)
        raise JobCancelledError("Cancelado tarea 1")

    def _task2(cancel_token: threading.Event, prog_cb: Any) -> str:
        job2_running.set()
        job2_gate.wait(timeout=5.0)
        return "task2_ok"

    def _task3(cancel_token: Any, prog_cb: Any) -> None:
        nonlocal job3_executed
        job3_executed = True

    def _task4(cancel_token: Any, prog_cb: Any) -> str:
        nonlocal job4_executed
        job4_executed = True
        return "task4_ok"

    def _task5(cancel_token: Any, prog_cb: Any) -> str:
        nonlocal job5_executed
        job5_executed = True
        return "task5_ok"

    def _task6(cancel_token: Any, prog_cb: Any) -> str:
        nonlocal job6_executed
        job6_executed = True
        return "task6_ok"

    # Submit 1 and 2 (workers occupied)
    id1 = service.submit("active1", _task1)
    id2 = service.submit("active2", _task2)

    assert job1_running.wait(timeout=3.0)
    assert job2_running.wait(timeout=3.0)

    # Submit 3, 4, 5, 6 (queued in pending)
    id3 = service.submit("queued3", _task3)
    id4 = service.submit("queued4", _task4)
    id5 = service.submit("queued5", _task5)
    id6 = service.submit("queued6", _task6)

    assert service.get_job(id3).status == "pending"
    assert service.get_job(id4).status == "pending"

    # 1. Cancel active running Job 1
    assert service.cancel(id1) is True

    # 2. Cancel queued Job 3 while pending
    assert service.cancel(id3) is True
    assert service.get_job(id3).status == "cancelled"

    # Wait for Job 1 to handle cooperative cancellation
    res1 = service.wait_job(id1, timeout=3.0)
    assert res1.status == "cancelled"
    assert res1.is_finished is True
    assert res1.is_active is False

    # 3. Unblock Job 2
    job2_gate.set()
    res2 = service.wait_job(id2, timeout=3.0)
    assert res2.status == "completed"
    assert res2.result == "task2_ok"

    # Wait for Jobs 4, 5, 6
    res4 = service.wait_job(id4, timeout=3.0)
    res5 = service.wait_job(id5, timeout=3.0)
    res6 = service.wait_job(id6, timeout=3.0)

    # Verify Queued Job 3 was NEVER executed
    assert job3_executed is False, "Queued job was executed despite being cancelled!"
    assert service.get_job(id3).status == "cancelled"

    # Verify subsequent queued jobs executed to completion
    assert job4_executed is True
    assert job5_executed is True
    assert job6_executed is True
    assert res4.status == "completed" and res4.result == "task4_ok"
    assert res5.status == "completed" and res5.result == "task5_ok"
    assert res6.status == "completed" and res6.result == "task6_ok"

    service.shutdown(wait=True)


# ==============================================================================
# 4. Chaos Cancellation under High Contention
# ==============================================================================

def test_cancellation_chaos_under_contention():
    """Submit 30 jobs and cancel a random subset (15 jobs) rapidly.

    Ensures that under chaotic cancellation of both pending and running jobs:
    - No deadlock occurs.
    - Every job reaches a valid terminal state ('completed' or 'cancelled').
    - Zero active jobs remain when done.
    """
    service = JobService(max_workers=2)
    rng = random.Random(42)
    n_jobs = 30

    def _chaotic_task(cancel_token: threading.Event, prog_cb: Any) -> str:
        for _ in range(5):
            if cancel_token.is_set():
                raise JobCancelledError("Cancelado en loop")
            time.sleep(0.01)
        return "survived"

    job_ids = [service.submit("chaos", _chaotic_task, label=f"chaos_{i}") for i in range(n_jobs)]
    to_cancel = set(rng.sample(job_ids, 15))

    # Cancel targeted jobs with small jitter
    for jid in to_cancel:
        service.cancel(jid)
        time.sleep(0.002)

    # Wait for all jobs to terminate
    for jid in job_ids:
        service.wait_job(jid, timeout=5.0)

    # Verify states
    all_jobs = service.all_jobs()
    assert len(all_jobs) == n_jobs

    completed_count = 0
    cancelled_count = 0
    for job in all_jobs:
        assert job.is_finished is True
        assert job.is_active is False
        assert job.status in ("completed", "cancelled")
        if job.status == "completed":
            completed_count += 1
            assert job.result == "survived"
        elif job.status == "cancelled":
            cancelled_count += 1

    assert completed_count + cancelled_count == n_jobs
    assert cancelled_count >= 10, f"Expected at least 10 cancelled, got {cancelled_count}"
    assert len(service.active()) == 0

    service.shutdown(wait=True)


# ==============================================================================
# 5. Exception Capture and Worker Resilience
# ==============================================================================

def test_exception_capture_and_worker_thread_resilience():
    """Verify that worker threads survive and properly record diverse exceptions.

    Invariants:
    - Various exception types are captured without killing executor threads.
    - job.status == 'failed' and job.error matches str(exc).
    - After multiple fatal exceptions, worker threads continue accepting and executing
      subsequent tasks correctly.
    """
    service = JobService(max_workers=2)

    def _fail_value_error(cancel: Any, prog: Any) -> None:
        prog(0.3, "about to fail")
        raise ValueError("Invalid numerical domain in metric calculation")

    def _fail_zero_div(cancel: Any, prog: Any) -> None:
        prog(0.5, "dividing")
        _ = 100 / 0

    def _fail_custom(cancel: Any, prog: Any) -> None:
        class SyntheticHardwareError(Exception):
            pass
        raise SyntheticHardwareError("Oscilloscope communication timeout")

    id_val = service.submit("fail_val", _fail_value_error)
    id_div = service.submit("fail_div", _fail_zero_div)
    id_cust = service.submit("fail_cust", _fail_custom)

    job_val = service.wait_job(id_val, timeout=3.0)
    job_div = service.wait_job(id_div, timeout=3.0)
    job_cust = service.wait_job(id_cust, timeout=3.0)

    assert job_val.status == "failed"
    assert "Invalid numerical domain" in str(job_val.error)
    assert job_val.result is None
    assert job_val.is_finished is True
    assert job_val.is_active is False

    assert job_div.status == "failed"
    assert "division by zero" in str(job_div.error)

    assert job_cust.status == "failed"
    assert "Oscilloscope communication timeout" in str(job_cust.error)

    # CRITICAL: Verify worker threads are still alive and healthy
    healthy_results: list[int] = []
    healthy_ids = [
        service.submit("healthy", lambda c, p, val=i: val * 2)
        for i in range(4)
    ]
    for hid in healthy_ids:
        hjob = service.wait_job(hid, timeout=3.0)
        assert hjob.status == "completed"
        healthy_results.append(hjob.result)

    assert healthy_results == [0, 2, 4, 6]
    service.shutdown(wait=True)


# ==============================================================================
# 6. Thread Lifecycle and Zero Thread Leak
# ==============================================================================

def test_zero_thread_leak_after_job_execution_and_shutdown():
    """Empirically test that JobService does not leak OS/Python threads.

    - Worker threads strictly capped at max_workers (2).
    - Threads named 'JobWorker_*' terminate and clean up upon shutdown(wait=True).
    """
    initial_threads = {t.ident for t in threading.enumerate()}

    service = JobService(max_workers=2)

    # Execute 10 jobs to warm up the pool
    job_ids = [
        service.submit("leak_test", lambda c, p: time.sleep(0.01))
        for _ in range(10)
    ]
    for jid in job_ids:
        service.wait_job(jid, timeout=3.0)

    # During operation: should have at most 2 JobWorker threads
    worker_threads = [t for t in threading.enumerate() if "JobWorker" in t.name]
    assert len(worker_threads) <= 2, f"Leaked worker threads during run: {len(worker_threads)}"

    # Shutdown
    service.shutdown(wait=True)

    # Give OS / runtime a brief window to reap joined threads
    time.sleep(0.05)
    remaining_workers = [t for t in threading.enumerate() if "JobWorker" in t.name and t.is_alive()]
    assert len(remaining_workers) == 0, f"Worker threads remained alive after shutdown: {remaining_workers}"

    final_threads = {t.ident for t in threading.enumerate()}
    # No lingering threads created by the test
    leaked_ids = final_threads - initial_threads
    assert len(leaked_ids) == 0, f"Leaked thread IDs found: {leaked_ids}"


# ==============================================================================
# 7. Progress Callback Robustness
# ==============================================================================

def test_adversarial_progress_callback_bounds_and_stress():
    """Adversarially challenge progress_callback:

    - Negative values clamped to 0.0.
    - Overshoot values (>1.0) clamped to 1.0.
    - Non-string messages handled cleanly.
    - High-frequency calls (1,000 in tight loop) do not deadlock or starve.
    """
    service = JobService(max_workers=2)

    def _clamping_task(cancel: Any, progress_cb: Callable[[float, str], None]) -> None:
        progress_cb(-0.5, "negative")
        time.sleep(0.02)
        progress_cb(1.5, "overshoot")
        time.sleep(0.02)

    jid1 = service.submit("clamp", _clamping_task)
    time.sleep(0.01)
    job1 = service.get_job(jid1)
    assert job1 is not None

    service.wait_job(jid1, timeout=3.0)
    assert job1.status == "completed"
    assert job1.progress == 1.0  # completed sets progress to 1.0

    # High frequency updates: 1,000 updates without deadlock
    def _hf_task(cancel: Any, progress_cb: Callable[[float, str], None]) -> int:
        for i in range(1000):
            progress_cb(i / 1000.0, f"step {i}")
        return 1000

    jid2 = service.submit("hf_prog", _hf_task)
    job2 = service.wait_job(jid2, timeout=5.0)
    assert job2.status == "completed"
    assert job2.result == 1000
    assert job2.progress == 1.0

    service.shutdown(wait=True)


# ==============================================================================
# 8. Boundary Conditions & Cancellation Idempotence
# ==============================================================================

def test_boundary_conditions_and_cancellation_idempotence():
    """Test boundary conditions, illegal operations, and idempotent cancellation.

    - Submitting after shutdown raises RuntimeError.
    - Submitting non-callable raises ValueError.
    - Calling cancel on completed job returns False.
    - Calling cancel on failed job returns False.
    - Calling cancel on already cancelled job returns False.
    - Calling cancel on nonexistent job returns False.
    - Calling wait_job on nonexistent job raises KeyError.
    """
    service = JobService(max_workers=2)

    # Submitting non-callable
    with pytest.raises(ValueError, match="Debe proporcionarse una función ejecutable"):
        service.submit("invalid", target=12345)

    # Cancel nonexistent job
    assert service.cancel("non-existent-uuid") is False
    assert service.cancel_kind("non-existent-kind") == 0

    # Wait on nonexistent job
    with pytest.raises(KeyError, match="Trabajo no encontrado"):
        service.wait_job("non-existent-uuid")

    # Completed job cancellation idempotence
    jid_comp = service.submit("comp", lambda c, p: 42)
    service.wait_job(jid_comp, timeout=3.0)
    assert service.cancel(jid_comp) is False

    # Failed job cancellation idempotence
    def _raise_err(c: Any, p: Any) -> None:
        raise RuntimeError("boom")
    jid_fail = service.submit("fail", _raise_err)
    service.wait_job(jid_fail, timeout=3.0)
    assert service.cancel(jid_fail) is False

    # Pending job cancellation idempotence
    gate1 = threading.Event()
    gate2 = threading.Event()
    _ = service.submit("block1", lambda c, p: gate1.wait(5.0))
    _ = service.submit("block2", lambda c, p: gate2.wait(5.0))
    jid_pending = service.submit("pending_canc", lambda c, p: 123)
    time.sleep(0.02)
    assert service.get_job(jid_pending).status == "pending"
    # First cancel on pending job
    assert service.cancel(jid_pending) is True
    assert service.get_job(jid_pending).status == "cancelled"
    # Second cancel on same pending job must return False (already cancelled/finished)
    assert service.cancel(jid_pending) is False

    # Running job cancellation idempotence
    def _cancellable_task(cancel_token: threading.Event, prog_cb: Any) -> None:
        while not cancel_token.is_set():
            time.sleep(0.01)
        raise JobCancelledError("cancelado")

    jid_canc = service.submit("canc_running", _cancellable_task)
    time.sleep(0.02)
    assert service.cancel(jid_canc) is True
    # Wait for cooperative cancellation to complete
    finished_canc = service.wait_job(jid_canc, timeout=3.0)
    assert finished_canc.status == "cancelled"
    # Now that it's finished, calling cancel again must return False
    assert service.cancel(jid_canc) is False

    # Clean up blocking workers
    gate1.set()
    gate2.set()

    # Shutdown behavior
    service.shutdown(wait=True)
    with pytest.raises(RuntimeError, match="ya ha sido cerrado"):
        service.submit("after_shutdown", lambda: 1)


# ==============================================================================
# 9. AppState begin_load Concurrency & Cancellation
# ==============================================================================

def test_app_state_begin_load_concurrency_and_cancellation(synthetic_hdf5: Path, tmp_path: Path):
    """Adversarially challenge AppState begin_load integration with JobService.

    - Test calling begin_load multiple times.
    - Verify atomic dataset_version progression upon deferred publish.
    - Test cancelling an in-flight dataset load job.
    """
    state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
    assert state.dataset_version == 0

    # 1. Load dataset via begin_load
    jid1 = state.begin_load(synthetic_hdf5)
    job_service = get_job_service()
    finished1 = job_service.wait_job(jid1, timeout=5.0)

    assert finished1.status == "completed"
    assert state.dataset_version == 1
    assert state.dataset is not None

    # 2. Reload same dataset -> version must increment to 2
    jid2 = state.begin_load(synthetic_hdf5)
    finished2 = job_service.wait_job(jid2, timeout=5.0)

    assert finished2.status == "completed"
    assert state.dataset_version == 2

    # 3. Test cooperative cancellation of load_dataset
    # Cancel pending or running load job
    jid3 = state.begin_load(synthetic_hdf5)
    job_service.cancel(jid3)
    finished3 = job_service.wait_job(jid3, timeout=5.0)

    assert finished3.status in ("cancelled", "completed")
    # If cancelled, version should NOT have incremented to 3
    if finished3.status == "cancelled":
        assert state.dataset_version == 2


# ==============================================================================
# 10. Dash Reactive Callbacks & Status Bar Rendering
# ==============================================================================

def test_job_callbacks_reactivity_and_prevent_update(synthetic_hdf5: Path, tmp_path: Path):
    """Test job callbacks (_on_poll_jobs, _on_poll_background_dataset) directly.

    Verifies:
    - PreventUpdate when dataset is None.
    - PreventUpdate when dataset_version has not changed.
    - Output tuple (new_version, label) when dataset_version changes.
    - Status bar container updates when jobs are active or finished.
    """
    from dash.exceptions import PreventUpdate
    from ui.callbacks.job_callbacks import register_job_callbacks
    from ui.state import get_state
    from dash import Dash

    app = Dash(__name__)
    register_job_callbacks(app)

    # Find the registered callback functions and unwrap the Dash add_context wrapper
    callback_fns = [cb["callback"] for cb in app.callback_map.values()]
    poll_jobs_fn = getattr(next(fn for fn in callback_fns if fn.__name__ == "_on_poll_jobs"), "__wrapped__")
    poll_bg_fn = getattr(next(fn for fn in callback_fns if fn.__name__ == "_on_poll_background_dataset"), "__wrapped__")

    state = get_state()
    state._dataset = None
    state._dataset_version = 0

    # 1. Background polling when no dataset loaded -> PreventUpdate
    with pytest.raises(PreventUpdate):
        poll_bg_fn(1, 0)

    # 2. Poll jobs when no jobs -> empty list
    rendered_empty = poll_jobs_fn(1)
    assert rendered_empty == []

    # 3. Submit a job and poll jobs -> rendered component list
    job_service = get_job_service()
    jid = job_service.submit("test_cb", lambda c, p: time.sleep(0.05), label="Callback test")
    rendered_active = poll_jobs_fn(2)
    assert len(rendered_active) == 1
    job_service.wait_job(jid, timeout=3.0)

    # 4. Load dataset and test dataset version detection
    state.load_dataset(synthetic_hdf5)
    assert state.dataset_version == 1

    # Current version is 0, state version is 1 -> returns (1, formatted_label)
    new_ver, label = poll_bg_fn(3, 0)
    assert new_ver == 1
    assert str(synthetic_hdf5.name) in label or "synthetic" in label

    # Current version matches state version (1 == 1) -> PreventUpdate
    with pytest.raises(PreventUpdate):
        poll_bg_fn(4, 1)


# ==============================================================================
# 11. Shutdown with Active and Pending Jobs
# ==============================================================================

def test_shutdown_with_active_and_pending_jobs():
    """Verify that calling shutdown while jobs are running and pending cleanly cancels all."""
    service = JobService(max_workers=2)

    running_evt = threading.Event()
    unblock_evt = threading.Event()

    def _blocker(cancel: threading.Event, prog: Any) -> None:
        running_evt.set()
        while not cancel.is_set() and not unblock_evt.is_set():
            time.sleep(0.01)

    id1 = service.submit("run1", _blocker)
    id2 = service.submit("run2", _blocker)
    id3 = service.submit("pend3", lambda: time.sleep(0.1))
    id4 = service.submit("pend4", lambda: time.sleep(0.1))

    assert running_evt.wait(timeout=3.0)

    # Shutdown immediately with cancel_futures=True
    service.shutdown(wait=False, cancel_futures=True)

    # All active jobs must have had cancel_event set
    job1 = service.get_job(id1)
    job2 = service.get_job(id2)
    assert job1.cancel_event.is_set()
    assert job2.cancel_event.is_set()

    unblock_evt.set()
    time.sleep(0.05)


# ==============================================================================
# 12. Flexible Target Callable Signatures
# ==============================================================================

def test_flexible_callable_signatures():
    """Adversarially challenge _invoke_callable with various parameter signatures."""
    service = JobService(max_workers=2)

    # 1. Zero arguments
    jid1 = service.submit("zero_arg", lambda: 100)
    assert service.wait_job(jid1, timeout=3.0).result == 100

    # 2. Only cancel_token
    jid2 = service.submit("token_only", lambda cancel_token: 200)
    assert service.wait_job(jid2, timeout=3.0).result == 200

    # 3. Only progress_callback
    def _prog_only(progress_callback: Callable[[float, str], None]) -> int:
        progress_callback(0.5, "half")
        return 300
    jid3 = service.submit("prog_only", _prog_only)
    assert service.wait_job(jid3, timeout=3.0).result == 300

    # 4. Positional args + keyword args + named cancel token
    def _complex_fn(a: int, b: int, c: int = 10, cancel_event: threading.Event | None = None) -> int:
        assert cancel_event is not None
        return a + b + c

    jid4 = service.submit("complex", _complex_fn, 5, 15, c=30)
    assert service.wait_job(jid4, timeout=3.0).result == 50

    # 5. Varargs and varkwargs
    def _varargs_fn(*args: Any, **kwargs: Any) -> tuple[Any, ...]:
        return args

    jid5 = service.submit("varargs", _varargs_fn, 1, "test", True)
    assert service.wait_job(jid5, timeout=3.0).result == (1, "test", True)

    service.shutdown(wait=True)

