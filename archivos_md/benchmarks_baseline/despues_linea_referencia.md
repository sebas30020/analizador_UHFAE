| Operación | Sensor | Mediana | Min | Max | Objetivo (§9.2) | Veredicto |
|---|---|---:|---:|---:|---:|---|
| Ingesta (matriz global) | UHF | 608 ms | 608 ms | 608 ms | reportar | — |
| Ingesta (matriz global) | AE | 3.12 s | 3.12 s | 3.12 s | reportar | — |
| Render gráfica #1 (dataset completo) | UHF | 25 ms | 25 ms | 26 ms | < 2.00 s | CUMPLE |
| Métrica puntual 'rms' (caché frío) | UHF | 545 ms | 545 ms | 545 ms | reportar | — |
| Métrica puntual 'feq' (caché frío) | UHF | 219 ms | 219 ms | 219 ms | reportar | — |
| Lectura desde caché 'rms' | UHF | 1.5 ms | 1.4 ms | 1.8 ms | < 200 ms | CUMPLE |
| Lectura desde caché 'feq' | UHF | 1.6 ms | 1.6 ms | 1.7 ms | < 200 ms | CUMPLE |
| Render gráfica #3 (puntual, dataset completo) | UHF | 14 ms | 13 ms | 16 ms | reportar | — |
| Render gráfica #3 (grupo, by_time 60 s) | UHF | 15 ms | 14 ms | 18 ms | reportar | — |
| Cambio de señal (gráfica #2) | UHF | 3.4 ms | 3.2 ms | 3.6 ms | < 100 ms | CUMPLE |
| Filtro + propagación (#1 + 2 gráficas #3) | UHF | 58 ms | 56 ms | 60 ms | < 200 ms | CUMPLE |
| Línea de referencia: promedio ingenuo (máscara + nanmean) | UHF | 0.0 ms | 0.0 ms | 0.1 ms | reportar | — |
| Línea de referencia: construcción de sumas de prefijo (una vez por serie) | UHF | 0.1 ms | 0.1 ms | 0.1 ms | reportar | — |
| Línea de referencia: promedio con sumas acumuladas (ya construidas) | UHF | 0.0 ms | 0.0 ms | 0.0 ms | reportar | — |
| Línea de referencia: recálculo al cambiar t (3 gráficas #3) | UHF | 0.0 ms | 0.0 ms | 0.0 ms | reportar | — |
| Línea de referencia: conmutar visibilidad (3 gráficas #3) | UHF | 0.0 ms | 0.0 ms | 0.0 ms | < 50 ms | CUMPLE |
| Render gráfica #3 (puntual, línea de referencia activa) | UHF | 14 ms | 13 ms | 15 ms | reportar | — |
| Render gráfica #1 (dataset completo) | AE | 29 ms | 29 ms | 30 ms | < 2.00 s | CUMPLE |
| Métrica puntual 'rms' (caché frío) | AE | 486 ms | 486 ms | 486 ms | reportar | — |
| Métrica puntual 'feq' (caché frío) | AE | 1.48 s | 1.48 s | 1.48 s | reportar | — |
| Lectura desde caché 'rms' | AE | 1.4 ms | 1.4 ms | 1.6 ms | < 200 ms | CUMPLE |
| Lectura desde caché 'feq' | AE | 1.7 ms | 1.7 ms | 1.8 ms | < 200 ms | CUMPLE |
| Render gráfica #3 (puntual, dataset completo) | AE | 13 ms | 13 ms | 14 ms | reportar | — |
| Render gráfica #3 (grupo, by_time 60 s) | AE | 14 ms | 14 ms | 15 ms | reportar | — |
| Cambio de señal (gráfica #2) | AE | 4.3 ms | 4.2 ms | 4.4 ms | < 250 ms | CUMPLE |
| Filtro + propagación (#1 + 2 gráficas #3) | AE | 61 ms | 58 ms | 63 ms | < 200 ms | CUMPLE |
| Línea de referencia: promedio ingenuo (máscara + nanmean) | AE | 0.0 ms | 0.0 ms | 0.1 ms | reportar | — |
| Línea de referencia: construcción de sumas de prefijo (una vez por serie) | AE | 0.1 ms | 0.1 ms | 0.1 ms | reportar | — |
| Línea de referencia: promedio con sumas acumuladas (ya construidas) | AE | 0.0 ms | 0.0 ms | 0.0 ms | reportar | — |
| Línea de referencia: recálculo al cambiar t (3 gráficas #3) | AE | 0.0 ms | 0.0 ms | 0.0 ms | reportar | — |
| Línea de referencia: conmutar visibilidad (3 gráficas #3) | AE | 0.0 ms | 0.0 ms | 0.0 ms | < 50 ms | CUMPLE |
| Render gráfica #3 (puntual, línea de referencia activa) | AE | 13 ms | 13 ms | 14 ms | reportar | — |
