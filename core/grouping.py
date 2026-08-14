"""Criterios de agrupamiento (PROMPT maestro §4.2).

El agrupamiento se define **sobre la matriz global ya concatenada y ordenada
cronológicamente** (nunca sobre chunks del origen, que ya desaparecieron en la Fase 1).
Dos modos mutuamente excluyentes:

- **Por ventana temporal** (``by_time``): ventanas fijas, no solapadas, ancladas al
  primer timestamp. ``T_w`` es siempre la duración **declarada** de la ventana (el
  ``Δt`` configurado), nunca el span observado de los pulsos dentro de ella — corrige
  el hallazgo de AUDITORIA_FORMULAS_PDF_vs_metricas.md §4 (inflar la tasa si los pulsos
  se agrupan en una fracción de la ventana).
- **Por cantidad de señales consecutivas** (``by_count``): bloques fijos de ``K``
  señales. Aquí no existe una duración declarada externамente, así que ``T_w`` es el
  span temporal real del propio grupo (``t[último] - t[primero]``) — es la única
  definición posible para este modo, no una regresión del hallazgo de auditoría.

Casos borde (PROMPT §4.2): ventanas vacías se omiten (no generan grupo); el grupo final
incompleto se calcula igual, marcado ``is_partial=True``. Timestamp representativo:
centro temporal del grupo (por defecto), documentado por modo en cada resolutor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np

GroupingMode = Literal["by_time", "by_count"]


@dataclass(frozen=True)
class Group:
    index: int
    start_idx: int          # inclusive, índice cronológico global
    end_idx: int             # exclusivo
    T_w: float                # duración a usar en las fórmulas de tasa (Ec. 10, 15, 47 del PDF)
    center_timestamp: float   # timestamp representativo del grupo
    is_partial: bool

    @property
    def n_signals(self) -> int:
        return self.end_idx - self.start_idx


def resolve_groups_by_time(timestamps: np.ndarray, delta_t: float, t_start: float | None = None) -> list[Group]:
    """Ventanas fijas de duración ``delta_t`` (s), no solapadas, ancladas a ``t_start``
    (por defecto el primer timestamp). Timestamp representativo = centro de la ventana
    **declarada** (``a_w + delta_t/2``), no de los pulsos observados dentro de ella.
    """
    if delta_t <= 0:
        raise ValueError(f"delta_t debe ser > 0, recibido {delta_t}")
    if timestamps.shape[0] == 0:
        return []

    t_start = timestamps[0] if t_start is None else t_start
    t_max = timestamps[-1]

    bin_idx = np.floor((timestamps - t_start) / delta_t).astype(np.int64)
    unique_bins = np.unique(bin_idx)

    groups: list[Group] = []
    for g_index, b in enumerate(unique_bins):
        mask = bin_idx == b
        indices = np.where(mask)[0]
        start_idx, end_idx = int(indices[0]), int(indices[-1]) + 1
        a_w = t_start + b * delta_t
        window_end = a_w + delta_t
        is_partial = window_end > t_max
        groups.append(
            Group(
                index=g_index,
                start_idx=start_idx,
                end_idx=end_idx,
                T_w=float(delta_t),
                center_timestamp=float(a_w + delta_t / 2.0),
                is_partial=bool(is_partial),
            )
        )
    return groups


def resolve_groups_by_count(timestamps: np.ndarray, k: int) -> list[Group]:
    """Bloques fijos de ``k`` señales consecutivas en orden cronológico. ``T_w`` es el
    span temporal real del grupo (no hay ventana declarada en este modo). El último
    grupo puede tener menos de ``k`` señales -> ``is_partial=True``.
    """
    if k <= 0:
        raise ValueError(f"k debe ser > 0, recibido {k}")
    n = timestamps.shape[0]
    if n == 0:
        return []

    groups: list[Group] = []
    for g_index, start_idx in enumerate(range(0, n, k)):
        end_idx = min(start_idx + k, n)
        t_first = timestamps[start_idx]
        t_last = timestamps[end_idx - 1]
        groups.append(
            Group(
                index=g_index,
                start_idx=start_idx,
                end_idx=end_idx,
                T_w=float(t_last - t_first),
                center_timestamp=float((t_first + t_last) / 2.0),
                is_partial=bool((end_idx - start_idx) < k),
            )
        )
    return groups


def resolve_groups(
    timestamps: np.ndarray, mode: GroupingMode, value: float, t_start: float | None = None
) -> list[Group]:
    """Punto de entrada único: despacha a :func:`resolve_groups_by_time` (``value`` en
    segundos) o :func:`resolve_groups_by_count` (``value`` = ``K``, entero)."""
    if mode == "by_time":
        return resolve_groups_by_time(timestamps, delta_t=value, t_start=t_start)
    elif mode == "by_count":
        return resolve_groups_by_count(timestamps, k=int(value))
    raise ValueError(f"Modo de agrupamiento desconocido: '{mode}'")
