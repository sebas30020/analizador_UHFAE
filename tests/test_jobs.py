"""Pruebas unitarias para el Servicio de Trabajos en Segundo Plano (R1 / Etapa 1).

Verifica ciclo de vida de trabajos, concurrencia limitada a 2 workers, cancelación
cooperativa, cancelación antes de iniciar, cancelación por tipo, manejo de errores,
reporte de progreso, renderizado de la barra de estado y montaje en layout, así como
la integración con AppState (begin_load, deferred publish y warmup).
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pytest
from dash import html

from ui.components.job_status_bar import build_job_status_bar, render_job_status_display
from ui.jobs import (
    JobCancelledError,
    JobInfo,
    JobService,
    get_job_service,
    reset_job_service,
)
from ui.layout import mount_job_status_bar
from ui.state import AppState


@pytest.fixture(autouse=True)
def _reset_jobs():
    """Aísla el singleton de JobService entre pruebas."""
    reset_job_service()
    yield
    reset_job_service()


def test_job_lifecycle_completed():
    service = JobService(max_workers=2)

    def _task(cancel_token: threading.Event, progress_cb: Callable[[float, str], None]) -> int:
        progress_cb(0.5, "A mitad de camino")
        time.sleep(0.05)
        return 42

    job_id = service.submit("calc", _task, label="Cálculo de prueba")
    assert isinstance(job_id, str)

    job = service.status(job_id)
    assert job is not None
    assert job.id == job_id
    assert job.type == "calc"
    assert job.label == "Cálculo de prueba"

    finished_job = service.wait_job(job_id, timeout=5.0)
    assert finished_job.status == "completed"
    assert finished_job.is_completed if hasattr(finished_job, "is_completed") else finished_job.status == "completed"
    assert finished_job.is_finished
    assert not finished_job.is_active
    assert finished_job.result == 42
    assert finished_job.progress == 1.0
    assert finished_job.started_at is not None
    assert finished_job.completed_at is not None
    assert finished_job.duration_s is not None and finished_job.duration_s >= 0.0
    assert finished_job.error is None
    service.shutdown(wait=True)


def test_job_concurrency_max_workers_2():
    service = JobService(max_workers=2)
    started_count = 0
    lock = threading.Lock()
    gate = threading.Event()
    all_started = threading.Event()

    def _worker(cancel_token: threading.Event, progress_cb: Any) -> str:
        nonlocal started_count
        with lock:
            started_count += 1
            if started_count == 2:
                all_started.set()
        gate.wait(timeout=5.0)
        return "ok"

    id1 = service.submit("task1", _worker)
    id2 = service.submit("task2", _worker)
    id3 = service.submit("task3", _worker)

    # Esperar a que exactamente 2 hilos hayan arrancado
    assert all_started.wait(timeout=3.0)
    time.sleep(0.05)

    # El 3er trabajo debe seguir pending en la cola
    job3 = service.get_job(id3)
    assert job3 is not None
    assert job3.status == "pending"

    # Liberar el bloqueo para que terminen los 2 primeros y corra el 3ero
    gate.set()
    res1 = service.wait_job(id1, timeout=5.0)
    res2 = service.wait_job(id2, timeout=5.0)
    res3 = service.wait_job(id3, timeout=5.0)

    assert res1.status == "completed"
    assert res2.status == "completed"
    assert res3.status == "completed"
    service.shutdown(wait=True)


def test_job_cooperative_cancellation_running():
    service = JobService(max_workers=2)
    task_running = threading.Event()

    def _cancellable_task(cancel_token: threading.Event, progress_cb: Any) -> None:
        task_running.set()
        while not cancel_token.is_set():
            time.sleep(0.02)
        raise JobCancelledError("Cancelado por solicitud del usuario")

    job_id = service.submit("long_running", _cancellable_task)
    assert task_running.wait(timeout=3.0)

    # Señalar cancelación
    assert service.cancel(job_id) is True

    finished = service.wait_job(job_id, timeout=5.0)
    assert finished.status == "cancelled"
    assert finished.is_finished
    assert "Cancelado" in finished.message
    service.shutdown(wait=True)


def test_job_cancel_pending():
    service = JobService(max_workers=2)
    gate = threading.Event()
    task3_executed = False

    def _blocking(cancel_token: Any, progress_cb: Any) -> None:
        gate.wait(timeout=5.0)

    def _task3(cancel_token: Any, progress_cb: Any) -> None:
        nonlocal task3_executed
        task3_executed = True

    id1 = service.submit("b1", _blocking)
    id2 = service.submit("b2", _blocking)
    id3 = service.submit("p3", _task3)

    time.sleep(0.05)
    job3 = service.get_job(id3)
    assert job3 is not None and job3.status == "pending"

    # Cancelar el 3ero mientras está pendiente
    assert service.cancel(id3) is True
    assert service.get_job(id3).status == "cancelled"

    # Liberar workers
    gate.set()
    service.wait_job(id1, timeout=3.0)
    service.wait_job(id2, timeout=3.0)
    time.sleep(0.05)

    assert task3_executed is False
    service.shutdown(wait=True)


def test_cancel_kind():
    service = JobService(max_workers=2)
    gate = threading.Event()

    def _job_fn(cancel_token: threading.Event, progress_cb: Any) -> None:
        while not cancel_token.is_set():
            if gate.is_set():
                break
            time.sleep(0.02)
        if cancel_token.is_set():
            raise JobCancelledError("Cancelado por tipo")

    w1 = service.submit("warmup", _job_fn, label="Warmup 1")
    w2 = service.submit("warmup", _job_fn, label="Warmup 2")
    m1 = service.submit("metric_calc", _job_fn, label="Métrica 1")

    time.sleep(0.05)
    cancelled_count = service.cancel_kind("warmup")
    assert cancelled_count >= 1

    gate.set()
    job_w1 = service.wait_job(w1, timeout=3.0)
    job_w2 = service.wait_job(w2, timeout=3.0)
    job_m1 = service.wait_job(m1, timeout=3.0)

    assert job_w1.status == "cancelled"
    assert job_w2.status == "cancelled"
    assert job_m1.status in ("completed", "running")
    service.shutdown(wait=True)


def test_job_error_handling():
    service = JobService(max_workers=2)

    def _failing_task(cancel_token: Any, progress_cb: Any) -> None:
        raise ValueError("Error simulado de cálculo")

    job_id = service.submit("failing", _failing_task)
    finished = service.wait_job(job_id, timeout=3.0)

    assert finished.status == "failed"
    assert finished.error == "Error simulado de cálculo"
    assert finished.is_finished

    # Verificar que el servicio sigue operativo tras un fallo
    ok_id = service.submit("healthy", lambda c, p: "todo_bien")
    ok_job = service.wait_job(ok_id, timeout=3.0)
    assert ok_job.status == "completed"
    assert ok_job.result == "todo_bien"
    service.shutdown(wait=True)


def test_job_progress_reporting():
    service = JobService(max_workers=2)
    reported: list[tuple[float, str]] = []

    def _progress_task(cancel_token: Any, progress_cb: Callable[[float, str], None]) -> str:
        for p, msg in [(0.25, "Fase 1"), (0.5, "Fase 2"), (0.75, "Fase 3")]:
            progress_cb(p, msg)
            time.sleep(0.02)
        return "listo"

    job_id = service.submit("progress_test", _progress_task)
    finished = service.wait_job(job_id, timeout=3.0)

    assert finished.status == "completed"
    assert finished.progress == 1.0
    service.shutdown(wait=True)


def test_clear_finished_jobs():
    service = JobService(max_workers=2)
    j1 = service.submit("t1", lambda c, p: 1)
    j2 = service.submit("t2", lambda c, p: 2)
    j3 = service.submit("t3", lambda c, p: 3)
    service.wait_job(j1, timeout=3.0)
    service.wait_job(j2, timeout=3.0)
    service.wait_job(j3, timeout=3.0)

    assert len(service.all_jobs()) == 3
    removed = service.clear_finished(max_retained=1)
    assert removed == 2
    assert len(service.all_jobs()) == 1
    service.shutdown(wait=True)


def test_status_bar_rendering():
    bar = build_job_status_bar()
    assert bar.id == "job-status-bar"
    # Verificar que contiene el intervalo de sondeo a 700 ms
    intervals = [c for c in bar.children if getattr(c, "id", None) == "job-status-interval"]
    assert len(intervals) == 1
    assert intervals[0].interval == 700
    assert intervals[0].disabled is False

    # Renderizar sin trabajos
    empty_content = render_job_status_display([])
    assert empty_content == []

    # Renderizar con trabajo activo con progreso
    job_active = JobInfo(
        id="job-123",
        type="load_dataset",
        label="Cargando dataset",
        status="running",
        progress=0.65,
        message="Leyendo bloques...",
    )
    rendered = render_job_status_display([job_active])
    assert len(rendered) == 1
    card = rendered[0]
    assert "job-running" in card.className


def test_layout_mounting():
    root = html.Div([html.P("Contenido principal")])
    mounted = mount_job_status_bar(root)

    # Verificar que el primer hijo es el job-status-bar
    first_child = mounted.children[0]
    assert getattr(first_child, "id", None) == "job-status-bar"

    # Verificar idempotencia (no duplicar)
    mounted_again = mount_job_status_bar(mounted)
    bars = [c for c in mounted_again.children if getattr(c, "id", None) == "job-status-bar"]
    assert len(bars) == 1


def test_state_begin_load_and_deferred_publish(synthetic_hdf5, tmp_path):
    state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
    assert state.dataset is None
    assert state.dataset_version == 0

    job_id = state.begin_load(synthetic_hdf5)
    assert isinstance(job_id, str)

    job_service = get_job_service()
    job = job_service.get_job(job_id)
    assert job is not None
    assert job.type == "load_dataset"

    # Esperar a que la carga termine
    finished_job = job_service.wait_job(job_id, timeout=5.0)
    assert finished_job.status == "completed"

    # Verificar publicación diferida bajo lock
    assert state.dataset is not None
    assert state.dataset_version == 1
    assert state.get_active_mask("UHF").all()
    assert state.get_active_index("UHF") == 0


def test_state_warmup_via_job_service(synthetic_hdf5, tmp_path):
    state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=True)
    state.load_dataset(synthetic_hdf5)

    job_service = get_job_service()
    # Debe haberse encolado un trabajo de warmup en JobService
    warmup_jobs = [j for j in job_service.all_jobs() if j.type == "warmup"]
    assert len(warmup_jobs) >= 1

    w_job = warmup_jobs[-1]
    finished = job_service.wait_job(w_job.id, timeout=10.0)
    assert finished.status in ("completed", "cancelled")


def test_export_and_merge_jobs(synthetic_hdf5, tmp_path):
    from ui.callbacks.sensor_window_callbacks import _launch_export_thread, _launch_merge_thread

    state = AppState(cache_dir=tmp_path / "cache", warmup_on_load=False)
    state.load_dataset(synthetic_hdf5)
    snapshot = state.export_snapshot()
    assert snapshot is not None

    job_service = get_job_service()
    dest_export1 = str(tmp_path / "exported1.hdf5")
    _launch_export_thread(state, snapshot, dest_export1)

    export_jobs = [j for j in job_service.all_jobs() if j.type == "export"]
    assert len(export_jobs) == 1
    job_service.wait_job(export_jobs[0].id, timeout=5.0)
    assert export_jobs[0].status == "completed"
    assert Path(dest_export1).exists()

    dest_export2 = str(tmp_path / "exported2.hdf5")
    _launch_export_thread(state, snapshot, dest_export2)
    export_jobs2 = [j for j in job_service.all_jobs() if j.type == "export"]
    job_service.wait_job(export_jobs2[-1].id, timeout=5.0)
    assert Path(dest_export2).exists()

    dest_merge = str(tmp_path / "merged.hdf5")
    _launch_merge_thread(state, dest_export1, dest_export2, dest_merge, "resultantes")

    merge_jobs = [j for j in job_service.all_jobs() if j.type == "merge"]
    assert len(merge_jobs) == 1
    job_service.wait_job(merge_jobs[0].id, timeout=5.0)
    assert merge_jobs[0].status == "completed"
    assert Path(dest_merge).exists()

