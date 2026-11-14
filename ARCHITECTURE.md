# Arquitectura del bot cuantitativo (solo paper trading)

Estado a 2026-11-14. Este documento describe lo que existe, lo que falta y cómo se construirá el resto. Ninguna parte opera con dinero real.

## 1. Auditoría del repositorio

| Archivo | Qué hace | Estado |
| --- | --- | --- |
| `bot.py` (411 líneas) | Bot en vivo: datos de Yahoo, señales, libro simulado en `state.json`, informe | Funciona. 3 reglas: ruptura de apertura en acciones, ruptura cripto (desactivada), ruptura bajista de ETH en corto |
| `review.py` | Revisión cada 5 operaciones cerradas; pausa o reduce el riesgo de una estrategia que pierde | Funciona, 7 tests |
| `quant/db.py`, `quant/data.py` | Base SQLite de precios, descarga incremental, control de calidad, importación del libro | Funciona, 3 tests |
| `.github/workflows/bot.yml` | Ejecuta el bot en GitHub: cada 5 min en sesión de EE. UU., cada hora fuera | Funciona: 69 pasadas la noche del 13 al 14 |
| `.github/workflows/review.yml` | Revisión manual | Funciona |
| `tradingview_strategy.pine` | Versión Pine de la regla cripto, para el Strategy Tester | Referencia |
| `data/quant.db` | 637.166 velas de 34 instrumentos (1d desde 2016, 1h desde 2023-12, 5m desde 2026-08) | No se sube a git; se reconstruye |

**Datos de mercado:** solo Yahoo Finance (endpoints `v8/chart` gratuitos, no oficiales). No hay spread ni libro de órdenes: el spread y el slippage habrá que **estimarlos** (por ejemplo, a partir del rango de la vela y el volumen) y decirlo en cada resultado.

**Memoria persistente:** sí, fuera del chat. `state.json` y `trades.jsonl` (en git) más `quant.db` (local). Todavía faltan campos: versión de estrategia, comisiones, slippage, indicadores, comprobaciones de riesgo, validación y análisis posterior.

**Dependencias:** solo la biblioteca estándar de Python. En este PC no hay pandas, NumPy, DuckDB, pyarrow, SciPy ni scikit-learn (pip sí está disponible).

**Errores y carencias detectados:**
- No existe un motor de backtest común: los backtests de estos días fueron scripts sueltos, con lógica distinta a la del bot.
- Las reglas del bot se aprobaron a mano; no hay validador.
- `bot.py` no conoce los festivos de la bolsa.
- Las velas diarias de divisas de Yahoo son incoherentes (USDJPY: 162 velas con el máximo por debajo del cierre): usar 1h.
- El historial de 5 minutos solo llega a 60 días y el de 1 hora a 730: el walk-forward intradía tiene poca profundidad.
- Las sesiones de TradingView y GitHub en el panel del navegador caducan; el bot no depende de ellas.

## 2. Skills

| Skill | Verificada | Uso |
| --- | --- | --- |
| Paquete `trader-*` (11 skills, idénticas al repositorio day-trader-skill) | Sí, instaladas y leídas | Ideas y comprobaciones de referencia: escáner, Time Machine (simulación + historial), reglas de riesgo |
| `superpowers:test-driven-development`, `superpowers:writing-plans`, `superpowers:verification-before-completion` | Sí, listadas | Disciplina de desarrollo: test antes que código, plan por fases, verificar antes de dar algo por hecho |
| `dataviz` | Sí, listada | Gráficos de resultados y del informe |
| `anthropic-skills:findskills` | Instalada, **no utilizable**: documenta un comando Node.js que no existe en este PC | Ninguno |
| Búsqueda de skills de claude.ai (backtesting, quant, estadística) | Ejecutada | Sin resultados; no se instala nada |

## 3. Módulos nuevos

