"""Registro de métricas por decorador (FASE0_DISENO_Analizador_UHF_AE.md §8, PROMPT §4.4).

Agregar una métrica nueva = crear un archivo en ``metrics/time_domain/`` o
``metrics/freq_domain/`` con una función decorada por :func:`metric`. Cero cambios en
``engine.py`` ni en la UI (prueba de fuego §10.3 del PROMPT maestro): el motor descubre
el registro por introspección en arranque (:func:`discover_metrics`).

Todas las funciones de métrica son puras y vectorizadas: reciben un
:class:`MetricContext` con lo que necesiten (traza normalizada, espectro centralizado,
timestamps, duración de ventana) y devuelven un array ``(N,)`` (régimen puntual) o un
escalar ``float`` (régimen grupo intrínseca) — nunca iteran señal por señal en Python
cuando NumPy admite operar sobre el eje completo (§9.1 del PROMPT).
"""
from __future__ import annotations

import importlib
import pkgutil
from dataclasses import dataclass
from typing import Any, Callable, Literal

import numpy as np

Regimen = Literal["puntual", "grupo"]
Dominio = Literal["tiempo", "frecuencia"]


class MetricContext:
    """Envoltorio uniforme de entrada para toda función de métrica.

    Ningún campo es obligatorio para todas las métricas: cada plugin solo lee los que
    declara necesitar (vía ``requires_spectrum``/``requires_global_timestamps`` o,
    para régimen grupo, los campos de grupo). Mantener un contrato único evita que
    agregar un campo nuevo rompa la firma de las métricas ya escritas.

    ``signal_matrix`` admite además una **construcción perezosa** vía
    ``signal_matrix_factory``: el llamador entrega una función que materializa la matriz
    normalizada, y esa función solo se ejecuta si la métrica llega a leer
    ``ctx.signal_matrix``. Métricas como ``tasa_pulsos`` o ``tasa_rafagas`` se calculan
    solo con ``timestamps``, y normalizar la matriz "por si acaso" era el trabajo más
    caro de ``compute_group_intrinsic`` (Fase 7: 4,3 s de los 4,3 s que tardaba propagar
    un filtro sobre AE, medido con ``benchmarks/run_benchmarks.py``). Los plugins no se
    enteran: siguen leyendo el mismo atributo.

    No es un ``dataclass`` justamente por eso -- ``signal_matrix`` es una propiedad con
    memoización, no un campo.
    """

    __slots__ = ("_signal_matrix", "_signal_matrix_factory", "timestamps", "spectrum", "fs_hz", "T_w")

    def __init__(
        self,
        signal_matrix: np.ndarray | None = None,     # (N, M) normalizada -- régimen puntual, por traza
        timestamps: np.ndarray | None = None,        # (N,) -- puntual con requires_global_timestamps, o grupo
        spectrum: Any | None = None,                 # SpectrumResult -- puntual con requires_spectrum
        fs_hz: float | None = None,
        T_w: float | None = None,                    # solo régimen grupo (duración declarada de la ventana)
        signal_matrix_factory: Callable[[], np.ndarray] | None = None,
    ) -> None:
        self._signal_matrix = signal_matrix
        self._signal_matrix_factory = signal_matrix_factory
        self.timestamps = timestamps
        self.spectrum = spectrum
        self.fs_hz = fs_hz
        self.T_w = T_w

    @property
    def signal_matrix(self) -> np.ndarray | None:
        """Matriz normalizada ``(N, M)``. Si se construyó con ``signal_matrix_factory``,
        la primera lectura la materializa y la memoiza (una métrica puede leerla varias
        veces sin pagar dos normalizaciones)."""
        if self._signal_matrix is None and self._signal_matrix_factory is not None:
            self._signal_matrix = self._signal_matrix_factory()
        return self._signal_matrix

    def __repr__(self) -> str:
        shape = None if self._signal_matrix is None else self._signal_matrix.shape
        pendiente = self._signal_matrix is None and self._signal_matrix_factory is not None
        return (
            f"MetricContext(signal_matrix={'<perezosa>' if pendiente else shape}, "
            f"n_timestamps={None if self.timestamps is None else self.timestamps.shape[0]}, "
            f"fs_hz={self.fs_hz}, T_w={self.T_w})"
        )


