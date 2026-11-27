# Sonda temporal (rama de pruebas, se borrará) · ronda 2
import os, re
import requests
H = {"User-Agent": "diagnosticador (github.com/PabloJaca89/diagnosticador)", "Accept": "*/*"}
OUT = "sonda/salida2"; os.makedirs(OUT, exist_ok=True)
log = []
def prueba(nombre, url, metodo="GET", **kw):
    try:
        r = requests.request(metodo, url, headers=dict(H, **kw.pop("headers", {})), timeout=25, **kw)
        t = r.text
        log.append(f"{nombre} | {r.status_code} | {len(t)} | {r.headers.get('content-type','')} | {re.sub(r'\s+',' ',t[:300])}")
        open(f"{OUT}/{nombre}.txt", "w", encoding="utf-8", errors="replace").write(t[:300000])
    except Exception as e:
        log.append(f"{nombre} | ERROR | {type(e).__name__}: {str(e)[:200]}")
B = "https://www.ice.com/marketdata/api/productguide/charting/data/"
for span in (1, 3):
    prueba(f"ice_hist_brent_{span}", B + f"historical?marketId=6018424&historicalSpan={span}")
prueba("ice_hist_ttf", B + "historical?marketId=6277391&historicalSpan=3")
prueba("ice_intraday_brent", B + "current-day?marketId=6018424")
sims = "|".join([f"@CL.{k}" for k in range(1, 14)] + [f"@NG.{k}" for k in range(1, 14)] +
                [f"@HG.{k}" for k in range(1, 14)] + [f"@LCO.{k}" for k in range(1, 14)])
prueba("cnbc_curvas", "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol",
       params={"symbols": sims, "requestMethod": "itv", "noform": "1", "partnerId": "2", "fund": "1", "exthrs": "1", "output": "json", "events": "1"})
for rng in ("5Y", "ALL"):
    prueba(f"cnbc_move_{rng}", f"https://ts-api.cnbc.com/harmony/app/charts/{rng}.json", params={"symbol": ".MOVE"})
prueba("cnbc_move_bars", "https://ts-api.cnbc.com/harmony/app/bars/.MOVE/1D/19880101000000/20261127000000/adjusted/EST5EDT.json")
open(f"{OUT}/_resumen.txt", "w", encoding="utf-8").write("\n".join(log))
print("\n".join(log))