```
quant/
  data.py          (existe)  descarga y calidad
  db.py            (existe)  SQLite: bars, trades ... + tablas nuevas
  costs.py         spread, comisión y slippage estimados por instrumento
  engine.py        motor único de simulación barra a barra (backtest = vivo)
  markets.py       selección de mercados por condiciones estadísticas
  generator/       CREADOR: familias de estrategias, hipótesis, propuestas
  validator/       VALIDADOR: criterios, pruebas, veredictos
  ledger.py        memoria de operaciones y análisis posterior
```

### 3.1 Selección de mercados (`markets.py`)

Para cada instrumento y temporalidad calcula volatilidad realizada, liquidez (volumen en dólares), spread y slippage estimados, tendencia (pendiente y ADX), momentum (12-1 y a corto), regularidad estadística (autocorrelación de rendimientos, exponente de Hurst, razón de varianzas), perfil intradía por franja horaria y régimen (alta o baja volatilidad, tendencia o rango). Puntúa la **operabilidad para cada familia de estrategias**, no la subida reciente. Si nada supera el umbral, la salida es "no operar".

### 3.2 Creador de estrategias (`generator/`)

Familias: seguimiento de tendencia, momentum, ruptura, reversión a la media, VWAP, ruptura del rango de apertura, ruptura de volatilidad, volumen, indicadores técnicos y cambio de régimen.

Cada estrategia es una **ficha versionada** (`StrategySpec`) con: hipótesis estadística, entrada, salida, stop, take profit, tamaño de posición, condiciones de no operación, costes, parámetros y rejilla de búsqueda. La versión es el **hash SHA-256** de la ficha más el código de la regla. Cualquier cambio produce una versión nueva.

El creador solo puede escribir en `proposals/`. **Solo ve datos hasta la fecha de corte** (ver 3.3).

### 3.3 Validador independiente (`validator/`)

Separación efectiva, no solo de nombre:

1. **Criterios congelados antes de evaluar.** `validator/criteria.json` contiene umbrales numéricos: mínimo de operaciones, Sharpe deflactado, PBO, p-valor de permutación, eficiencia walk-forward, meseta de parámetros, resultado con costes x2, número de regímenes con resultado positivo. Su hash se registra en el log al principio de cada evaluación. Si el hash cambia, todas las aprobaciones anteriores quedan en revisión.
2. **Caja fuerte de datos.** Los datos posteriores a la fecha de corte (los últimos 6 meses en 1d y 1h, los últimos 15 días en 5m) solo los lee el validador. El creador no puede optimizar sobre ellos.
3. **Pruebas propias.** El validador vuelve a ejecutar el backtest con su propia instancia del motor y su propio modelo de costes. No acepta las cifras del creador.
4. **Comprobaciones:** procedencia y calidad de los datos; coherencia de la hipótesis (¿el resultado sale de donde dice la hipótesis?); look-ahead (señal calculada con datos desplazados una vela, que debe dar el mismo resultado); fuga de datos (sin indicadores con información futura); número de observaciones; fuera de muestra; walk-forward con matriz; estabilidad de parámetros; sensibilidad a costes; robustez por periodos y por regímenes; rendimiento ajustado al riesgo; permutación (azar); comparación con benchmarks (comprar y mantener, aleatorio con el mismo número de operaciones).
5. **Veredicto:** `APPROVED`, `PROBATION`, `REJECTED` o `NEEDS MORE EVIDENCE`, con métricas, motivos, fecha, versión de la estrategia, hash de los criterios y hash de los datos. `PROBATION` = no pasa todas las pruebas, pero gana dinero **fuera de muestra y después de costes**.
6. **Registro inmutable:** `validations.jsonl`, solo se añaden líneas. Cada línea lleva el hash de la anterior (cadena de hashes) y se sube a git, así que cualquier edición rompe la cadena y es visible en el historial.
7. **Selección de la más rentable (petición del usuario, 2026-11-14):** el validador ordena todas las estrategias por su resultado **fuera de muestra, después de costes**, nunca por el backtest con el que se diseñaron, que siempre sale inflado. El bot opera la primera de la lista:
   - si es `APPROVED`, con el riesgo normal (2% por operación);
   - si es `PROBATION`, con riesgo reducido (0,5% por operación), y sube a normal si en vivo confirma lo esperado.
   Solo queda sin operar si incluso la mejor pierde dinero fuera de muestra después de costes, porque en ese caso "la más rentable" sería solo la que menos pierde. Una estrategia sin veredicto, `REJECTED` o con un fallo del validador no entra en la lista.
