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
from typing import Any, Callable, Sequence

from cache.backend import CacheBackend, SqliteHdf5CacheBackend
from cache.service import get_or_compute_puntual
from cache.warmup import default_warmup_specs, start_background_warmup, warm_cache
from core.metric_filters import (
    MetricCondition,
    combine_conditions,
    condition_key,
    detect_contradictions,
    evaluate_condition,
    scatter_to_full,
)
from core.models import EnvironmentalSeries, EventSeries, SensorConfig, SensorName, SignalBlock, load_sensor_configs
from core.normalization import NORMALIZATION_VERSION
from data.export import ExportPartition
from data.ingest import ingest_experiment
from data.readers.factory import open_reader
from data.readers.filtered_export_reader import FilteredExportReader
from ui.jobs import JobCancelledError, get_job_service

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
    # Partición efectivamente cargada, o ``None`` si el origen no es una exportación
    # filtrada (``data/export.py``) y por lo tanto no tiene particiones. Es lo que
    # permite al selector "Partición a cargar" saber si puede recargar en vivo o si
    # debe quedarse inerte -- ver ``ui/callbacks/sensor_window_callbacks.py``.
    partition: ExportPartition | None = None


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


def prepare_dataset(
    path: str | Path,
    partition: ExportPartition = "resultantes",
    sensors_config_path: Path | None = None,
) -> LoadedDataset:
    """Ingiere un archivo de origen fuera de cualquier lock.

    Carga configuraciones de sensores, abre el lector correspondiente, realiza la
    ingesta del experimento y calcula t0. Retorna un objeto LoadedDataset listo
    para ser publicado en AppState.
    """
    cfg_path = sensors_config_path or DEFAULT_SENSORS_CONFIG_PATH
    sensor_configs = load_sensor_configs(cfg_path)
    with open_reader(path, partition=partition) as reader:
        effective_partition = partition if isinstance(reader, FilteredExportReader) else None
        experiments = reader.list_experiments()
        if not experiments:
            raise ValueError(f"El archivo no contiene ningún experimento: {path}")
        experiment = experiments[0]

        sensores_disponibles = [s for s in sensor_configs if s in reader.available_sensors()]
        result = ingest_experiment(reader, experiment, sensores_disponibles)

        for sensor in sensores_disponibles:
            overrides = reader.sensor_config_overrides(experiment, sensor)
            if overrides:
                effective = replace(sensor_configs[sensor], **overrides)
                if effective != sensor_configs[sensor]:
                    _logger.info(
                        "Perfil efectivo de %s (dataset %s): %s", sensor, reader.dataset_id, overrides
                    )
                sensor_configs = {**sensor_configs, sensor: effective}

    return LoadedDataset(
        dataset_id=result.dataset_id,
        source_path=Path(path),
        experiment=experiment,
        sensor_configs=sensor_configs,
        blocks=result.sensors,
        environmental=result.environmental,
        events=result.events,
        t0=compute_t0(result.sensors, result.environmental, result.events),
        partition=effective_partition,
    )


@dataclass(frozen=True)
class FilterSnapshot:
    """Instantánea inmutable del estado de filtrado para operaciones de deshacer/rehacer."""
    manual_mask: np.ndarray
    metric_filters: tuple[MetricCondition, ...]
    metric_mask: np.ndarray
    active_mask: np.ndarray


@dataclass(frozen=True)
class MetricFilterResult:
    """Resultado estructurado de operaciones de filtrado por métricas."""
    filter_version: int
    active_count: int
    total_count: int
    contradiction_warning: str | None = None
    error: str | None = None