@dataclass(frozen=True)
class MetricDefinition:
    id: str
    label: str
    regimen: Regimen
    dominio: Dominio
    unit: str
    version: int
    params_schema: dict[str, Any]
    compute: Callable[..., Any]
    requires_spectrum: bool = False
    requires_global_timestamps: bool = False
    doc: str = ""


_REGISTRY: dict[str, MetricDefinition] = {}


def metric(
    id: str,
    label: str,
    regimen: Regimen,
    dominio: Dominio,
    unit: str,
    version: int,
    params_schema: dict[str, Any] | None = None,
    requires_spectrum: bool = False,
    requires_global_timestamps: bool = False,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorador que registra una función ``compute(ctx: MetricContext, **params)``."""

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        if id in _REGISTRY:
            raise ValueError(f"Métrica duplicada: '{id}' ya está registrada (¿archivo importado dos veces?)")
        _REGISTRY[id] = MetricDefinition(
            id=id,
            label=label,
            regimen=regimen,
            dominio=dominio,
            unit=unit,
            version=version,
            params_schema=dict(params_schema or {}),
            compute=fn,
            requires_spectrum=requires_spectrum,
            requires_global_timestamps=requires_global_timestamps,
            doc=(fn.__doc__ or "").strip(),
        )
        return fn

    return decorator


def discover_metrics(force_reload: bool = False) -> dict[str, MetricDefinition]:
    """Importa todos los módulos de ``metrics/time_domain`` y ``metrics/freq_domain``
    para que sus decoradores ``@metric`` se ejecuten y pueblen el registro.

    Siempre recorre los 20 módulos, incluso si ya se llamó antes: ``importlib.import_module``
    es un simple lookup en ``sys.modules`` para un módulo ya importado (no reejecuta el
    cuerpo del módulo ni el decorador), así que repetir el recorrido es barato y seguro.

    Deliberadamente **no** usa "``_REGISTRY`` no está vacío" como señal de "ya completo":
    un test (u otro código) puede importar un solo módulo de métrica directamente
    (p. ej. ``from metrics.time_domain.zcr import compute_zcr_array``), lo que puebla el
    registro parcialmente como efecto secundario del import de Python **antes** de que
    nada llame a ``discover_metrics()``. Cortar aquí al ver el registro no vacío dejaba
    el resto de métricas sin descubrir (bug real encontrado al ejecutar la suite
    completa de Fase 2 -- ver FASE2_ENTREGA.md).

    ``force_reload=True`` (uso en tests) además fuerza un ``importlib.reload()`` de cada
    módulo tras limpiar el registro, para poder reejecutar los decoradores a propósito.
    """
    if force_reload:
        _REGISTRY.clear()

    import metrics.time_domain as time_domain_pkg
    import metrics.freq_domain as freq_domain_pkg

    for pkg in (time_domain_pkg, freq_domain_pkg):
        for module_info in pkgutil.iter_modules(pkg.__path__, prefix=f"{pkg.__name__}."):
            module = importlib.import_module(module_info.name)
            if force_reload:
                importlib.reload(module)

    return dict(_REGISTRY)


def get_metric(metric_id: str) -> MetricDefinition:
    if metric_id not in _REGISTRY:
        discover_metrics()
    if metric_id not in _REGISTRY:
        raise KeyError(f"Métrica desconocida: '{metric_id}'. Registradas: {sorted(_REGISTRY)}")
    return _REGISTRY[metric_id]


def list_metrics(regimen: Regimen | None = None, dominio: Dominio | None = None) -> list[MetricDefinition]:
    discover_metrics()
    result = list(_REGISTRY.values())
    if regimen is not None:
        result = [m for m in result if m.regimen == regimen]
    if dominio is not None:
        result = [m for m in result if m.dominio == dominio]
    return sorted(result, key=lambda m: m.id)
