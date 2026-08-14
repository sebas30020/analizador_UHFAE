# Cómo agregar una métrica

**Una métrica nueva = un archivo nuevo.** No hay que tocar el motor, ni el caché, ni la
interfaz: el registro las descubre por introspección del paquete y el selector de
métricas se puebla desde ese mismo registro.

## 1. Elige dónde va

| Si la métrica… | El archivo va en |
|---|---|
| se calcula sobre la traza en el tiempo | `metrics/time_domain/` |
| se calcula sobre el espectro | `metrics/freq_domain/` |

El nombre del archivo es libre; lo que identifica a la métrica es el `id` del decorador.

## 2. Escribe la función

```python
"""Factor de forma (ejemplo)."""
from __future__ import annotations

import numpy as np

from metrics.registry import MetricContext, metric


@metric(id="factor_forma", label="Factor de Forma", regimen="puntual",
        dominio="tiempo", unit="", version=1)
def compute(ctx: MetricContext, **params: object) -> np.ndarray:
    """FF_i = RMS_i / mean(|x_i|)."""
    s = ctx.signal_matrix
    assert s is not None
    rms = np.sqrt(np.mean(s**2, axis=1))
    return rms / np.mean(np.abs(s), axis=1)
```

Eso es todo. Al reiniciar el servidor, "Factor de Forma" aparece en el selector de
métricas, tanto en régimen puntual como en grupo-reducción, y se cachea como cualquier
otra.

## 3. Reglas del contrato

- **Vectoriza siempre.** `ctx.signal_matrix` es la matriz `(N, M)` de todas las señales
  del lote: opera sobre el eje 1 y devuelve un array `(N,)`. Nunca iteres señal por señal
  en Python; el §9.1 del PROMPT lo prohíbe explícitamente y con 20 000 señales se nota.
- **La señal ya viene normalizada** (`x / vrange`). No normalices dentro de la métrica.
- **No calcules tu propia FFT.** Declara `requires_spectrum=True` y lee `ctx.spectrum`
  (`freqs_hz`, `mag2`): el espectro se calcula una vez por lote y lo comparten todas las
  métricas espectrales de la pasada.
- **Documenta la fórmula en el docstring**, en notación matemática. Ese texto es la
  definición de referencia de la métrica.
- **Devuelve NaN, no excepciones**, para casos degenerados de una señal concreta: el
  motor sabe descartar NaN al reducir por grupo. Una excepción tumba el cálculo entero.

## 4. Qué declara el decorador

| Argumento | Para qué sirve |
|---|---|
| `id` | Identificador estable. Entra en la clave de caché: **no lo cambies** sin asumir que invalidas lo calculado. |
| `label` | Nombre visible en el selector y en el eje Y de la gráfica. |
| `regimen` | `"puntual"` (un valor por señal) o `"grupo"` (un valor por ventana, métrica intrínseca). |
| `dominio` | `"tiempo"` o `"frecuencia"`. Solo etiqueta para la interfaz. |
| `unit` | Unidad, para el eje Y. Cadena vacía si es adimensional. |
| `version` | **Súbela cuando cambies la fórmula.** Entra en la clave de caché, así que subirla invalida sola lo viejo sin borrar nada a mano. |
| `params_schema` | Parámetros con sus valores por defecto, p. ej. `{"tau": 0.01, "n_min": 5}`. |
| `requires_spectrum` | `True` si lees `ctx.spectrum`. |
| `requires_global_timestamps` | `True` si necesitas la secuencia completa de pulsos del sensor (Δt y derivadas), no solo los de un grupo. |

## 5. Métricas de grupo intrínsecas

Si la métrica solo tiene sentido sobre el conjunto (una tasa), declara `regimen="grupo"`
y devuelve un **escalar**:

```python
@metric(id="tasa_algo", label="Tasa de Algo", regimen="grupo",
        dominio="tiempo", unit="Hz", version=1)
def compute(ctx: MetricContext, **params: object) -> float:
    ts = ctx.timestamps          # solo las señales válidas del grupo
    T_w = ctx.T_w                # duración DECLARADA de la ventana
    assert ts is not None and T_w is not None and T_w > 0
    return ts.shape[0] / T_w
```

Usa siempre `ctx.T_w`, nunca `ts.max() - ts.min()`: la duración de la ventana es un dato
del agrupamiento, no algo a inferir de lo que se observó dentro.

Si tu métrica de grupo **no** necesita las trazas, no toques `ctx.signal_matrix`: se
materializa de forma perezosa y no leerla ahorra normalizar la matriz entera del grupo.

## 6. Pruébala

Toda métrica necesita una prueba unitaria contra una señal sintética de **resultado
analítico conocido** — no contra "lo que devolvió la implementación". En
`tests/test_metrics_time_domain.py` y `tests/test_metrics_freq_domain.py` hay ejemplos:

```python
def test_factor_forma_de_una_senoidal():
    # Para una senoidal pura, FF = (A/√2) / (2A/π) = π / (2√2) ≈ 1.1107
    t = np.linspace(0, 1, 10_000, endpoint=False)
    ctx = MetricContext(signal_matrix=np.sin(2 * np.pi * 5 * t)[np.newaxis, :], fs_hz=10_000.0)
    assert np.isclose(get_metric("factor_forma").compute(ctx)[0], np.pi / (2 * np.sqrt(2)), rtol=1e-3)
```

Y comprueba que la métrica quedó registrada de verdad:

```bash
pytest tests/test_registry.py tests/test_metrics_time_domain.py -q
```

## 7. Lista de verificación

- [ ] Un archivo nuevo en `time_domain/` o `freq_domain/`, con `@metric(...)`.
- [ ] Cálculo vectorizado sobre el eje 1, sin bucles por señal.
- [ ] Docstring con la fórmula.
- [ ] `version=1` (o subida, si reemplazas una existente).
- [ ] Prueba contra un valor analítico conocido.
- [ ] Cero cambios en `engine.py`, `service.py`, `registry.py` y `ui/`. Si necesitaste
      tocar alguno, eso es una señal de que el contrato se quedó corto: vale la pena
      revisarlo antes de seguir.
