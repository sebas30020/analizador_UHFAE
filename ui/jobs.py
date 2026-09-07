"""Servicio de trabajos en segundo plano para el Analizador de Señales UHF/AE.

Implementa un servicio de ejecución de tareas asíncronas desacopladas de los
callbacks de Dash, respaldado por un ThreadPoolExecutor(max_workers=2).
Cumple estrictamente con el invariante C.2: un solo proceso, múltiples hilos
(multiprocesamiento prohibido para evitar duplicar el estado en RAM en Windows).

Soporta cancelación cooperativa mediante threading.Event y seguimiento de estado
con bloqueo interno reentrante para seguridad entre hilos.
"""
from __future__ import annotations

import inspect
import logging
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from utils.profiling import stage, stage_job

_logger = logging.getLogger("analizador.ui.jobs")

JobStatus = Literal["pending", "running", "completed", "failed", "cancelled"]
JobState = JobStatus


class JobCancelledError(Exception):
    """Excepción lanzada cuando una tarea cooperativa detecta cancelación."""
    pass


JobCancelledException = JobCancelledError


@dataclass
class JobInfo:
    """Metadatos y estado observable de un trabajo en segundo plano."""

    id: str
    type: str
    status: JobStatus = "pending"
    progress: float | None = None
    message: str = ""
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    result: Any = None
    error: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    label: str = ""
    result_version: int | None = None

    @property
    def job_id(self) -> str:
        """Alias para compatibilidad con interfaces que esperan job_id."""
        return self.id

    @property
    def kind(self) -> str:
        """Alias para compatibilidad con interfaces que esperan kind."""
        return self.type

    @property
    def state(self) -> JobStatus:
        """Alias para compatibilidad con interfaces que esperan state."""
        return self.status

    @property
    def is_active(self) -> bool:
        return self.status in ("pending", "running")

    @property
    def is_finished(self) -> bool:
        return self.status in ("completed", "failed", "cancelled")

    @property
    def duration_s(self) -> float | None:
        if self.started_at is None:
            return None
        end = self.completed_at if self.completed_at is not None else time.time()
        return max(0.0, end - self.started_at)


