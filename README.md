# Bot de paper trading

Dinero simulado: no se conecta a ningún bróker ni coloca órdenes reales. Lee precios gratuitos de Yahoo, abre y cierra posiciones en su propio libro (`state.json`) y reinvierte lo que gana, porque cada operación se calcula sobre el capital del momento.

- **Cripto** (BTC, ETH, SOL, 24/7): ruptura del máximo de las últimas 4 horas con tendencia de 15 minutos (EMA20 > EMA50). Stop bajo los últimos 8 bars, objetivo 2R.
- **Acciones de EE. UU.** (SPY, QQQ, grandes tecnológicas, petroleras, USO): ruptura del rango de los primeros 15 minutos. Stop en la mitad del rango, objetivo 2R, cierre obligatorio a las 15:55 ET.
- **Riesgo:** un 5 % del capital por operación, como máximo 3 posiciones abiertas, sin entradas nuevas si el día cae un 20 %, sin apalancamiento.

## Uso

```
python bot.py selftest        # comprueba la lógica de salidas
python bot.py tick            # una pasada
python bot.py loop --every 300
```

El resultado queda en `report.md` y cada operación en `trades.jsonl`.

## Que funcione con el ordenador apagado (GitHub Actions, gratis)

1. Crea un repositorio **privado** en GitHub.
2. Sube esta carpeta, `.github/workflows/bot.yml` incluido.
3. En el repositorio, ve a **Actions** y actívalas. El bot hará una pasada cada 5 minutos en los servidores de GitHub y guardará `state.json` y `report.md` en el propio repositorio.

En horas de mucha carga, GitHub puede retrasar las ejecuciones programadas varios minutos.

## Limitaciones

- No tiene en cuenta los festivos de la bolsa; solo descarta los fines de semana.
- Los datos de Yahoo pueden llegar con retraso.
- Simula el precio de entrada con el último precio y no aplica comisiones ni deslizamiento, así que los resultados reales serían algo peores.
