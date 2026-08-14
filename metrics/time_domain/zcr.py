"""Zero Crossing Rate (Ecuación 22 del PDF de referencia)."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


def compute_zcr_array(s: np.ndarray, dead_zone: float = 0.01) -> np.ndarray:
    """Implementación compartida, reutilizada por ``freq_domain/f_aprox.py`` (Ec. 23)
    para no duplicar la lógica de zona muerta -- importada como función simple, no vía
    registro (el registro es solo para el contrato de metadatos de la métrica, no
    impide reutilizar código entre módulos).

    Nota de diseño heredada de ``analizador_nuevo/metricas.py`` (ya validado): con
    ``dead_zone > 0`` se filtran las muestras dentro de la zona muerta y se cuentan
    transiciones de signo solo entre las muestras restantes, pero el denominador sigue
    siendo ``N_i - 1`` (el total original), tal como documenta
    AUDITORIA_FORMULAS_PDF_vs_metricas.md §5 (zona gris del propio PDF, comportamiento
    preservado sin cambios). Ese caso no se pudo vectorizar limpiamente sobre el eje de
    señales (compactar por fila es una operación irregular) y se resuelve con un bucle
    por señal -- igual que la referencia ya validada; el trabajo interno por fila sí
    está vectorizado sobre las M muestras.
    """
    n_signals, n_points = s.shape
    zcr = np.full(n_signals, np.nan, dtype=np.float64)

    if dead_zone <= 0.0:
        s_signs = np.sign(s)
        s_signs[s_signs == 0] = 1
        crossings = np.sum(s_signs[:, :-1] != s_signs[:, 1:], axis=1)
        return crossings / (n_points - 1)

    peak = np.max(np.abs(s), axis=1)
    for i in range(n_signals):
        if np.isnan(peak[i]) or peak[i] == 0:
            continue
        y = s[i] / peak[i]
        signs = np.zeros_like(y)
        signs[y > dead_zone] = 1
        signs[y < -dead_zone] = -1
        non_zero = signs[signs != 0]
        if non_zero.size <= 1:
            zcr[i] = 0.0
        else:
            crossings = np.sum(non_zero[:-1] != non_zero[1:])
            zcr[i] = crossings / (n_points - 1)
    return zcr


@metric(
    id="zcr",
    label="ZCR",
    regimen="puntual",
    dominio="tiempo",
    unit="",
    version=1,
    params_schema={"dead_zone": 0.01},
)
def compute(ctx: MetricContext, dead_zone: float = 0.01, **params: object) -> np.ndarray:
    """ZCR_i = (1/(N_i-1)) * sum(1{q_i[n+1]*q_i[n] < 0})."""
    s = ctx.signal_matrix
    assert s is not None
    return compute_zcr_array(s, dead_zone=dead_zone)
