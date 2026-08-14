"""Normalización de señales por escala vertical (FASE0_DISENO_Analizador_UHF_AE.md §3.1).

Regla inviolable del PROMPT maestro §2.4: ninguna métrica se calcula sobre señal cruda.
Toda señal debe normalizarse antes de entrar al motor de métricas (``metrics/``), nunca
dentro de una función de métrica individual.

Fórmula: ``x_norm = x_raw / vrange``, donde ``vrange`` es la escala vertical de ESA señal
(no un valor global por sensor).

Desviación documentada frente al PDF de referencia (Ecuación 4, corrección de línea base
``x_i[n] = v_i[n] - b_i``): **no se implementa** aquí. Decisión explícita del usuario
(AUDITORIA_FORMULAS_PDF_vs_metricas.md §1, Opción C) tras verificar empíricamente que el
offset en los datos reales es del orden del ruido de fondo, y para mantener paridad con
``analizador_nuevo/metricas.py`` (ya validado), que tampoco la aplica.
"""
from __future__ import annotations

import numpy as np

NORMALIZATION_VERSION = "v1_divide_by_vrange"


def compute_valid_mask(trigger: np.ndarray, vrange: np.ndarray) -> np.ndarray:
    """Marca inválida una señal si le falta ``trigger`` o ``vrange``, o si ``vrange <= 0``.

    Una señal inválida se excluye de todo cálculo de métricas (PROMPT §11) — el resto del
    pipeline debe respetar esta máscara, nunca normalizar ni calcular sobre filas inválidas.
    """
    trigger = np.asarray(trigger, dtype=np.float64)
    vrange = np.asarray(vrange, dtype=np.float64)
    if trigger.shape != vrange.shape:
        raise ValueError(f"trigger {trigger.shape} y vrange {vrange.shape} deben tener la misma forma")

    valid = np.isfinite(trigger) & np.isfinite(vrange) & (vrange > 0.0)
    return valid


def normalize(data: np.ndarray, vrange: np.ndarray, valid_mask: np.ndarray | None = None) -> np.ndarray:
    """Normaliza un bloque de señales ``(N, M)`` dividiendo cada fila por su ``vrange``.

    Las filas marcadas inválidas en ``valid_mask`` (si se provee) se retornan como NaN,
    de forma explícita y auditable, en vez de producir un resultado numérico engañoso
    (p. ej. división por cero silenciosa).
    """
    data = np.asarray(data, dtype=np.float32)
    vrange = np.asarray(vrange, dtype=np.float64)
    if data.shape[0] != vrange.shape[0]:
        raise ValueError(f"data tiene {data.shape[0]} filas, vrange tiene {vrange.shape[0]}")

    if valid_mask is None:
        valid_mask = vrange > 0.0

    safe_vrange = np.where(valid_mask, vrange, 1.0)
    normalized = data / safe_vrange[:, np.newaxis]
    normalized[~valid_mask, :] = np.nan
    return normalized.astype(np.float32)
