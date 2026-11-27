# Sonda temporal (rama de pruebas, se borrará): ¿qué fuentes públicas de curvas de futuros responden desde GitHub?
import os, re
import requests
H = {"User-Agent": "diagnosticador (github.com/PabloJaca89/diagnosticador)", "Accept": "*/*"}
OUT = "sonda/salida"; os.makedirs(OUT, exist_ok=True)
log = []
def prueba(nombre, url, metodo="GET", **kw):
    try:
        r = requests.request(metodo, url, headers=dict(H, **kw.pop("headers", {})), timeout=25, **kw)
        t = r.text
        log.append(f"{nombre} | {r.status_code} | {len(t)} | {r.headers.get('content-type','')} | {re.sub(r'\s+',' ',t[:250])}")
        open(f"{OUT}/{nombre}.txt", "w", encoding="utf-8", errors="replace").write(t[:300000])
    except Exception as e:
        log.append(f"{nombre} | ERROR | {type(e).__name__}: {str(e)[:200]}")
# ICE (datos retrasados, gratuitos según su política de datos)
for pid, hub, n in [(254, 403, "brent"), (4331, 7979, "ttf")]:
    prueba(f"ice_delayed_{n}", f"https://www.ice.com/marketdata/DelayedMarkets.shtml?getContractsAsJson=&productId={pid}&hubId={hub}")
    prueba(f"ice_api_{n}", f"https://www.ice.com/marketdata/api/productguide/charting/contract-data?productId={pid}&hubId={hub}")
# CNBC
prueba("cnbc", "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol",
       params={"symbols": "@CL.1|@CL.2|@CL.12|@LCO.1|@LCO.2|@LCO.12|@NG.1|@HG.1|.MOVE|@TFM.1",
               "requestMethod": "itv", "noform": "1", "partnerId": "2", "fund": "1", "exthrs": "1", "output": "json", "events": "1"})
# TradingView
prueba("tv_futures", "https://scanner.tradingview.com/futures/scan", "POST",
       json={"symbols": {"tickers": ["NYMEX:CLF2027", "ICEEUR:BRNG2027", "ICEENDEX:TFMF2027", "TVC:MOVE", "CBOT:ZQF2027"], "query": {"types": []}},
             "columns": ["name", "description", "close", "update_mode"]})
# Fed
prueba("fedmarketwatch", "https://fedmarketwatch.com/")
prueba("kalshi_fed", "https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXFED&status=open&limit=200")
open(f"{OUT}/_resumen.txt", "w", encoding="utf-8").write("\n".join(log))
print("\n".join(log))
