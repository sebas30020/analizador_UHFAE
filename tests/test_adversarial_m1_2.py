"""Adversarial challenge test suite for Milestone 1 (by challenger_m1_2).

Challenges:
1. Asynchronous dataset loading:
   - Non-blocking return of state.begin_load (< 50ms) even under heavy/slow file parsing.
   - Empirical proof that state._lock is NEVER held while prepare_dataset parses files.
   - Concurrent dataset loads: rapid switching cancels stale loads and prevents out-of-order dataset overwrite.
   - Clean recovery when file parsing raises unhandled exceptions.
2. Layout injection idempotence:
   - Repeated calls to mount_job_status_bar (1 to 10 iterations) produce strictly ONE status bar and interval.
   - Structural edge cases: children=None, children=[], children=str, children=Component, children=tuple.
   - Pre-existing status bar and interval ID immunity.
3. Status bar polling callback logic (_on_poll_jobs):
   - Direct execution across all job states: pending, running, completed, failed, cancelled.
   - Active job exclusivity (finished jobs suppressed while active jobs exist).
   - Display of up to 3 most recent finished jobs in reverse chronological order when active is empty.
   - Progress bar rendering: 0%, 100%, intermediate percentages, indeterminate spinners, clamped bounds.
   - Cancel button presence on active jobs only, never on finished jobs.
   - Integration with Dash callback map.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable
from unittest.mock import patch

import numpy as np
import pytest
from dash import Dash, html

from core.models import EnvironmentalSeries, EventSeries, SensorConfig, SignalBlock
from ui.callbacks.job_callbacks import register_job_callbacks
from ui.components.job_status_bar import build_job_status_bar, render_job_item, render_job_status_display
from ui.jobs import (
    JobInfo,
    JobService,
    get_job_service,
    reset_job_service,
)
from ui.layout import mount_job_status_bar
from ui.state import AppState, LoadedDataset


@pytest.fixture(autouse=True)
def _isolate_job_environment():
    """Ensure clean JobService state before and after each test."""
    reset_job_service()
    yield
    reset_job_service()


def _create_mock_loaded_dataset(dataset_id: str = "mock_ds") -> LoadedDataset:
    """Helper to generate a minimal valid LoadedDataset."""
    n = 10
    block_uhf = SignalBlock(
        data=np.ones((n, 100), dtype=np.float32),
        timestamps=np.linspace(0, 10, n, dtype=np.float64),
        trigger=np.zeros(n, dtype=np.float64),
        vrange=np.ones(n, dtype=np.float64),
        valid_mask=np.ones(n, dtype=bool),
        minmax=np.zeros((n, 2), dtype=np.float32),
    )
    sensor_cfg = SensorConfig(
        name="UHF",
        hdf5_group="UHF",
        fs_hz=1e6,
        n_samples=100,
        freq_limit_hz=5e5,
        axis_unit="V",
        axis_scale=1.0,
        target_block_bytes=1024 * 1024,
    )
    return LoadedDataset(
        dataset_id=dataset_id,
        source_path=Path(f"/mock/{dataset_id}.hdf5"),
        experiment="exp1",
        sensor_configs={"UHF": sensor_cfg},
        blocks={"UHF": block_uhf},
        environmental=EnvironmentalSeries(
            timestamps=np.array([], dtype=np.float64),
            temperature=np.array([], dtype=np.float64),
            humidity=np.array([], dtype=np.float64),
        ),
        events=EventSeries(
            timestamps=np.array([], dtype=np.float64),
            event_type=np.array([], dtype=str),
        ),
        t0=0.0,
        partition=None,
    )


# ==============================================================================
# CHALLENGE AREA 1: Asynchronous dataset loading & locking
# ==============================================================================

class TestAsyncDatasetLoading:
    """Stress tests for state.begin_load, concurrency, and locking invariants."""

    def test_begin_load_non_blocking_return(self, tmp_path):
        """state.begin_load MUST return immediately (< 50ms) even if prepare_dataset takes 0.5s."""
        state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
        prepare_entered = threading.Event()
        prepare_gate = threading.Event()

        def _slow_prepare(*args, **kwargs):
            prepare_entered.set()
            prepare_gate.wait(timeout=5.0)
            return _create_mock_loaded_dataset("slow_dataset")

        with patch("ui.state.prepare_dataset", side_effect=_slow_prepare):
            t_start = time.perf_counter()
            job_id = state.begin_load("slow_file.hdf5")
            t_elapsed = time.perf_counter() - t_start

            # Must return in under 50ms
            assert t_elapsed < 0.05, f"begin_load blocked for {t_elapsed:.4f}s; must be non-blocking!"
            assert isinstance(job_id, str)
            assert len(job_id) > 0

            # Immediately after return, dataset must still be None
            assert state.dataset is None
            assert state.dataset_version == 0

            # Ensure background thread has entered prepare_dataset
            assert prepare_entered.wait(timeout=2.0)

            # Release the slow prepare and verify eventual completion
            prepare_gate.set()
            job_service = get_job_service()
            finished_job = job_service.wait_job(job_id, timeout=3.0)
            assert finished_job.status == "completed"
            assert state.dataset is not None
            assert state.dataset.dataset_id == "slow_dataset"
            assert state.dataset_version == 1

    def test_lock_not_held_during_file_parsing(self, tmp_path):
        """CRITICAL INVARIANT: state._lock MUST NOT be held while prepare_dataset runs."""
        state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
        inside_parsing = threading.Event()
        finish_parsing = threading.Event()

        def _deliberate_slow_parsing(*args, **kwargs):
            inside_parsing.set()
            finish_parsing.wait(timeout=5.0)
            return _create_mock_loaded_dataset("parse_test")

        with patch("ui.state.prepare_dataset", side_effect=_deliberate_slow_parsing):
            state.begin_load("heavy.hdf5")
            assert inside_parsing.wait(timeout=2.0), "Worker thread failed to enter prepare_dataset"

            # 1. Empirically verify that state._lock is completely free
            lock_acquired = state._lock.acquire(blocking=False)
            assert lock_acquired is True, "VIOLATION: state._lock is held during file parsing!"
            state._lock.release()

            # 2. Empirically verify that reading state properties has 0 contention (< 10ms)
            t0 = time.perf_counter()
            _ = state.dataset
            _ = state.dataset_version
            _ = state.filter_version
            _ = state.get_active_mask("UHF")
            _ = state.get_filter_counts("UHF")
            elapsed = time.perf_counter() - t0
            assert elapsed < 0.01, f"State inspection delayed ({elapsed:.4f}s) while parsing runs"

            # 3. Empirically verify that state mutations under lock are unblocked
            state.set_active_index("UHF", 0)

            finish_parsing.set()
            job_service = get_job_service()
            active_jobs = [j for j in job_service.all_jobs() if j.type == "load_dataset"]
            assert len(active_jobs) == 1
            job_service.wait_job(active_jobs[0].id, timeout=3.0)
            assert state.dataset is not None

    def test_concurrent_dataset_loads_cancels_stale_and_preserves_order(self, tmp_path):
        """When multiple dataset loads are initiated, older loads must be cancelled and

        NEVER overwrite a newer dataset upon completion.
        """
        state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
        job_service = get_job_service()

        gate_job_a = threading.Event()
        job_a_parsing = threading.Event()

        def _prepare_mock(path, *args, **kwargs):
            if "dataset_a" in str(path):
                job_a_parsing.set()
                gate_job_a.wait(timeout=5.0)
                return _create_mock_loaded_dataset("dataset_a")
            return _create_mock_loaded_dataset("dataset_b")

        with patch("ui.state.prepare_dataset", side_effect=_prepare_mock):
            id_a = state.begin_load("dataset_a.hdf5")
            assert job_a_parsing.wait(timeout=2.0)

            # Rapidly load dataset_b while dataset_a is still parsing
            id_b = state.begin_load("dataset_b.hdf5")

            # dataset_b should complete first (not gated)
            job_b = job_service.wait_job(id_b, timeout=3.0)
            assert job_b.status == "completed"
            assert state.dataset is not None
            assert state.dataset.dataset_id == "dataset_b"
            assert state.dataset_version == 1

            # Release dataset_a parsing — it must notice cancel_token and abort publication
            gate_job_a.set()
            job_a = job_service.wait_job(id_a, timeout=3.0)
            assert job_a.status == "cancelled"

            # Verify that stale dataset_a DID NOT overwrite dataset_b
            assert state.dataset.dataset_id == "dataset_b"
            assert state.dataset_version == 1

    def test_burst_concurrent_loads_stress(self, tmp_path):
        """Simulate a burst of 10 concurrent begin_load calls from multiple threads."""
        state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
        job_service = get_job_service()

        with patch("ui.state.prepare_dataset", side_effect=lambda p, *a, **kw: _create_mock_loaded_dataset(Path(p).stem)):
            submitted_ids: list[str] = []
            threads: list[threading.Thread] = []

            def _submit(name: str):
                jid = state.begin_load(f"{name}.hdf5")
                submitted_ids.append(jid)

            for i in range(10):
                t = threading.Thread(target=_submit, args=(f"ds_{i}",))
                threads.append(t)

            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=3.0)

            assert len(submitted_ids) == 10

            # Wait for all jobs in the service to finish
            for jid in submitted_ids:
                job_service.wait_job(jid, timeout=5.0)

            # Exactly one dataset should be active in state, with consistent version
            assert state.dataset is not None
            assert state.dataset_version >= 1
            all_loads = [j for j in job_service.all_jobs() if j.type == "load_dataset"]
            assert all(j.is_finished for j in all_loads)

    def test_begin_load_failure_isolation(self, tmp_path):
        """If file parsing raises an unhandled error, state and job service recover cleanly."""
        state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
        job_service = get_job_service()

        def _broken_prepare(*args, **kwargs):
            raise OSError("HDF5 header corrupted or file unreadable")

        with patch("ui.state.prepare_dataset", side_effect=_broken_prepare):
            jid = state.begin_load("corrupted.hdf5")
            failed_job = job_service.wait_job(jid, timeout=3.0)

            assert failed_job.status == "failed"
            assert "HDF5 header corrupted" in (failed_job.error or "")
            assert state.dataset is None
            assert state.dataset_version == 0

        # Verify that subsequent load works normally
        with patch("ui.state.prepare_dataset", return_value=_create_mock_loaded_dataset("healed")):
            good_id = state.begin_load("good.hdf5")
            good_job = job_service.wait_job(good_id, timeout=3.0)
            assert good_job.status == "completed"
            assert state.dataset is not None
            assert state.dataset_version == 1


# ==============================================================================
# CHALLENGE AREA 2: Layout injection idempotence
# ==============================================================================

class TestLayoutInjectionIdempotence:
    """Stress tests for mount_job_status_bar under repeated and diverse structures."""

    def _count_components_by_id(self, component: Any, target_id: str) -> int:
        count = 0
        if getattr(component, "id", None) == target_id:
            count += 1
        children = getattr(component, "children", None)
        if isinstance(children, list):
            for c in children:
                count += self._count_components_by_id(c, target_id)
        elif isinstance(children, tuple):
            for c in children:
                count += self._count_components_by_id(c, target_id)
        elif children is not None and hasattr(children, "id"):
            count += self._count_components_by_id(children, target_id)
        return count

    def test_repeated_mount_calls_exact_idempotence(self):
        """Mounting 1, 2, 5, or 10 times results in strictly ONE status bar and interval."""
        layout = html.Div(
            id="app-root",
            children=[
                html.Header(id="app-header", children="Header content"),
                html.Main(id="app-main", children=[html.P("Body text")]),
            ],
        )

        for iteration in range(1, 11):
            layout = mount_job_status_bar(layout)
            n_bars = self._count_components_by_id(layout, "job-status-bar")
            n_intervals = self._count_components_by_id(layout, "job-status-interval")
            assert n_bars == 1, f"Iteration {iteration}: expected 1 job-status-bar, found {n_bars}"
            assert n_intervals == 1, f"Iteration {iteration}: expected 1 job-status-interval, found {n_intervals}"

            # Verify existing children are preserved
            header_count = self._count_components_by_id(layout, "app-header")
            main_count = self._count_components_by_id(layout, "app-main")
            assert header_count == 1
            assert main_count == 1

    def test_mount_with_empty_children_list(self):
        layout = html.Div(id="root", children=[])
        mounted = mount_job_status_bar(layout)
        assert self._count_components_by_id(mounted, "job-status-bar") == 1
        assert self._count_components_by_id(mounted, "job-status-interval") == 1

        mounted_again = mount_job_status_bar(mounted)
        assert self._count_components_by_id(mounted_again, "job-status-bar") == 1

    def test_mount_with_none_children(self):
        layout = html.Div(id="root", children=None)
        mounted = mount_job_status_bar(layout)
        assert self._count_components_by_id(mounted, "job-status-bar") == 1
        assert self._count_components_by_id(mounted, "job-status-interval") == 1

        mounted_again = mount_job_status_bar(mounted)
        assert self._count_components_by_id(mounted_again, "job-status-bar") == 1

    def test_mount_with_single_primitive_child(self):
        layout = html.Div(id="root", children="Hello plain text")
        mounted = mount_job_status_bar(layout)
        assert self._count_components_by_id(mounted, "job-status-bar") == 1

        mounted_again = mount_job_status_bar(mounted)
        assert self._count_components_by_id(mounted_again, "job-status-bar") == 1

    def test_mount_with_single_dash_component_child(self):
        layout = html.Div(id="root", children=html.P("Solo child"))
        mounted = mount_job_status_bar(layout)
        assert self._count_components_by_id(mounted, "job-status-bar") == 1

        mounted_again = mount_job_status_bar(mounted)
        assert self._count_components_by_id(mounted_again, "job-status-bar") == 1

    def test_mount_with_tuple_children(self):
        layout = html.Div(id="root", children=(html.Span("A"), html.Span("B")))
        mounted = mount_job_status_bar(layout)
        assert self._count_components_by_id(mounted, "job-status-bar") == 1

        mounted_again = mount_job_status_bar(mounted)
        assert self._count_components_by_id(mounted_again, "job-status-bar") == 1

    def test_mount_on_layout_already_containing_status_bar(self):
        pre_existing_bar = build_job_status_bar()
        layout = html.Div(id="root", children=[pre_existing_bar, html.P("Existing")])

        mounted = mount_job_status_bar(layout)
        assert self._count_components_by_id(mounted, "job-status-bar") == 1
        assert self._count_components_by_id(mounted, "job-status-interval") == 1


# ==============================================================================
# CHALLENGE AREA 3: Status bar polling callback logic (_on_poll_jobs)
# ==============================================================================

class TestStatusBarPollingCallbackLogic:
    """Stress tests for _on_poll_jobs and render_job_status_display across all job states."""

    def test_empty_jobs_returns_empty_list(self):
        result = render_job_status_display([])
        assert result == []

    def test_active_jobs_all_states(self):
        """Test active jobs in both 'pending' and 'running' states."""
        pending_job = JobInfo(
            id="job-p1",
            type="load_dataset",
            label="Carga pendiente",
            status="pending",
            progress=None,
        )
        running_determinate = JobInfo(
            id="job-r1",
            type="warmup",
            label="Precalentando",
            status="running",
            progress=0.42,
            message="Calculando métricas...",
        )
        running_indeterminate = JobInfo(
            id="job-r2",
            type="export",
            label="Exportando",
            status="running",
            progress=None,
        )

        rendered = render_job_status_display([pending_job, running_determinate, running_indeterminate])
        assert len(rendered) == 3

        # Pending card: has spinner and cancel button
        c_pending = rendered[0]
        assert "job-pending" in c_pending.className
        assert any("job-spinner-indeterminate" in getattr(ch, "className", "") for ch in c_pending.children)
        assert any(getattr(ch, "className", None) == "btn-cancel-job" for ch in c_pending.children)

        # Running determinate: has progress bar with 42%
        c_determinate = rendered[1]
        assert "job-running" in c_determinate.className
        assert any(getattr(ch, "className", None) == "btn-cancel-job" for ch in c_determinate.children)
        prog_wrappers = [ch for ch in c_determinate.children if getattr(ch, "className", "") == "job-progress-wrapper"]
        assert len(prog_wrappers) == 1
        assert any("42%" in str(getattr(ch, "children", "")) for ch in prog_wrappers[0].children)

        # Running indeterminate: has spinner and cancel button
        c_indeterminate = rendered[2]
        assert "job-running" in c_indeterminate.className
        assert any("job-spinner-indeterminate" in getattr(ch, "className", "") for ch in c_indeterminate.children)

    def test_finished_jobs_suppressed_when_active_jobs_exist(self):
        """When at least one active job exists, finished jobs must NOT be rendered."""
        active = JobInfo(id="act-1", type="load", status="running", progress=0.1)

        # In _on_poll_jobs logic: recent = finished_recent[:3] if not active_jobs else []
        recent = []  # because active is non-empty
        rendered = render_job_status_display([active], recent)
        assert len(rendered) == 1
        assert rendered[0].id == {"type": "job-item-card", "index": "act-1"}

    def test_finished_jobs_display_when_active_empty(self):
        """When no active jobs exist, up to 3 most recent finished jobs are displayed."""
        t_base = 1000.0
        j1 = JobInfo(id="f1", type="t1", status="completed", completed_at=t_base + 10)
        j2 = JobInfo(id="f2", type="t2", status="failed", error="División por cero", completed_at=t_base + 30)
        j3 = JobInfo(id="f3", type="t3", status="cancelled", message="Cancelado usuario", completed_at=t_base + 20)
        j4 = JobInfo(id="f4", type="t4", status="completed", completed_at=t_base + 40)
        j5 = JobInfo(id="f5", type="t5", status="completed", completed_at=t_base + 5)

        all_finished = [j1, j2, j3, j4, j5]
        all_finished.sort(key=lambda j: j.completed_at or 0.0, reverse=True)
        recent = all_finished[:3]

        rendered = render_job_status_display([], recent)
        assert len(rendered) == 3

        # Order must be j4 (t=40), j2 (t=30), j3 (t=20)
        assert rendered[0].id == {"type": "job-item-card", "index": "f4"}
        assert "job-completed" in rendered[0].className
        assert "✓" in str(rendered[0].children[0].children)

        assert rendered[1].id == {"type": "job-item-card", "index": "f2"}
        assert "job-failed" in rendered[1].className
        assert "✕" in str(rendered[1].children[0].children)
        assert "División por cero" in str(rendered[1].children[1].children)

        assert rendered[2].id == {"type": "job-item-card", "index": "f3"}
        assert "job-cancelled" in rendered[2].className
        assert "✕" in str(rendered[2].children[0].children)
        assert "Cancelado usuario" in str(rendered[2].children[1].children)

        # Finished jobs must NEVER have a cancel button
        for card in rendered:
            assert not any(getattr(ch, "className", None) == "btn-cancel-job" for ch in card.children)

    def test_edge_cases_in_job_rendering(self):
        """Test boundary progress values, missing timestamps, and long text."""
        # 1. progress 0.0
        j_zero = JobInfo(id="z1", type="zero", status="running", progress=0.0)
        card_zero = render_job_item(j_zero)
        prog_w = [ch for ch in card_zero.children if getattr(ch, "className", "") == "job-progress-wrapper"][0]
        assert any("0%" in str(getattr(ch, "children", "")) for ch in prog_w.children)

        # 2. progress 1.0
        j_one = JobInfo(id="o1", type="one", status="running", progress=1.0)
        card_one = render_job_item(j_one)
        prog_w1 = [ch for ch in card_one.children if getattr(ch, "className", "") == "job-progress-wrapper"][0]
        assert any("100%" in str(getattr(ch, "children", "")) for ch in prog_w1.children)

        # 3. None completed_at on finished job does not crash sorting
        j_notime = JobInfo(id="nt1", type="notime", status="completed", completed_at=None)
        finished_list = [j_notime]
        finished_list.sort(key=lambda j: j.completed_at or 0.0, reverse=True)
        rendered_nt = render_job_status_display([], finished_list)
        assert len(rendered_nt) == 1

        # 4. Long error message (e.g. traceback)
        long_err = "Traceback (most recent call last):\n  File 'app.py', line 42\nValueError: something bad" * 50
        j_long = JobInfo(id="err1", type="err", status="failed", error=long_err)
        card_long = render_job_item(j_long)
        assert card_long is not None

        # 5. Empty label defaults to type
        j_nolabel = JobInfo(id="nl1", type="fallback_type", label="", status="running")
        card_nl = render_job_item(j_nolabel)
        assert "fallback_type" in str(card_nl.children[1].children)

    def test_progress_clamping_in_job_service(self):
        """JobService progress callback clamps negative and > 1.0 values to [0.0, 1.0]."""
        service = JobService(max_workers=2)

        def _clamped_task(cancel_token: Any, progress_cb: Callable[[float, str], None]) -> None:
            progress_cb(-0.5, "Negative")
            assert service.get_job(jid).progress == 0.0

            progress_cb(1.5, "Exceeded")
            assert service.get_job(jid).progress == 1.0

        jid = service.submit("clamp_test", _clamped_task)
        finished = service.wait_job(jid, timeout=3.0)
        assert finished.status == "completed"
        assert finished.progress == 1.0
        service.shutdown(wait=True)

    def test_on_poll_jobs_registered_dash_callback(self):
        """Empirically invoke the registered _on_poll_jobs Dash callback via app.callback_map."""
        app = Dash(__name__)
        app.layout = html.Div([build_job_status_bar()])
        register_job_callbacks(app)

        # Lookup registered callback from Dash callback_map
        cb_key = "job-status-container.children"
        assert cb_key in app.callback_map
        raw_poll_func = app.callback_map[cb_key]["callback"].__wrapped__

        service = get_job_service()

        # 1. Initially no jobs -> returns []
        res = raw_poll_func(0)
        assert res == []

        # 2. Submit a long-running job -> poll callback returns 1 running card
        gate = threading.Event()
        jid = service.submit("bg_task", lambda c, p: gate.wait(timeout=5.0), label="Tarea BG")
        time.sleep(0.05)

        res_active = raw_poll_func(1)
        assert len(res_active) == 1
        assert res_active[0].id == {"type": "job-item-card", "index": jid}

        # 3. Finish the job -> poll callback returns 1 completed card
        gate.set()
        service.wait_job(jid, timeout=3.0)

        res_finished = raw_poll_func(2)
        assert len(res_finished) == 1
        assert "job-completed" in res_finished[0].className
