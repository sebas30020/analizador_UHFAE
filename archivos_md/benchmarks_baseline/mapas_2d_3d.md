Mapas de separación #4/#5 — `med_5_ago_3.hdf5`, sensor UHF, 12 484 señales activas.
Mediana de 5 repeticiones (50 para el resaltado). Ejes: 2D = Vmax × RMS, 3D = Vmax × RMS × Vpp.

| Operación | Mediana | Objetivo | Veredicto |
|---|---:|---:|---|
| `build_map_dataset` 2D, caché frío (calcula ambas métricas) | 865 ms | reportar | — |
| `build_map_dataset` 3D, caché frío (solo el eje nuevo) | 469 ms | reportar | — |
| `build_map_dataset` 2D, caché caliente | 30 ms | < 200 ms | CUMPLE |
| `build_map_dataset` 3D, caché caliente | 52 ms | < 200 ms | CUMPLE |
| `build_map_2d_figure` (12 484 puntos) | 15 ms | reportar | — |
| `build_map_3d_figure` (12 484 puntos) | 32 ms | reportar | — |
| `build_map_dataset` 2D con filtro activo (6 242 puntos) | 25 ms | < 200 ms | CUMPLE |
| Resaltado 2D al navegar (`resolve_map_highlight_coords`) | 0.035 ms | < 50 ms | CUMPLE |
| Resaltado 3D al navegar (`resolve_map_highlight_coords`) | 0.035 ms | < 50 ms | CUMPLE |

Señales omitidas por valor no finito con estos ejes: 0 de 12 484 en Vmax, RMS y Vpp.