def evaluate_metric_conditions(
    cache: CacheBackend,
    block: SignalBlock,
    sensor_config: SensorConfig,
    dataset_id: str,
    conditions: Sequence[MetricCondition],
) -> tuple[np.ndarray, str | None]:
    """Evalúa una secuencia de condiciones declarativas sobre métricas puntuales.

    - Agrupa por (metric_name, params) para calcular cada métrica una única vez sobre el conjunto completo.
    - Aplica scatter_to_full para alinear con las señales válidas.
    - Combina en AND con combine_conditions.
    - Detecta contradicciones lógicas entre condiciones.
    """
    n_total = block.data.shape[0]
    if not conditions:
        return np.ones(n_total, dtype=bool), None

    valid_idx = np.where(block.valid_mask)[0]
    warnings = detect_contradictions(conditions)
    warning_msg = " | ".join(str(w) for w in warnings) if warnings else None

    cached_metric_values: dict[tuple[str, tuple[tuple[str, Any], ...]], np.ndarray] = {}
    for cond in conditions:
        m_name = cond.metric_name or cond.metric_id
        key = (m_name, cond.params)
        if key not in cached_metric_values:
            params_dict = dict(cond.params) if cond.params else None
            _t, vals = get_or_compute_puntual(
                cache=cache,
                block=block,
                sensor_config=sensor_config,
                dataset_id=dataset_id,
                metric_id=m_name,
                params=params_dict,
                active_mask=None,
            )
            cached_metric_values[key] = vals

    condition_masks: list[np.ndarray] = []
    for cond in conditions:
        m_name = cond.metric_name or cond.metric_id
        key = (m_name, cond.params)
        vals = cached_metric_values[key]
        partial_mask = evaluate_condition(cond, vals)
        full_mask = scatter_to_full(partial_mask, valid_idx, n_total)
        condition_masks.append(full_mask)

    final_mask = combine_conditions(condition_masks, n_total)
    return final_mask, warning_msg


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
        # Señal de cancelación del precalentamiento en curso. Cargar un dataset nuevo
        # deja obsoleto al anterior: su hilo seguiría calculando métricas que ya nadie
        # va a mirar y, peor, manteniendo viva su matriz completa (medido: 234 MB con
        # test-6.h5, ~1 GB con med_5_ago_3.hdf5) hasta terminar -- hasta ~63 s después.
        # Ver archivos_md/CONTINUAR_DIAGNOSTICO_RENDIMIENTO.md §7.
        self._warmup_cancel: threading.Event | None = None
        self._dataset_version = 0  # 0 = "sin dataset cargado"; se incrementa en cada load_dataset()
        self.cache = SqliteHdf5CacheBackend(cache_dir)

        # Fase 6 + Etapa 2: máscaras de filtrado y condiciones declarativas por sensor.
        # Separación entre exclusión manual geométrica (_manual_mask) y filtros por métricas (_metric_filters).
        # _active_mask es la composición ya materializada (_manual_mask & _metric_mask).
        # Las pilas _undo_stack y _redo_stack almacenan snapshots completos (FilterSnapshot).
        self._manual_mask: dict[SensorName, np.ndarray] = {}
        self._metric_filters: dict[SensorName, tuple[MetricCondition, ...]] = {}
        self._metric_mask: dict[SensorName, np.ndarray] = {}
        self._active_mask: dict[SensorName, np.ndarray] = {}
        self._undo_stack: dict[SensorName, list[FilterSnapshot]] = {}
        self._redo_stack: dict[SensorName, list[FilterSnapshot]] = {}
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
        self._merge_in_progress = False
        self._merge_message = ""

    def publish_dataset(self, dataset: LoadedDataset) -> int:
        """Publica bajo lock un dataset ya preparado en memoria e incrementa la versión."""
        with self._lock:
            self._dataset = dataset
            self._active_index = {sensor: 0 for sensor, block in dataset.blocks.items() if block.data.shape[0] > 0}
            # Un dataset nuevo invalida cualquier filtro previo (son señales distintas) --
            # reseteo duro, igual que el índice de navegación activa.
            self._manual_mask = {
                sensor: np.ones(block.data.shape[0], dtype=bool)
                for sensor, block in dataset.blocks.items()
                if block.data.shape[0] > 0
            }
            self._metric_filters = {sensor: () for sensor in self._manual_mask}
            self._metric_mask = {
                sensor: np.ones(block.data.shape[0], dtype=bool)
                for sensor, block in dataset.blocks.items()
                if block.data.shape[0] > 0
            }
            self._active_mask = {
                sensor: np.ones(block.data.shape[0], dtype=bool)
                for sensor, block in dataset.blocks.items()
                if block.data.shape[0] > 0
            }
            self._undo_stack = {sensor: [] for sensor in self._active_mask}
            self._redo_stack = {sensor: [] for sensor in self._active_mask}
            self._dataset_version += 1
            new_version = self._dataset_version

        if self._warmup_on_load:
            self._trigger_warmup(dataset)

        return new_version

    def _trigger_warmup(self, dataset: LoadedDataset) -> None:
        specs = [
            spec
            for sensor in dataset.blocks
            if dataset.blocks[sensor].data.shape[0] > 0
            for spec in default_warmup_specs(sensor)
        ]
        if not specs:
            return

        cancel = threading.Event()
        with self._lock:
            # Se vuelve a señalar bajo el mismo lock que publica el nuevo Event.
            # No es redundante con el _cancel_pending_warmup() del inicio: entre
            # aquel y esta línea pasa la ingesta entera, tiempo de sobra para que
            # OTRA carga haya publicado su propio Event.
            if self._warmup_cancel is not None:
                self._warmup_cancel.set()
            self._warmup_cancel = cancel

        job_service = get_job_service()
        job_service.cancel_kind("warmup")

        cache_dir = self._cache_dir
        blocks = dataset.blocks
        sensor_configs = dataset.sensor_configs
        dataset_id = dataset.dataset_id

        def _warmup_worker(cancel_token: threading.Event, progress_cb: Callable[[float, str], None]) -> list[str]:
            backend = SqliteHdf5CacheBackend(cache_dir)
            return warm_cache(backend, blocks, sensor_configs, dataset_id, specs, cancelled=cancel_token)

        job_service.submit(
            "warmup",
            _warmup_worker,
            label=f"Precalentando caché ({dataset.dataset_id})",
            cancel_token=cancel,
        )

    def load_dataset(self, path: str | Path, partition: ExportPartition = "resultantes") -> LoadedDataset:
        """Ingiere un archivo de origen síncronamente y lo deja como dataset activo."""
        self._cancel_pending_warmup()
        dataset = prepare_dataset(path, partition=partition, sensors_config_path=self._sensors_config_path)
        self.publish_dataset(dataset)
        return dataset

    def begin_load(self, path: str | Path, partition: ExportPartition = "resultantes") -> str:
        """Inicia la carga de un dataset en segundo plano mediante JobService.

        Retorna el job_id asignado. La publicación del dataset ocurrirá al completarse
        la ingesta de forma diferida bajo el lock del proceso, incrementando `dataset_version`.
        """
        self._cancel_pending_warmup()
        job_service = get_job_service()
        job_service.cancel_kind("load_dataset")

        cfg_path = self._sensors_config_path
        filename = Path(path).name

        def _load_job(cancel_token: threading.Event, progress_cb: Callable[[float, str], None]) -> LoadedDataset:
            progress_cb(0.1, f"Abriendo lector para {filename}...")
            if cancel_token.is_set():
                raise JobCancelledError("Carga cancelada antes de iniciar.")
            dataset = prepare_dataset(path, partition=partition, sensors_config_path=cfg_path)
            if cancel_token.is_set():
                raise JobCancelledError("Carga cancelada durante la ingesta.")
            progress_cb(0.85, "Publicando dataset...")
            self.publish_dataset(dataset)
            progress_cb(1.0, f"Dataset {dataset.dataset_id} publicado.")
            return dataset

        return job_service.submit(
            "load_dataset",
            _load_job,
            label=f"Cargando {filename} ({partition})",
        )

    def _cancel_pending_warmup(self) -> None:
        """Señala al precalentamiento en curso (si lo hay) que abandone."""
        with self._lock:
            cancel, self._warmup_cancel = self._warmup_cancel, None
        if cancel is not None:
            cancel.set()
        try:
            get_job_service().cancel_kind("warmup")
        except Exception:
            pass

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
        manual del sensor -- no destructivo, solo apaga bits.
        Compone eager: _active_mask = _manual_mask & _metric_mask.
        Empuja el FilterSnapshot ANTERIOR a la pila de deshacer y limpia la pila de rehacer.
        Retorna el ``filter_version`` nuevo."""
        with self._lock:
            if sensor not in self._active_mask or sensor not in self._manual_mask:
                return self._filter_version
            current_snapshot = FilterSnapshot(
                manual_mask=self._manual_mask[sensor].copy(),
                metric_filters=self._metric_filters.get(sensor, ()),
                metric_mask=self._metric_mask[sensor].copy(),
                active_mask=self._active_mask[sensor].copy(),
            )
            new_manual = self._manual_mask[sensor].copy()
            new_manual[np.asarray(exclude_indices, dtype=np.int64)] = False
            self._manual_mask[sensor] = new_manual
            new_active = new_manual & self._metric_mask[sensor]
            self._active_mask[sensor] = new_active

            self._undo_stack[sensor].append(current_snapshot)
            if len(self._undo_stack[sensor]) > 50:
                self._undo_stack[sensor].pop(0)
            self._redo_stack[sensor].clear()
            self._filter_version += 1
            return self._filter_version

    def undo_filter(self, sensor: SensorName) -> int:
        """Restaura la máscara al estado anterior a la última operación aplicada.
        No-op silencioso (sin excepción) si no hay nada que deshacer."""
        with self._lock:
            if sensor not in self._undo_stack or not self._undo_stack[sensor]:
                return self._filter_version
            current_snapshot = FilterSnapshot(
                manual_mask=self._manual_mask[sensor].copy(),
                metric_filters=self._metric_filters.get(sensor, ()),
                metric_mask=self._metric_mask[sensor].copy(),
                active_mask=self._active_mask[sensor].copy(),
            )
            previous_snapshot = self._undo_stack[sensor].pop()
            self._redo_stack[sensor].append(current_snapshot)
            if len(self._redo_stack[sensor]) > 50:
                self._redo_stack[sensor].pop(0)

            self._manual_mask[sensor] = previous_snapshot.manual_mask.copy()
            self._metric_filters[sensor] = previous_snapshot.metric_filters
            self._metric_mask[sensor] = previous_snapshot.metric_mask.copy()
            self._active_mask[sensor] = previous_snapshot.active_mask.copy()
            self._filter_version += 1
            return self._filter_version

    def redo_filter(self, sensor: SensorName) -> int:
        """Inversa de :meth:`undo_filter`. No-op silencioso si no hay nada que rehacer."""
        with self._lock:
            if sensor not in self._redo_stack or not self._redo_stack[sensor]:
                return self._filter_version
            current_snapshot = FilterSnapshot(
                manual_mask=self._manual_mask[sensor].copy(),
                metric_filters=self._metric_filters.get(sensor, ()),
                metric_mask=self._metric_mask[sensor].copy(),
                active_mask=self._active_mask[sensor].copy(),
            )
            next_snapshot = self._redo_stack[sensor].pop()
            self._undo_stack[sensor].append(current_snapshot)
            if len(self._undo_stack[sensor]) > 50:
                self._undo_stack[sensor].pop(0)

            self._manual_mask[sensor] = next_snapshot.manual_mask.copy()
            self._metric_filters[sensor] = next_snapshot.metric_filters
            self._metric_mask[sensor] = next_snapshot.metric_mask.copy()
            self._active_mask[sensor] = next_snapshot.active_mask.copy()
            self._filter_version += 1
            return self._filter_version

    def reset_filters(self, sensor: SensorName) -> int:
        """Reseteo DURO: máscara a todo-``True`` y limpia ambas pilas por completo --
        "restablecer todo" es una acción de borrón y cuenta nueva, deliberadamente no
        deshacible (distinta de deshacer), y alcanza solo al sensor indicado.
        No-op si el sensor no tiene dataset cargado."""
        with self._lock:
            if sensor not in self._active_mask:
                return self._filter_version
            n = self._active_mask[sensor].shape[0]
            self._manual_mask[sensor] = np.ones(n, dtype=bool)
            self._metric_filters[sensor] = ()
            self._metric_mask[sensor] = np.ones(n, dtype=bool)
            self._active_mask[sensor] = np.ones(n, dtype=bool)
            self._undo_stack[sensor] = []
            self._redo_stack[sensor] = []
            self._filter_version += 1
            return self._filter_version

    def add_metric_filter(self, sensor: SensorName, condition: MetricCondition) -> MetricFilterResult:
        """Añade una condición de filtrado declarativo por métrica.
        Paso 1: lectura de candidatos bajo lock.
        Paso 2: evaluación fuera de lock (no bloquea el proceso Dash).
        Paso 3: validación de integridad y publicación atómica bajo lock.
        """
        with self._lock:
            if self._dataset is None or sensor not in self._dataset.blocks:
                cnt = int(self._active_mask[sensor].sum()) if sensor in self._active_mask else 0
                tot = int(self._active_mask[sensor].shape[0]) if sensor in self._active_mask else 0
                return MetricFilterResult(filter_version=self._filter_version, active_count=cnt, total_count=tot, error="Sin dataset cargado.")
            block = self._dataset.blocks[sensor]
            sensor_config = self._dataset.sensor_configs[sensor]
            dataset_id = self._dataset.dataset_id
            expected_len = block.data.shape[0]
            current_filters = self._metric_filters.get(sensor, ())
            cond_k = condition_key(condition)
            if any(condition_key(c) == cond_k for c in current_filters):
                cnt = int(self._active_mask[sensor].sum())
                tot = int(self._active_mask[sensor].shape[0])
                return MetricFilterResult(filter_version=self._filter_version, active_count=cnt, total_count=tot)
            candidate_filters = (*current_filters, condition)

        try:
            new_metric_mask, warning = evaluate_metric_conditions(
                cache=self.cache,
                block=block,
                sensor_config=sensor_config,
                dataset_id=dataset_id,
                conditions=candidate_filters,
            )
        except Exception as e:
            _logger.exception("Error evaluando filtros de métricas: %s", e)
            with self._lock:
                cnt = int(self._active_mask[sensor].sum()) if sensor in self._active_mask else 0
                tot = int(self._active_mask[sensor].shape[0]) if sensor in self._active_mask else 0
                return MetricFilterResult(filter_version=self._filter_version, active_count=cnt, total_count=tot, error=str(e))

        with self._lock:
            if (
                self._dataset is None
                or self._dataset.dataset_id != dataset_id
                or sensor not in self._manual_mask
                or self._manual_mask[sensor].shape[0] != expected_len
                or new_metric_mask.shape[0] != expected_len
            ):
                cnt = int(self._active_mask[sensor].sum()) if sensor in self._active_mask else 0
                tot = int(self._active_mask[sensor].shape[0]) if sensor in self._active_mask else 0
                return MetricFilterResult(
                    filter_version=self._filter_version,
                    active_count=cnt,
                    total_count=tot,
                    error="Dataset modificado durante la evaluación.",
                )

            current_snapshot = FilterSnapshot(
                manual_mask=self._manual_mask[sensor].copy(),
                metric_filters=self._metric_filters.get(sensor, ()),
                metric_mask=self._metric_mask[sensor].copy(),
                active_mask=self._active_mask[sensor].copy(),
            )
            self._undo_stack[sensor].append(current_snapshot)
            if len(self._undo_stack[sensor]) > 50:
                self._undo_stack[sensor].pop(0)
            self._redo_stack[sensor].clear()

            self._metric_filters[sensor] = candidate_filters
            self._metric_mask[sensor] = new_metric_mask
            new_active = self._manual_mask[sensor] & new_metric_mask
            self._active_mask[sensor] = new_active
            self._filter_version += 1

            return MetricFilterResult(
                filter_version=self._filter_version,
                active_count=int(new_active.sum()),
                total_count=int(new_active.shape[0]),
                contradiction_warning=warning,
            )

    def remove_metric_filter(self, sensor: SensorName, key: str) -> MetricFilterResult:
        """Elimina una condición de filtrado identificada por su clave canónica estable."""
        with self._lock:
            if self._dataset is None or sensor not in self._dataset.blocks:
                cnt = int(self._active_mask[sensor].sum()) if sensor in self._active_mask else 0
                tot = int(self._active_mask[sensor].shape[0]) if sensor in self._active_mask else 0
                return MetricFilterResult(filter_version=self._filter_version, active_count=cnt, total_count=tot, error="Sin dataset cargado.")
            block = self._dataset.blocks[sensor]
            sensor_config = self._dataset.sensor_configs[sensor]
            dataset_id = self._dataset.dataset_id
            expected_len = block.data.shape[0]
            current_filters = self._metric_filters.get(sensor, ())
            candidate_filters = tuple(c for c in current_filters if condition_key(c) != key)
            if len(candidate_filters) == len(current_filters):
                cnt = int(self._active_mask[sensor].sum())
                tot = int(self._active_mask[sensor].shape[0])
                return MetricFilterResult(filter_version=self._filter_version, active_count=cnt, total_count=tot)

        if not candidate_filters:
            new_metric_mask = np.ones(expected_len, dtype=bool)
            warning: str | None = None
        else:
            try:
                new_metric_mask, warning = evaluate_metric_conditions(
                    cache=self.cache,
                    block=block,
                    sensor_config=sensor_config,
                    dataset_id=dataset_id,
                    conditions=candidate_filters,
                )
            except Exception as e:
                _logger.exception("Error evaluando filtros de métricas tras remoción: %s", e)
                with self._lock:
                    cnt = int(self._active_mask[sensor].sum()) if sensor in self._active_mask else 0
                    tot = int(self._active_mask[sensor].shape[0]) if sensor in self._active_mask else 0
                    return MetricFilterResult(filter_version=self._filter_version, active_count=cnt, total_count=tot, error=str(e))

        with self._lock:
            if (
                self._dataset is None
                or self._dataset.dataset_id != dataset_id
                or sensor not in self._manual_mask
                or self._manual_mask[sensor].shape[0] != expected_len
                or new_metric_mask.shape[0] != expected_len
            ):
                cnt = int(self._active_mask[sensor].sum()) if sensor in self._active_mask else 0
                tot = int(self._active_mask[sensor].shape[0]) if sensor in self._active_mask else 0
                return MetricFilterResult(
                    filter_version=self._filter_version,
                    active_count=cnt,
                    total_count=tot,
                    error="Dataset modificado durante la evaluación.",
                )

            current_snapshot = FilterSnapshot(
                manual_mask=self._manual_mask[sensor].copy(),
                metric_filters=self._metric_filters.get(sensor, ()),
                metric_mask=self._metric_mask[sensor].copy(),
                active_mask=self._active_mask[sensor].copy(),
            )
            self._undo_stack[sensor].append(current_snapshot)
            if len(self._undo_stack[sensor]) > 50:
                self._undo_stack[sensor].pop(0)
            self._redo_stack[sensor].clear()

            self._metric_filters[sensor] = candidate_filters
            self._metric_mask[sensor] = new_metric_mask
            new_active = self._manual_mask[sensor] & new_metric_mask
            self._active_mask[sensor] = new_active
            self._filter_version += 1

            return MetricFilterResult(
                filter_version=self._filter_version,
                active_count=int(new_active.sum()),
                total_count=int(new_active.shape[0]),
                contradiction_warning=warning,
            )

    def clear_metric_filters(self, sensor: SensorName) -> MetricFilterResult:
        """Elimina todos los filtros por métrica del sensor, preservando las exclusiones manuales."""
        with self._lock:
            if sensor not in self._active_mask or sensor not in self._manual_mask:
                return MetricFilterResult(filter_version=self._filter_version, active_count=0, total_count=0)
            if not self._metric_filters.get(sensor, ()):
                cnt = int(self._active_mask[sensor].sum())
                tot = int(self._active_mask[sensor].shape[0])
                return MetricFilterResult(filter_version=self._filter_version, active_count=cnt, total_count=tot)

            current_snapshot = FilterSnapshot(
                manual_mask=self._manual_mask[sensor].copy(),
                metric_filters=self._metric_filters.get(sensor, ()),
                metric_mask=self._metric_mask[sensor].copy(),
                active_mask=self._active_mask[sensor].copy(),
            )
            self._undo_stack[sensor].append(current_snapshot)
            if len(self._undo_stack[sensor]) > 50:
                self._undo_stack[sensor].pop(0)
            self._redo_stack[sensor].clear()

            n = self._manual_mask[sensor].shape[0]
            self._metric_filters[sensor] = ()
            self._metric_mask[sensor] = np.ones(n, dtype=bool)
            new_active = self._manual_mask[sensor].copy()
            self._active_mask[sensor] = new_active
            self._filter_version += 1

            return MetricFilterResult(
                filter_version=self._filter_version,
                active_count=int(new_active.sum()),
                total_count=int(new_active.shape[0]),
            )

    def get_metric_filters(self, sensor: SensorName) -> tuple[MetricCondition, ...]:
        with self._lock:
            return self._metric_filters.get(sensor, ())

    def get_filter_version(self) -> int:
        with self._lock:
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

    def start_merge_status(self, message: str) -> None:
        with self._lock:
            self._merge_in_progress = True
            self._merge_message = message

    def finish_merge_status(self, message: str) -> None:
        with self._lock:
            self._merge_in_progress = False
            self._merge_message = message

    @property
    def merge_status(self) -> tuple[str, bool]:
        """``(mensaje, en_progreso)`` de la última fusión de bases de datos lanzada en este
        proceso -- ``("", False)`` si nunca se fusionó nada. Sondeado por un
        ``dcc.Interval`` de forma análoga a ``export_status``.
        """
        with self._lock:
            return self._merge_message, self._merge_in_progress


_STATE: AppState | None = None
_STATE_LOCK = threading.Lock()


def get_state() -> AppState:
    """Acceso perezoso al singleton de proceso -- se crea la primera vez que se pide."""
    global _STATE
    with _STATE_LOCK:
        if _STATE is None:
            _STATE = AppState()
        return _STATE
