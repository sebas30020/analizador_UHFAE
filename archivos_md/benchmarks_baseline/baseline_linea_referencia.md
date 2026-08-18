| Operación | Sensor | Mediana | Min | Max | Objetivo (§9.2) | Veredicto |
|---|---|---:|---:|---:|---:|---|
| Ingesta (matriz global) | UHF | 691 ms | 691 ms | 691 ms | reportar | — |
| Ingesta (matriz global) | AE | 3.30 s | 3.30 s | 3.30 s | reportar | — |
| Render gráfica #1 (dataset completo) | UHF | 26 ms | 26 ms | 27 ms | < 2.00 s | CUMPLE |
| Métrica puntual 'rms' (caché frío) | UHF | 700 ms | 700 ms | 700 ms | reportar | — |
| Métrica puntual 'feq' (caché frío) | UHF | 214 ms | 214 ms | 214 ms | reportar | — |
| Lectura desde caché 'rms' | UHF | 1.4 ms | 1.4 ms | 1.7 ms | < 200 ms | CUMPLE |
| Lectura desde caché 'feq' | UHF | 1.6 ms | 1.6 ms | 2.0 ms | < 200 ms | CUMPLE |
| Render gráfica #3 (puntual, dataset completo) | UHF | 14 ms | 14 ms | 16 ms | reportar | — |
| Render gráfica #3 (grupo, by_time 60 s) | UHF | 14 ms | 14 ms | 14 ms | reportar | — |
| Cambio de señal (gráfica #2) | UHF | 3.1 ms | 3.0 ms | 3.3 ms | < 100 ms | CUMPLE |
| Filtro + propagación (#1 + 2 gráficas #3) | UHF | 56 ms | 56 ms | 70 ms | < 200 ms | CUMPLE |
| Render gráfica #1 (dataset completo) | AE | 29 ms | 28 ms | 30 ms | < 2.00 s | CUMPLE |
| Métrica puntual 'rms' (caché frío) | AE | 496 ms | 496 ms | 496 ms | reportar | — |
| Métrica puntual 'feq' (caché frío) | AE | 1.55 s | 1.55 s | 1.55 s | reportar | — |
| Lectura desde caché 'rms' | AE | 1.4 ms | 1.4 ms | 1.7 ms | < 200 ms | CUMPLE |
| Lectura desde caché 'feq' | AE | 1.8 ms | 1.7 ms | 2.0 ms | < 200 ms | CUMPLE |
| Render gráfica #3 (puntual, dataset completo) | AE | 14 ms | 13 ms | 44 ms | reportar | — |
| Render gráfica #3 (grupo, by_time 60 s) | AE | 15 ms | 14 ms | 18 ms | reportar | — |
| Cambio de señal (gráfica #2) | AE | 4.7 ms | 4.3 ms | 6.2 ms | < 250 ms | CUMPLE |
| Filtro + propagación (#1 + 2 gráficas #3) | AE | 62 ms | 61 ms | 64 ms | < 200 ms | CUMPLE |