class JobService:
    """Servicio de gestión y ejecución de tareas asíncronas multihilo."""

    def __init__(self, max_workers: int = 2) -> None:
        self._max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="JobWorker")
        self._lock = threading.RLock()
        self._jobs: dict[str, JobInfo] = {}
        self._futures: dict[str, Future[Any]] = {}
        self._shutdown = False

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def submit(
        self,
        job_type: str,
        target: Any = None,
        *args: Any,
        label: str = "",
        cancel_token: threading.Event | None = None,
        **kwargs: Any,
    ) -> str:
        """Encola un trabajo para ejecución asíncrona.

        Acepta firmas flexibles:
        - submit("warmup", fn, label="Precalentamiento")
        - submit("load_dataset", "Cargando...", fn)
        - submit("export", fn, arg1, arg2)
        """
        with self._lock:
            if self._shutdown:
                raise RuntimeError("JobService ya ha sido cerrado (shutdown).")

            fn: Callable[..., Any]
            effective_label = label
            if callable(target):
                fn = target
            elif isinstance(target, str) and args and callable(args[0]):
                effective_label = target
                fn = args[0]
                args = args[1:]
            elif target is None and args and callable(args[0]):
                fn = args[0]
                args = args[1:]
            else:
                raise ValueError("Debe proporcionarse una función ejecutable (callable).")

            job_id = str(uuid.uuid4())
            cancel_event = cancel_token if cancel_token is not None else threading.Event()
            job = JobInfo(
                id=job_id,
                type=job_type,
                label=effective_label or job_type,
                status="pending",
                cancel_event=cancel_event,
            )
            self._jobs[job_id] = job

            future = self._executor.submit(self._execute_job, job_id, fn, args, kwargs)
            self._futures[job_id] = future
            _logger.info("etapa=job.submit id=%s tipo=%s etiqueta=%s", job_id, job_type, job.label)
            return job_id

    def submit_job(
        self,
        job_type: str,
        fn: Callable[..., Any],
        *args: Any,
        label: str = "",
        cancel_token: threading.Event | None = None,
        **kwargs: Any,
    ) -> str:
        """Alias explícito para submit."""
        return self.submit(job_type, fn, *args, label=label, cancel_token=cancel_token, **kwargs)

    def _execute_job(
        self,
        job_id: str,
        fn: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status == "cancelled":
                return None
            job.status = "running"
            job.started_at = time.time()

        cancel_event = job.cancel_event

        def progress_callback(p: float, msg: str = "") -> None:
            with self._lock:
                clamped = max(0.0, min(1.0, float(p)))
                job.progress = clamped
                if msg:
                    job.message = msg

        try:
            if cancel_event.is_set():
                raise JobCancelledError("Cancelado antes del inicio de ejecución.")

            with stage(stage_job(job.type), job_id=job_id):
                res = self._invoke_callable(fn, cancel_event, progress_callback, args, kwargs)

            if cancel_event.is_set():
                raise JobCancelledError("Cancelado durante la ejecución.")

            with self._lock:
                job.status = "completed"
                job.completed_at = time.time()
                job.progress = 1.0
                job.result = res
                _logger.info("etapa=job.completed id=%s tipo=%s duracion_s=%.3f", job_id, job.type, job.duration_s or 0.0)
            return res

        except (JobCancelledError, JobCancelledException) as exc:
            with self._lock:
                job.status = "cancelled"
                job.completed_at = time.time()
                job.message = str(exc) or "Cancelado"
                _logger.info("etapa=job.cancelled id=%s tipo=%s", job_id, job.type)
            return None

        except Exception as exc:
            with self._lock:
                job.status = "failed"
                job.completed_at = time.time()
                job.error = str(exc)
                _logger.exception("etapa=job.failed id=%s tipo=%s error=%s", job_id, job.type, exc)
            return None

    @staticmethod
    def _invoke_callable(
        fn: Callable[..., Any],
        cancel_event: threading.Event,
        progress_callback: Callable[[float, str], None],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:
        try:
            sig = inspect.signature(fn)
            params = list(sig.parameters.values())
            param_names = [p.name for p in params]
            has_varargs = any(p.kind == inspect.Parameter.VAR_POSITIONAL for p in params)
            has_varkw = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params)

            call_kwargs = dict(kwargs)
            for cand in ("cancel_event", "cancelled", "cancel_token"):
                if cand in param_names:
                    call_kwargs.setdefault(cand, cancel_event)
            for cand in ("progress_callback", "progress_cb"):
                if cand in param_names:
                    call_kwargs.setdefault(cand, progress_callback)

            if not args and not kwargs and len(params) == 2 and not (has_varargs or has_varkw):
                return fn(cancel_event, progress_callback)
            elif not args and not kwargs and len(params) == 1 and not (has_varargs or has_varkw):
                pname = params[0].name
                if pname in ("progress_callback", "progress_cb"):
                    return fn(progress_callback)
                return fn(cancel_event)

            return fn(*args, **call_kwargs)

        except (ValueError, TypeError):
            if args or kwargs:
                return fn(*args, **kwargs)
            try:
                return fn(cancel_event, progress_callback)
            except TypeError:
                try:
                    return fn(cancel_event)
                except TypeError:
                    return fn()

    def cancel(self, job_id: str) -> bool:
        """Solicita la cancelación cooperativa de un trabajo."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.is_finished:
                return False

            job.cancel_event.set()
            future = self._futures.get(job_id)
            if future is not None and job.status == "pending":
                future.cancel()
                job.status = "cancelled"
                job.completed_at = time.time()
                job.message = "Cancelado antes de iniciar"
                _logger.info("etapa=job.cancel_pending id=%s tipo=%s", job_id, job.type)
                return True

            _logger.info("etapa=job.cancel_signaled id=%s tipo=%s", job_id, job.type)
            return True

    def cancel_job(self, job_id: str) -> bool:
        """Alias para cancel."""
        return self.cancel(job_id)

    def cancel_kind(self, kind: str) -> int:
        """Cancela todos los trabajos activos de un tipo determinado."""
        with self._lock:
            active_ids = [j.id for j in self._jobs.values() if j.type == kind and j.is_active]
        count = 0
        for j_id in active_ids:
            if self.cancel(j_id):
                count += 1
        return count

    def cancel_type(self, job_type: str) -> int:
        """Alias para cancel_kind."""
        return self.cancel_kind(job_type)

    def get_job(self, job_id: str) -> JobInfo | None:
        with self._lock:
            return self._jobs.get(job_id)

    def status(self, job_id: str) -> JobInfo | None:
        return self.get_job(job_id)

    def get_job_status(self, job_id: str) -> JobInfo | None:
        return self.get_job(job_id)

    def active(self) -> list[JobInfo]:
        """Lista de trabajos en estado pending o running."""
        with self._lock:
            return [j for j in self._jobs.values() if j.is_active]

    def get_active_jobs(self) -> list[JobInfo]:
        return self.active()

    def all_jobs(self) -> list[JobInfo]:
        with self._lock:
            return list(self._jobs.values())

    def clear_finished(self, max_retained: int = 50) -> int:
        """Limpia trabajos finalizados conservando como máximo `max_retained` más recientes."""
        with self._lock:
            finished = [j for j in self._jobs.values() if j.is_finished]
            if len(finished) <= max_retained:
                return 0
            finished.sort(key=lambda x: x.completed_at or x.created_at)
            to_remove = finished[:-max_retained]
            for j in to_remove:
                self._jobs.pop(j.id, None)
                self._futures.pop(j.id, None)
            return len(to_remove)

    def wait_job(self, job_id: str, timeout: float | None = None) -> JobInfo:
        """Espera a que un trabajo termine y retorna su JobInfo final."""
        with self._lock:
            future = self._futures.get(job_id)
        if future is not None:
            try:
                future.result(timeout=timeout)
            except Exception:
                pass
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(f"Trabajo no encontrado: {job_id}")
        return job

    def shutdown(self, wait: bool = False, cancel_futures: bool = True) -> None:
        with self._lock:
            self._shutdown = True
            for job in self._jobs.values():
                if job.is_active:
                    job.cancel_event.set()
        self._executor.shutdown(wait=wait, cancel_futures=cancel_futures)


_JOB_SERVICE: JobService | None = None
_JOB_SERVICE_LOCK = threading.Lock()


def get_job_service() -> JobService:
    """Acceso perezoso al singleton de proceso del servicio de trabajos."""
    global _JOB_SERVICE
    with _JOB_SERVICE_LOCK:
        if _JOB_SERVICE is None:
            _JOB_SERVICE = JobService(max_workers=2)
        return _JOB_SERVICE


def reset_job_service() -> None:
    """Reinicia el singleton del servicio de trabajos (usado para aislamiento de pruebas)."""
    global _JOB_SERVICE
    with _JOB_SERVICE_LOCK:
        if _JOB_SERVICE is not None:
            _JOB_SERVICE.shutdown(wait=False, cancel_futures=True)
            _JOB_SERVICE = None