8. **Un rechazo no se convierte en aprobación:** solo una versión nueva con una validación nueva y completa.
9. **Control técnico:** un test de CI falla si `generator/` importa algo de escritura del validador o toca `criteria.json` o `validations.jsonl`.

**Límite honesto:** en un repositorio con un solo desarrollador (yo), la independencia la garantizan el proceso y las comprobaciones automáticas, no personas distintas. Para reforzarla, conviene que tú actives en GitHub la protección de rama con CODEOWNERS sobre `validator/criteria.json`: así un cambio de criterios necesitaría tu aprobación.

### 3.4 Base de datos (objetivo: más de un millón de registros reales)

| Capa | Contenido | Tecnología |
| --- | --- | --- |
| 1. Datos de mercado | Velas OHLCV de Yahoo | SQLite ahora; Parquet + DuckDB si se instalan |
| 2. Oportunidades reconstruidas | Cada vez que una regla habría disparado en el histórico, con su contexto | SQLite, tabla `opportunities` |
| 3. Operaciones de backtest | Resultado de cada oportunidad simulada, por estrategia y versión | `backtest_trades` |
| 4. Paper trading | Operaciones del bot en vivo | `trades.jsonl` (git) → `trades` |
| 5. Experimentos y validaciones | Ejecuciones, parámetros, veredictos | `experiments`, `validations.jsonl` |

Cada tabla lleva su capa en el nombre; nunca se mezclan.

Para pasar el millón con datos verificables: ampliar a unas 100 acciones líquidas en 1h (unas 5.000 velas cada una, 730 días) y 5m (unas 4.700 cada una) suma más de 900.000 velas adicionales. No se inventa ningún registro.

### 3.5 Memoria de operaciones y aprendizaje

La tabla `trades` se amplía con: estrategia y versión, stop, take profit, tamaño, riesgo, comisión y slippage estimados, régimen e indicadores al entrar, motivos de entrada y salida, resultado de la comprobación de riesgo, veredicto de validación vigente y análisis posterior. Habrá consultas por fecha, activo, estrategia y resultado, y una tabla `changes` con cada cambio de estrategia, parámetro o configuración.

**Análisis posterior de cada operación:** ¿la señal cumplía las reglas? ¿El riesgo era el previsto? ¿El slippage real está dentro del estimado? ¿El resultado cae dentro de la banda de Montecarlo del validador? ¿Ha cambiado el régimen? ¿Hubo huecos o errores en los datos?

**Aprendizaje estadístico:** se comparan los resultados en vivo con la distribución esperada (test de una cola sobre la media en R y bandas de Montecarlo). Se pausa la estrategia si se sale de lo esperado. **No se reoptimiza con los datos en vivo:** cualquier cambio vuelve al creador como versión nueva.

## 4. Plan por fases

1. **Hecho:** almacén de datos y descarga.
2. **Motor + costes:** un único simulador para backtest y bot, con tests de look-ahead.
3. **Validador:** criterios congelados, caja fuerte de datos, pruebas, veredictos y registro encadenado. Va **antes** que el creador, para que no haya estrategias sin juez.
4. **Creador:** 10 familias con hipótesis; la primera tanda pasa por el validador.
5. **Selección de mercados.**
6. **Memoria ampliada y análisis posterior;** el bot solo opera lo `APPROVED`.
7. **Ampliar datos a más de 1 millón de velas** y, si se instalan, Parquet y DuckDB.

Todo resultado de estas fases es simulado. Muchas estrategias no pasarán todas las pruebas; el bot opera igualmente la más rentable fuera de muestra, con el riesgo que corresponda a su veredicto (punto 7 de 3.3).
