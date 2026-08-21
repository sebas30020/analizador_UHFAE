"""Estado compartido del proceso Dash (FASE0_DISENO_Analizador_UHF_AE.md §3.4).

Herramienta de un solo usuario local: el estado (dataset cargado, índice de señal
activa por sensor, máscara de filtrado) vive en memoria de un único proceso, protegido
por un lock simple — no hace falta Redis ni almacenamiento externo. Todas las
pestañas/ventanas del navegador comparten este mismo estado porque hablan con el mismo
proceso Dash.

Fase 6 (filtrado cruzado, PROMPT §7.2): ``AppState`` es la "fuente única de verdad" de
la máscara de selección/exclusión por sensor -- todas las gráficas son vistas derivadas,
ninguna mantiene su propio estado de filtrado.
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from cache.backend import SqliteHdf5CacheBackend
from cache.warmup import default_warmup_specs, start_background_warmup
from core.models import EnvironmentalSeries, EventSeries, SensorConfig, SensorName, SignalBlock, load_sensor_configs
from core.normalization import NORMALIZATION_VERSION
from data.export import ExportPartition
from data.ingest import ingest_experiment
from data.readers.factory import open_reader

_logger = logging.getLogger("analizador.ui.state")

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SENSORS_CONFIG_PATH = PROJECT_ROOT / "config" / "sensors.yaml"
DEFAULT_CACHE_DIR = PROJECT_ROOT / "cache_data"


@dataclass
class LoadedDataset:
    dataset_id: str
    source_path: Path
    experiment: str
    sensor_configs: dict[SensorName, SensorConfig]
    blocks: dict[SensorName, SignalBlock]
    environmental: EnvironmentalSeries
    events: EventSeries
    t0: float


@dataclass
class ExportSnapshot:
    """Copia barata del dataset cargado y de las máscaras de filtrado activas, tomada
    bajo el lock del proceso (Fase 6: "Exportar datos filtrados…").

    ``blocks``/``sensor_configs``/``environmental``/``events`` son las referencias del
    ``LoadedDataset`` actual, no copias -- un ``load_dataset()`` posterior nunca muta un
    dataset ya cargado, solo reemplaza ``AppState._dataset`` por uno nuevo, así que
    seguir leyendo estas referencias en un hilo aparte tras soltar el lock es seguro.
    Solo ``active_masks`` se copia de verdad, porque esa sí puede seguir mutando en el
    proceso mientras la exportación está en curso.
    """

    dataset_id: str
    experiment: str
    normalization_version: str
    source_path: Path
    blocks: dict[SensorName, SignalBlock]
    sensor_configs: dict[SensorName, SensorConfig]
    active_masks: dict[SensorName, np.ndarray]
    environmental: EnvironmentalSeries
    events: EventSeries


def compute_t0(
    blocks: dict[SensorName, SignalBlock],
    environmental: EnvironmentalSeries,
    events: EventSeries,
) -> float:
    """Referencia "inicio del experimento" para el eje de minutos transcurridos de las
    gráficas tipo #1/#3 (``ui/components/time_axis.py``): el timestamp más temprano
    entre TODAS las señales de TODOS los sensores (UHF y AE comparten esta misma
    referencia, no se recalcula por sensor, así ambas ventanas gemelas y todas las
    gráficas apiladas quedan alineadas al mismo cero), la serie ambiental y los eventos.

    Usa ``block.timestamps`` sin filtrar por ``valid_mask``: una señal con metadato
    inválido (vrange=0, trigger NaN) igual ocurrió en ese instante -- excluirla podría
    hacer que ``t0`` "salte" hacia adelante si la primera señal capturada resulta
    inválida. Retorna ``0.0`` si todo está vacío (dataset degenerado).
    """
    candidates: list[float] = []
    for block in blocks.values():
        if block.timestamps.shape[0] > 0:
            candidates.append(float(block.timestamps.min()))
    if environmental.timestamps.shape[0] > 0:
        candidates.append(float(environmental.timestamps.min()))
    if events.timestamps.shape[0] > 0:
        candidates.append(float(events.timestamps.min()))
    return min(candidates) if candidates else 0.0


class AppState:
    """Único punto de acceso al dataset cargado y a la navegación de señal activa."""

    def __init__(
        self,
        cache_dir: Path = DEFAULT_CACHE_DIR,
        sensors_config_path: Path = DEFAULT_SENSORS_CONFIG_PATH,
        warmup_on_load: bool = True,
    ):
        self._lock = threading.Lock()
        self._dataset: LoadedDataset | None = None
        self._active_index: dict[SensorName, int] = {}
        self._sensors_config_path = sensors_config_path
        self._cache_dir = cache_dir
        # Fase 7: el precalentamiento de caché (Fase 3, ``cache/warmup.py``) estaba
        # implementado y probado pero nunca se llamaba desde la UI. Se dispara al cargar
        # un dataset, en un hilo daemon. Los benchmarks lo apagan (``warmup_on_load=False``)
        # para que no contamine la medición de "caché frío" compitiendo por CPU.
        self._warmup_on_load = warmup_on_load
        self._dataset_version = 0  # 0 = "sin dataset cargado"; se incrementa en cada load_dataset()
        self.cache = SqliteHdf5CacheBackend(cache_dir)

        # Fase 6 (PROMPT §7.2): máscara de filtrado por sensor (True = señal incluida) +
        # pilas de deshacer/rehacer (snapshots completos de la máscara, uno por
        # operación de filtrado confirmada) + contador de versión para que los
        # callbacks de refresco de gráficas sepan cuándo re-renderizar (mismo patrón
        # que ``_dataset_version``). Todo se resetea en cada ``load_dataset()``.
        self._active_mask: dict[SensorName, np.ndarray] = {}
        self._undo_stack: dict[SensorName, list[np.ndarray]] = {}
        self._redo_stack: dict[SensorName, list[np.ndarray]] = {}
        self._filter_version = 0

        # Fase 6: estado del último "Exportar datos filtrados…" lanzado en este proceso
        # (mensaje + si sigue en curso) -- la escritura corre en un hilo aparte
        # (``ui/callbacks/sensor_window_callbacks.py``), así que el callback de Dash que
        # dispara el botón no puede esperar el resultado; un ``dcc.Interval`` sondea
        # este estado hasta que ``en_progreso`` pasa a ``False``. Mismo espíritu que
        # ``_dataset_version``/``_filter_version``, pero no hace falta contador: solo
        # hay una exportación relevante a la vez, la última.
        self._export_in_progress = False
        self._export_message = ""

    def load_dataset(self, path: str | Path, partition: ExportPartition = "resultantes") -> LoadedDataset:
        """Ingiere un archivo de origen (§10 supuesto #6 de FASE0: un archivo = un
        experimento independiente) y lo deja como dataset activo.

        El formato del archivo (esquema con chunks, Keysight en memoria segmentada, o
        una exportación filtrada de esta misma herramienta -- ``data/export.py``) se
        detecta por contenido en ``open_reader`` -- este método no sabe cuál es, solo
        habla con la interfaz ``OriginReader`` (§10.3, prueba de fuego #3 del PROMPT
        maestro). ``partition`` solo tiene efecto sobre una exportación filtrada: qué
        subconjunto cargar (activas, excluidas, o ambas recombinadas) -- inerte para
        los otros dos formatos.
        """
        sensor_configs = load_sensor_configs(self._sensors_config_path)
        with open_reader(path, partition=partition) as reader:
            experiments = reader.list_experiments()
            if not experiments:
                raise ValueError(f"El archivo no contiene ningún experimento: {path}")
            experiment = experiments[0]

            # Solo se ingieren los sensores que este origen realmente ofrece -- un
            # archivo Keysight no tiene AE, y viceversa (§4.4 del plan): sin este
            # filtro, ingest_experiment pediría lotes a sensores inexistentes y
            # produciría bloques vacíos silenciosos en vez de simplemente no aparecer.
            sensores_disponibles = [s for s in sensor_configs if s in reader.available_sensors()]
            result = ingest_experiment(reader, experiment, sensores_disponibles)

            # El YAML documenta el valor nominal; el archivo puede aportar el efectivo
            # (p. ej. fs_hz/n_samples reales de una adquisición Keysight, §4.3 del
            # plan) -- se resuelve aquí, una sola vez por carga, no en cada cálculo.
            for sensor in sensores_disponibles:
                overrides = reader.sensor_config_overrides(experiment, sensor)
                if overrides:
                    effective = replace(sensor_configs[sensor], **overrides)
                    if effective != sensor_configs[sensor]:
                        _logger.info(
                            "Perfil efectivo de %s (dataset %s): %s", sensor, reader.dataset_id, overrides
                        )
                    sensor_configs = {**sensor_configs, sensor: effective}

        dataset = LoadedDataset(
            dataset_id=result.dataset_id,
            source_path=Path(path),
            experiment=experiment,
            sensor_configs=sensor_configs,
            blocks=result.sensors,
            environmental=result.environmental,
            events=result.events,
            t0=compute_t0(result.sensors, result.environmental, result.events),
        )
        with self._lock:
            self._dataset = dataset
            self._active_index = {sensor: 0 for sensor, block in dataset.blocks.items() if block.data.shape[0] > 0}
            # Un dataset nuevo invalida cualquier filtro previo (son señales distintas) --
            # reseteo duro, igual que el índice de navegación activa.
            self._active_mask = {
                sensor: np.ones(block.data.shape[0], dtype=bool)
                for sensor, block in dataset.blocks.items()
                if block.data.shape[0] > 0
            }
            self._undo_stack = {sensor: [] for sensor in self._active_mask}
            self._redo_stack = {sensor: [] for sensor in self._active_mask}
            self._dataset_version += 1

        if self._warmup_on_load:
            specs = [
                spec
                for sensor in dataset.blocks
                if dataset.blocks[sensor].data.shape[0] > 0
                for spec in default_warmup_specs(sensor)
            ]
            if specs:
                start_background_warmup(
                    self._cache_dir, dataset.blocks, dataset.sensor_configs, dataset.dataset_id, specs
                )
        return dataset

    @property
    def dataset(self) -> LoadedDataset | None:
        with self._lock:
            return self._dataset

    @property
    def dataset_version(self) -> int:
        """Contador monotónico: ``0`` si nunca se cargó nada, incrementa en cada carga.

        Cada ventana/pestaña (Fase 5, §6.1/§6.3 del PROMPT) siembra su propio Store
        ``dataset-version`` con este valor al construir su layout -- así una ventana
        recién abierta ve de inmediato un dataset ya cargado por otra pestaña, sin
        esperar a que el usuario repita "Seleccionar base de datos". No hay empuje en
        tiempo real entre pestañas ya abiertas (limitación reconocida, ver
        FASE5_ENTREGA.md): si se carga un dataset nuevo mientras otra pestaña sigue
        abierta, esa pestaña no se refresca sola hasta recargar.
        """
        with self._lock:
            return self._dataset_version

    @property
    def filter_version(self) -> int:
        """Contador monotónico del estado de filtrado (Fase 6): sube en cada
        aplicar/deshacer/rehacer/restablecer filtro, de cualquier sensor. Sembrado en un
        Store ``filter-version`` igual que ``dataset_version``, para que los callbacks
        de refresco de gráfica #1/#3 sepan cuándo re-renderizar con la máscara nueva.
        """
        with self._lock:
            return self._filter_version

    def get_active_index(self, sensor: SensorName) -> int:
        with self._lock:
            return self._active_index.get(sensor, 0)

    def set_active_index(self, sensor: SensorName, index: int) -> int:
        """Fija el índice de señal activa, saturado a ``[0, n_señales-1]``. Retorna el
        índice efectivamente aplicado (para que la UI pueda reflejarlo)."""
        with self._lock:
            dataset = self._dataset
            if dataset is None or sensor not in dataset.blocks:
                return 0
            n = dataset.blocks[sensor].data.shape[0]
            if n == 0:
                return 0
            clamped = max(0, min(index, n - 1))
            self._active_index[sensor] = clamped
            return clamped

    def nearest_index_for_timestamp(self, sensor: SensorName, timestamp: float) -> int:
        """Índice cronológico global de la señal más cercana a ``timestamp`` -- usado
        por la sincronización click-para-navegar desde las gráficas #1/#3."""
        dataset = self._dataset
        if dataset is None or sensor not in dataset.blocks:
            return 0
        timestamps = dataset.blocks[sensor].timestamps
        if timestamps.shape[0] == 0:
            return 0
        idx = int(np.searchsorted(timestamps, timestamp))
        idx = min(idx, timestamps.shape[0] - 1)
        if idx > 0 and abs(timestamps[idx - 1] - timestamp) <= abs(timestamps[idx] - timestamp):
            idx -= 1
        return idx

    # --- Filtrado cruzado (Fase 6, PROMPT §7.2) --------------------------------------

    def get_active_mask(self, sensor: SensorName) -> np.ndarray:
        """Copia defensiva de la máscara activa (``True`` = señal incluida) -- nunca la
        referencia interna, para que el llamador no pueda mutar el estado por accidente.
        """
        with self._lock:
            if sensor not in self._active_mask:
                return np.array([], dtype=bool)
            return self._active_mask[sensor].copy()

    def apply_filter(self, sensor: SensorName, exclude_indices: np.ndarray) -> int:
        """Excluye ``exclude_indices`` (índices cronológicos globales) de la máscara
        activa del sensor -- no destructivo, solo apaga bits. Empuja el estado ANTERIOR
        a la pila de deshacer y limpia la pila de rehacer (nueva rama de historia,
        invalida cualquier "rehacer" pendiente). Retorna el ``filter_version`` nuevo."""
        with self._lock:
            if sensor not in self._active_mask:
                return self._filter_version
            previous = self._active_mask[sensor].copy()
            new_mask = previous.copy()
            new_mask[np.asarray(exclude_indices, dtype=np.int64)] = False
            self._undo_stack[sensor].append(previous)
            self._redo_stack[sensor].clear()
            self._active_mask[sensor] = new_mask
            self._filter_version += 1
            return self._filter_version

    def undo_filter(self, sensor: SensorName) -> int:
        """Restaura la máscara al estado anterior a la última operación aplicada.
        No-op silencioso (sin excepción) si no hay nada que deshacer -- mismo espíritu
        que ``PreventUpdate`` en los callbacks, para no obligar a manejarlo con try/except."""
        with self._lock:
            if sensor not in self._undo_stack or not self._undo_stack[sensor]:
                return self._filter_version
            current = self._active_mask[sensor]
            previous = self._undo_stack[sensor].pop()
            self._redo_stack[sensor].append(current)
            self._active_mask[sensor] = previous
            self._filter_version += 1
            return self._filter_version

    def redo_filter(self, sensor: SensorName) -> int:
        """Inversa de :meth:`undo_filter`. No-op silencioso si no hay nada que rehacer."""
        with self._lock:
            if sensor not in self._redo_stack or not self._redo_stack[sensor]:
                return self._filter_version
            current = self._active_mask[sensor]
            next_mask = self._redo_stack[sensor].pop()
            self._undo_stack[sensor].append(current)
            self._active_mask[sensor] = next_mask
            self._filter_version += 1
            return self._filter_version

    def reset_filters(self, sensor: SensorName) -> int:
        """Reseteo DURO: máscara a todo-``True`` y limpia ambas pilas por completo --
        "restablecer todo" es una acción de borrón y cuenta nueva, deliberadamente no
        deshacible (distinta de deshacer), y alcanza solo al sensor indicado (UHF y AE
        nunca comparten máscara ni historial, PROMPT §7.2: "un único estado... por
        sensor"). No-op si el sensor no tiene dataset cargado."""
        with self._lock:
            if sensor not in self._active_mask:
                return self._filter_version
            n = self._active_mask[sensor].shape[0]
            self._active_mask[sensor] = np.ones(n, dtype=bool)
            self._undo_stack[sensor] = []
            self._redo_stack[sensor] = []
            self._filter_version += 1
            return self._filter_version

    def get_filter_counts(self, sensor: SensorName) -> tuple[int, int, int]:
        """``(señales_activas, señales_totales, número_de_filtros_aplicados)`` -- el
        indicador permanente que exige el PROMPT §7.2. ``n_operaciones`` es el tamaño de
        la pila de deshacer (una entrada por filtro confirmado, no por deshacer/rehacer)."""
        with self._lock:
            if sensor not in self._active_mask:
                return 0, 0, 0
            mask = self._active_mask[sensor]
            return int(mask.sum()), int(mask.shape[0]), len(self._undo_stack[sensor])

    def can_undo(self, sensor: SensorName) -> bool:
        with self._lock:
            return bool(self._undo_stack.get(sensor))

    def can_redo(self, sensor: SensorName) -> bool:
        with self._lock:
            return bool(self._redo_stack.get(sensor))

    # --- Exportación de datos filtrados (Fase 6) -------------------------------------

    def export_snapshot(self) -> ExportSnapshot | None:
        """Copia barata para exportar en un hilo aparte sin retener el lock del
        proceso durante la escritura a disco (potencialmente varios segundos con
        ``UHF_KS``, ~1,6 GB). ``None`` si no hay dataset cargado -- no hay nada que
        exportar."""
        with self._lock:
            dataset = self._dataset
            if dataset is None:
                return None
            active_masks = {sensor: mask.copy() for sensor, mask in self._active_mask.items()}
        return ExportSnapshot(
            dataset_id=dataset.dataset_id,
            experiment=dataset.experiment,
            normalization_version=NORMALIZATION_VERSION,
            source_path=dataset.source_path,
            blocks=dataset.blocks,
            sensor_configs=dataset.sensor_configs,
            active_masks=active_masks,
            environmental=dataset.environmental,
            events=dataset.events,
        )

    def start_export_status(self, message: str) -> None:
        with self._lock:
            self._export_in_progress = True
            self._export_message = message

    def finish_export_status(self, message: str) -> None:
        with self._lock:
            self._export_in_progress = False
            self._export_message = message

    @property
    def export_status(self) -> tuple[str, bool]:
        """``(mensaje, en_progreso)`` de la última exportación lanzada en este proceso
        -- ``("", False)`` si nunca se exportó nada. Sondeado por un ``dcc.Interval``
        desde que se lanza el hilo de escritura hasta que termina (``ui/callbacks/
        sensor_window_callbacks.py``): el hilo no puede escribir directamente en un
        ``Output`` de Dash, así que el estado se publica aquí y el callback lo lee.
        """
        with self._lock:
            return self._export_message, self._export_in_progress


_STATE: AppState | None = None
_STATE_LOCK = threading.Lock()


def get_state() -> AppState:
    """Acceso perezoso al singleton de proceso -- se crea la primera vez que se pide."""
    global _STATE
    with _STATE_LOCK:
        if _STATE is None:
            _STATE = AppState()
        return _STATE
