# -*- coding: utf-8 -*-
"""
DIAGNOSTICADOR DE MERCADOS
Genera docs/index.html con tres pestañas: Diagnosticador · Opciones · Calendario.
Se ejecuta cada día desde GitHub Actions. Necesita el secreto TIINGO_API_KEY.
"""

import os
import re
import io
import json
import math
import time
import html
import datetime as dt

import numpy as np
import pandas as pd
import requests

# ----------------------------------------------------------------------------
# CONFIGURACIÓN
# ----------------------------------------------------------------------------

CARPETA_WEB = "docs"
CARPETA_DATOS = "datos"
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}

AHORA = pd.Timestamp.now(tz="UTC").tz_convert(None)
HOY = AHORA.normalize()
# Datos completos hasta ayer; última semana cerrada = último viernes <= ayer
_REF = HOY - pd.Timedelta(days=1)
ULTIMO_VIERNES = _REF - pd.Timedelta(days=(_REF.weekday() - 4) % 7)

MOTORES = [
    ("ACWI", "RV global"), ("SPY", "RV EE. UU."), ("RSP", "RV EE. UU. equiponderada"),
    ("VGK", "RV Europa"), ("EEM", "RV emergentes"), ("QQQ", "Nasdaq"),
    ("IWM", "Small caps EE. UU."), ("VTV", "Value"), ("VUG", "Growth"),
    ("MTUM", "Momentum"), ("QUAL", "Calidad"), ("BTAL", "Anti-beta"),
    ("TLT", "Bonos largos"), ("IEF", "Bonos medios"), ("TIP", "Bonos ligados a inflación"),
    ("HYG", "High yield"), ("DBC", "Materias primas"), ("XLE", "Energía"),
    ("GLD", "Oro"), ("URA", "Uranio"), ("UUP", "Dólar"),
    ("DBMF", "Managed futures"), ("VIXY", "Volatilidad"),
]
REFERENCIA = "BIL"
OTROS_TIINGO = ["CPER"]

FRED_SERIES = {
    "T10YIE": "Inflación implícita 10 años",
    "CPIAUCSL": "IPC EE. UU.",
    "DFII10": "Tipo real 10 años (TIPS)",
    "ICSA": "Peticiones de subsidio",
    "SAHMREALTIME": "Regla de Sahm",
    "GACDFSA066MSFRBPHI": "Encuesta Fed Filadelfia",
    "GACDISA066MSFRBNY": "Encuesta Fed Nueva York",
    "BAMLH0A0HYM2": "Diferencial high yield (3 años)",
    "BAA10Y": "Diferencial Baa − bono 10 años",
    "T10Y3M": "Curva 10 años − 3 meses",
    "NFCI": "Condiciones financieras (Chicago Fed)",
    "STLFSI4": "Estrés financiero (St. Louis Fed)",
    "WALCL": "Balance de la Fed",
    "WTREGEN": "Cuenta del Tesoro",
    "RRPONTSYD": "Repo inverso",
    "DTWEXBGS": "Dólar (índice amplio)",
}

CBOE_INDICES = ["VIX", "VIX3M", "COR3M", "DSPX", "SKEW"]

# Calendarios oficiales (revisar cada año)
FOMC = [
    ("2026-12-08", "2026-12-09"),
    ("2027-01-26", "2027-01-27"), ("2027-03-16", "2027-03-17"),
    ("2027-04-27", "2027-04-28"), ("2027-06-08", "2027-06-09"),
    ("2027-07-27", "2027-07-28"), ("2027-09-14", "2027-09-15"),
    ("2027-10-26", "2027-10-27"), ("2027-11-16", "2027-11-17"),
]
IPC_EEUU = [
    "2026-12-10", "2027-01-13", "2027-02-11", "2027-03-10", "2027-04-13",
    "2027-05-12", "2027-06-10", "2027-07-14", "2027-08-11", "2027-09-14",
    "2027-10-14", "2027-11-10", "2027-12-10",
]

ERRORES = []   # (fuente, mensaje)
FECHAS = {}    # fuente -> última fecha disponible


# ----------------------------------------------------------------------------
# UTILIDADES
# ----------------------------------------------------------------------------

INICIO = time.time()
LIMITE_DESCARGAS = INICIO + 12 * 60   # nunca más de 12 minutos descargando


def get(url, params=None, intentos=2, espera=2, timeout=20):
    ultimo = ""
    for i in range(intentos):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=timeout)
            if r.status_code == 200:
                return r
            ultimo = f"HTTP {r.status_code}"
            if r.status_code in (401, 403, 404):
                break
        except Exception as e:  # noqa
            ultimo = str(e)
        time.sleep(espera * (i + 1))
    raise RuntimeError(ultimo)


def seguro(nombre, fn, *a, **k):
    if time.time() > LIMITE_DESCARGAS:
        ERRORES.append((nombre, "sin tiempo"))
        print(f"[{time.time() - INICIO:5.0f}s] {nombre}: SALTADO (sin tiempo)", flush=True)
        return None
    t0 = time.time()
    try:
        r = fn(*a, **k)
        print(f"[{time.time() - INICIO:5.0f}s] {nombre}: ok ({time.time() - t0:.0f}s)", flush=True)
        return r
    except Exception as e:  # noqa
        ERRORES.append((nombre, str(e)[:180]))
        print(f"[{time.time() - INICIO:5.0f}s] {nombre}: FALLO ({time.time() - t0:.0f}s) {str(e)[:120]}", flush=True)
        return None


def semanal(s):
    if s is None or len(s) == 0:
        return None
    w = s.resample("W-FRI").last().dropna()
    return w[w.index <= ULTIMO_VIERNES]


def mensual(s):
    if s is None or len(s) == 0:
        return None
    return s.resample("MS").last().dropna()


def pct(s, valor=None):
    s = s.dropna()
    if len(s) == 0:
        return np.nan
    v = s.iloc[-1] if valor is None else valor
    return float((s <= v).mean() * 100)


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def fmt(x, dec=2, suf=""):
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "—"
    return f"{x:,.{dec}f}{suf}".replace(",", "X").replace(".", ",").replace("X", ".")


def e(t):
    return html.escape(str(t))


def antiguedad_ok(fecha, frecuencia):
    if fecha is None:
        return False
    limite = {"d": 7, "s": 16, "m": 75}[frecuencia]
    return (HOY - pd.Timestamp(fecha)).days <= limite


def registrar(nombre, s):
    if s is not None and len(s):
        FECHAS[nombre] = s.index[-1]


# ----------------------------------------------------------------------------
# DESCARGAS
# ----------------------------------------------------------------------------

def fred(sid):
    r = get("https://fred.stlouisfed.org/graph/fredgraph.csv", params={"id": sid})
    df = pd.read_csv(io.StringIO(r.text))
    s = pd.Series(pd.to_numeric(df.iloc[:, 1], errors="coerce").values,
                  index=pd.to_datetime(df.iloc[:, 0], errors="coerce"))
    s = s[s.index.notna()].dropna().sort_index()
    if len(s) == 0:
        raise RuntimeError("serie vacía")
    return s.rename(sid)


def cboe_indice(nombre):
    url = f"https://cdn-api.cboe.com/api/global/us_indices/daily_prices/{nombre}_History.csv"
    df = pd.read_csv(io.StringIO(get(url).text))
    s = pd.Series(pd.to_numeric(df.iloc[:, -1], errors="coerce").values,
                  index=pd.to_datetime(df.iloc[:, 0], format="%m/%d/%Y", errors="coerce"))
    s = s[s.index.notna()].dropna().sort_index()
    if len(s) == 0:
        raise RuntimeError("serie vacía")
    return s.rename(nombre)


def tiingo(ticker):
    clave = os.environ.get("TIINGO_API_KEY", "").strip()
    if not clave:
        raise RuntimeError("falta la clave de Tiingo")
    r = get(f"https://api.tiingo.com/tiingo/daily/{ticker}/prices",
            params={"startDate": "1990-01-01", "token": clave})
    d = r.json()
    if not isinstance(d, list) or not d:
        raise RuntimeError(str(d)[:120])
    df = pd.DataFrame(d)
    idx = pd.to_datetime(df["date"], utc=True).dt.tz_convert(None).dt.normalize()
    s = pd.Series(df["adjClose"].astype(float).values, index=idx).sort_index()
    return s.rename(ticker)


def shiller():
    pagina = get("https://shillerdata.com/").text
    enlaces = re.findall(r'href="([^"]*ie_data\.xls[^"]*)"', pagina)
    if not enlaces:
        raise RuntimeError("no se encuentra el archivo")
    url = html.unescape(enlaces[0])
    if url.startswith("//"):
        url = "https:" + url
    contenido = get(url, timeout=120).content
    df = pd.read_excel(io.BytesIO(contenido), sheet_name="Data", header=None)
    fila = None
    for i in range(min(40, len(df))):
        celdas = [str(v).strip() for v in df.iloc[i].values]
        if "CAPE" in celdas:
            fila = i
            break
    if fila is None:
        raise RuntimeError("formato no reconocido")
    celdas = [str(v).strip() for v in df.iloc[fila].values]
    col_cape = celdas.index("CAPE")
    datos = df.iloc[fila + 1:].copy()
    fecha = pd.to_numeric(datos.iloc[:, 0], errors="coerce")
    datos = datos[fecha.notna()]
    fecha = fecha[fecha.notna()]
    anio = np.floor(fecha).astype(int)
    mes = np.round((fecha - anio) * 100).astype(int).clip(1, 12)
    idx = pd.to_datetime(dict(year=anio, month=mes, day=1))
    out = pd.DataFrame({
        "P": pd.to_numeric(datos.iloc[:, 1], errors="coerce").values,
        "CPI": pd.to_numeric(datos.iloc[:, 4], errors="coerce").values,
        "GS10": pd.to_numeric(datos.iloc[:, 6], errors="coerce").values,
        "CAPE": pd.to_numeric(datos.iloc[:, col_cape], errors="coerce").values,
    }, index=idx.values)
    out = out[~out.index.duplicated(keep="last")].sort_index()
    infl10 = (out["CPI"] / out["CPI"].shift(120)) ** 0.1 - 1
    out["ECY"] = 100 / out["CAPE"] - (out["GS10"] - 100 * infl10)
    return out


def fcig():
    url = "https://www.federalreserve.gov/econres/notes/feds-notes/fci_g_public_monthly_3yr.csv"
    df = pd.read_csv(io.StringIO(get(url).text))
    cols = [c for c in df.columns[1:] if "fci" in str(c).lower()]
    col = cols[0] if cols else df.columns[1]
    s = pd.Series(pd.to_numeric(df[col], errors="coerce").values,
                  index=pd.to_datetime(df.iloc[:, 0], errors="coerce"))
    s = s[s.index.notna()].dropna().sort_index()
    if len(s) == 0:
        raise RuntimeError("serie vacía")
    return s.rename("FCIG")


def cot(dataset, codigo, largo, corto):
    r = get(f"https://publicreporting.cftc.gov/resource/{dataset}.json",
            params={"cftc_contract_market_code": codigo,
                    "$order": "report_date_as_yyyy_mm_dd DESC", "$limit": 1500})
    df = pd.DataFrame(r.json())
    if df.empty:
        raise RuntimeError("sin datos")
    idx = pd.to_datetime(df["report_date_as_yyyy_mm_dd"])
    neto = (pd.to_numeric(df[largo]) - pd.to_numeric(df[corto])) / pd.to_numeric(df["open_interest_all"]) * 100
    return pd.Series(neto.values, index=idx).sort_index()


# ----------------------------------------------------------------------------
# HISTÓRICOS PROPIOS (put/call y gamma)
# ----------------------------------------------------------------------------

def leer_hist(nombre):
    ruta = os.path.join(CARPETA_DATOS, nombre)
    if os.path.exists(ruta):
        df = pd.read_csv(ruta, parse_dates=["fecha"]).set_index("fecha").sort_index()
        return df[~df.index.duplicated(keep="last")]
    return pd.DataFrame()


def guardar_hist(nombre, df):
    os.makedirs(CARPETA_DATOS, exist_ok=True)
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df.index.name = "fecha"
    df.to_csv(os.path.join(CARPETA_DATOS, nombre))


PC_CLAVES = {
    "total": "TOTAL PUT/CALL RATIO",
    "acciones": "EQUITY PUT/CALL RATIO",
    "indices": "INDEX PUT/CALL RATIO",
    "spx": "SPX",
}


def putcall_dia(fecha):
    url = f"https://cdn.cboe.com/data/us/options/market_statistics/daily/{fecha:%Y-%m-%d}_daily_options"
    r = requests.get(url, headers=UA, timeout=10)
    if r.status_code != 200:
        return None
    d = r.json()
    ratios = d.get("ratios", []) if isinstance(d, dict) else []
    fila = {}
    for it in ratios:
        nombre = str(it.get("name", "")).upper()
        try:
            valor = float(it.get("value"))
        except Exception:  # noqa
            continue
        if nombre == "TOTAL PUT/CALL RATIO":
            fila["total"] = valor
        elif nombre == "EQUITY PUT/CALL RATIO":
            fila["acciones"] = valor
        elif nombre == "INDEX PUT/CALL RATIO":
            fila["indices"] = valor
        elif nombre.startswith("SPX + SPXW") and "PUT/CALL" in nombre:
            fila["spx"] = valor
    return fila or None


def actualizar_putcall():
    hist = leer_hist("putcall.csv")
    dias = pd.bdate_range(HOY - pd.Timedelta(days=400), HOY - pd.Timedelta(days=1))
    pendientes = [d for d in dias if d not in hist.index]
    pendientes = pendientes[-300:]
    nuevas = {}
    fallos = 0
    limite = min(time.time() + 180, LIMITE_DESCARGAS)   # como mucho 3 minutos por ejecución
    for d in reversed(pendientes):
        if time.time() > limite:
            break
        try:
            fila = putcall_dia(d)
        except Exception:  # noqa
            fila = None
        if fila:
            nuevas[d] = fila
            fallos = 0
        else:
            fallos += 1
        if fallos >= 15:   # festivos o fin del archivo disponible
            break
        time.sleep(0.3)
    if nuevas:
        hist = pd.concat([hist, pd.DataFrame.from_dict(nuevas, orient="index")])
        guardar_hist("putcall.csv", hist)
    if hist.empty:
        raise RuntimeError("sin datos")
    return hist.sort_index()


def _gamma_bs(S, K, T, iv):
    sq = np.sqrt(T)
    d1 = (np.log(S / K) + 0.5 * iv ** 2 * T) / (iv * sq)
    return np.exp(-0.5 * d1 ** 2) / np.sqrt(2 * np.pi) / (S * iv * sq)


def gamma_spx():
    d = get("https://cdn-api.cboe.com/api/global/delayed_quotes/options/_SPX.json", timeout=120).json()
    datos = d["data"]
    op = pd.DataFrame(datos["options"])
    m = op["option"].str.extract(r"^([A-Z]+)(\d{6})([CP])(\d{8})$")
    op["vto"] = pd.to_datetime(m[1], format="%y%m%d", errors="coerce") + pd.Timedelta(hours=21)
    op["tipo"] = m[2]
    op["K"] = pd.to_numeric(m[3], errors="coerce") / 1000
    for c in ["iv", "open_interest", "bid", "ask"]:
        op[c] = pd.to_numeric(op[c], errors="coerce")
    op = op.dropna(subset=["vto", "tipo", "K"])
    op["T"] = (op["vto"] - AHORA).dt.total_seconds() / (365 * 86400)
    op = op[op["T"] > 0]

    spot = None
    for clave in ("current_price", "close", "last_trade_price", "price"):
        v = datos.get(clave) if isinstance(datos, dict) else None
        try:
            if v and float(v) > 0:
                spot = float(v)
                break
        except Exception:  # noqa
            pass
    if spot is None:   # paridad put-call en el vencimiento más cercano
        cerc = op[op["vto"] == op["vto"].min()].copy()
        cerc["mid"] = (cerc["bid"] + cerc["ask"]) / 2
        tabla = cerc.pivot_table(index="K", columns="tipo", values="mid").dropna()
        tabla = tabla[(tabla["C"] > 0) & (tabla["P"] > 0)]
        k = (tabla["C"] - tabla["P"]).abs().idxmin()
        spot = float(k + tabla.loc[k, "C"] - tabla.loc[k, "P"])

    op = op[(op["open_interest"] > 0) & (op["iv"] > 0.01) & (op["T"] < 1.5)]
    signo = np.where(op["tipo"].values == "C", 1.0, -1.0)
    K = op["K"].values
    T = op["T"].values
    iv = op["iv"].values
    oi = op["open_interest"].values

    def gex(S):
        g = _gamma_bs(S, K, T, iv)
        return float(np.nansum(signo * g * oi * 100 * S * S * 0.01))

    actual = gex(spot)
    rejilla = np.linspace(spot * 0.85, spot * 1.15, 121)
    perfil = np.array([gex(s) for s in rejilla])
    giro = None
    cambios = np.where(np.diff(np.sign(perfil)) != 0)[0]
    if len(cambios):
        mejores = sorted(cambios, key=lambda i: abs(rejilla[i] - spot))
        i = mejores[0]
        x0, x1, y0, y1 = rejilla[i], rejilla[i + 1], perfil[i], perfil[i + 1]
        giro = float(x0 - y0 * (x1 - x0) / (y1 - y0))

    cerca = op[op["T"] < 70 / 365]
    calls = cerca[cerca["tipo"] == "C"].groupby("K")["open_interest"].sum()
    puts = cerca[cerca["tipo"] == "P"].groupby("K")["open_interest"].sum()
    muro_call = float(calls[calls.index >= spot].idxmax()) if len(calls[calls.index >= spot]) else None
    muro_put = float(puts[puts.index <= spot].idxmax()) if len(puts[puts.index <= spot]) else None

    res = {"spot": spot, "gex_mm": actual / 1e9, "giro": giro,
           "muro_call": muro_call, "muro_put": muro_put,
           "fecha": str(d.get("timestamp", AHORA))}
    hist = leer_hist("gamma.csv")
    fila = pd.DataFrame({"gex_mm": [res["gex_mm"]],
                         "dist_giro_pct": [((spot / giro - 1) * 100) if giro else np.nan]},
                        index=[HOY])
    hist = pd.concat([hist, fila])
    guardar_hist("gamma.csv", hist)
    res["hist"] = hist.sort_index()
    return res


# ----------------------------------------------------------------------------
# CÁLCULOS DEL DIAGNOSTICADOR
# ----------------------------------------------------------------------------

def rrg(precio_w, ref_w):
    df = pd.concat([precio_w, ref_w], axis=1, join="inner").dropna()
    rs = df.iloc[:, 0] / df.iloc[:, 1]
    X = 100 * rs / rs.rolling(26).mean()
    Y = 100 * X / X.shift(4)
    return rs, X.dropna(), Y.dropna()


def fase_motor(precio_w, rs, X, Y):
    x, y = X.iloc[-1], Y.iloc[-1]
    dy = Y.diff().iloc[-3:]
    sube3 = bool((dy > 0).all())
    baja3 = bool((dy < 0).all())
    r = rsi(precio_w).iloc[-1]
    dist = (precio_w / precio_w.rolling(40).mean() - 1).dropna()
    pd_ = pct(dist)
    prs = pct(rs)
    alc = int(r > 70) + int(pd_ >= 90) + int(prs > 90)
    baj = int(r < 30) + int(pd_ <= 10) + int(prs < 10)
    if x >= 100 and y < 100:
        f = "GIRO"
    elif x >= 100:
        f = "MADUREZ" if (baja3 or alc >= 2) else "ACELERACIÓN"
    elif y >= 100:
        f = "ARRANQUE"
    else:
        f = "SUELO" if (sube3 or baj >= 2) else "REZAGADO"
    return f, {"rsi": r, "alc": alc, "baj": baj}


ACCION = {
    "SUELO": "Vigilar", "ARRANQUE": "Construir poco a poco", "ACELERACIÓN": "Mantener",
    "MADUREZ": "Empezar a salir", "GIRO": "Salir", "REZAGADO": "No tocar",
}
COLOR_FASE = {
    "SUELO": "#6b7a90", "ARRANQUE": "#2f6fdb", "ACELERACIÓN": "#1f9d55",
    "MADUREZ": "#d9a400", "GIRO": "#e0661b", "REZAGADO": "#c0392b",
}


def fase_macro(nivel_pct, impulso, frenando3):
    if nivel_pct >= 50 and impulso > 0:
        return "MADUREZ" if (nivel_pct > 80 and frenando3) else "ACELERACIÓN"
    if nivel_pct >= 50:
        return "GIRO"
    if impulso > 0:
        return "ARRANQUE"
    return "SUELO" if (nivel_pct < 20 and frenando3) else "REZAGADO"


def variable_macro(nombre, s, frecuencia, periodos, modo="dif"):
    """s: serie ya en su frecuencia (semanal o mensual)."""
    s = s.dropna()
    if len(s) < periodos + 10:
        return None
    imp = (s / s.shift(periodos) - 1) * 100 if modo == "pct" else s - s.shift(periodos)
    imp = imp.dropna()
    frenando3 = bool((imp.abs().diff().iloc[-3:] < 0).all())
    niv = pct(s)
    ultimo = imp.iloc[-1]
    fase = fase_macro(niv, ultimo, frenando3)
    rastro_x = [pct(s, v) for v in s.iloc[-8:]]
    rastro_y = [pct(imp, v) for v in imp.iloc[-8:]]
    return {"nombre": nombre, "valor": s.iloc[-1], "fecha": s.index[-1], "nivel": niv,
            "impulso": ultimo, "fase": fase, "frecuencia": frecuencia,
            "rx": rastro_x, "ry": rastro_y, "modo": modo, "periodos": periodos}


def puerta(acwi_w, baa_w, ratio_vix_w, ecy):
    res = {"condiciones": [], "puntos": 0, "tramo": 0, "veto": False, "techo": None,
           "techo_txt": "sin dato", "exposicion": None}
    # Techo
    if ecy is not None and not math.isnan(ecy):
        if ecy < 1.5:
            res["techo"], res["techo_txt"] = 0.0, "NADA"
        elif ecy < 3:
            res["techo"], res["techo_txt"] = 1 / 3, "1/3"
        elif ecy < 4.5:
            res["techo"], res["techo_txt"] = 2 / 3, "2/3"
        else:
            res["techo"], res["techo_txt"] = 1.0, "TODO"
    V = 13

    # 1. Precio cerca de la media de 200 semanas
    c1 = None
    if acwi_w is not None and len(acwi_w) > 210:
        ma = acwi_w.rolling(200).mean()
        cond = (acwi_w <= 1.05 * ma)
        c1 = bool(cond.iloc[-V:].any())
        dist = (acwi_w.iloc[-1] / ma.iloc[-1] - 1) * 100
        res["dist_ma200"] = dist
    res["condiciones"].append(("Precio cerca de la media de 200 semanas", 2, c1,
                               f"distancia actual {fmt(res.get('dist_ma200'), 1, ' %')}"))

    # 2. Crédito girando a la baja
    c2 = None
    if baa_w is not None and len(baa_w) > 120:
        b = baa_w.values
        sma4 = baa_w.rolling(4).mean().values
        sma13 = baa_w.rolling(13).mean().values
        n = len(b)
        hits = []
        for i in range(n - V, n):
            ini = max(0, i - 25)
            j = ini + int(np.argmax(b[ini:i + 1]))
            M = b[j]
            previo = b[max(0, j - 52):j]
            if len(previo) == 0:
                hits.append(False)
                continue
            m = previo.min()
            hits.append(bool(M >= 1.4 * m and b[i] <= 0.85 * M and sma4[i] < sma13[i]))
        c2 = any(hits)
    res["condiciones"].append(("Crédito girando a la baja desde su pico", 2, c2, ""))

    # 3. VIX vuelve a contango
    c3 = None
    if ratio_vix_w is not None and len(ratio_vix_w) > 40:
        r = ratio_vix_w.values
        n = len(r)
        hits = []
        for k in range(n - V, n):
            if k < 15:
                continue
            ok = r[k - 1] < 0.95 and r[k] < 0.95 and (r[max(0, k - 14):k - 1] > 1).any()
            hits.append(bool(ok))
        c3 = any(hits)
    res["condiciones"].append(("VIX vuelve a contango tras inversión", 1, c3,
                               f"VIX/VIX3M actual {fmt(ratio_vix_w.iloc[-1] if ratio_vix_w is not None else None, 2)}"))

    # 4. RSI semanal en sobreventa
    c4 = None
    if acwi_w is not None and len(acwi_w) > 30:
        rr = rsi(acwi_w)
        c4 = bool((rr.iloc[-V:] < 30).any())
        res["rsi_acwi"] = rr.iloc[-1]
    res["condiciones"].append(("RSI semanal en sobreventa", 1, c4,
                               f"RSI actual {fmt(res.get('rsi_acwi'), 0)}"))

    res["puntos"] = sum(p for _, p, c, _ in res["condiciones"] if c)
    res["tramo"] = 3 if res["puntos"] >= 6 else 2 if res["puntos"] >= 4 else 1 if res["puntos"] >= 2 else 0

    # Veto
    if baa_w is not None and len(baa_w) > 30:
        b = baa_w
        cond = (b / b.shift(13) - 1 >= 0.30) & ((b - b.shift(4)) > (b.shift(4) - b.shift(8)))
        res["veto"] = bool(cond.iloc[-4:].any())
        res["baa_13s"] = (b.iloc[-1] / b.iloc[-14] - 1) * 100
    if res["veto"]:
        res["tramo"] = min(res["tramo"], 1)
    if res["techo"] is not None:
        res["exposicion"] = res["techo"] * res["tramo"] / 3
    return res


# ----------------------------------------------------------------------------
# CALENDARIO
# ----------------------------------------------------------------------------

def tercer_viernes(anio, mes):
    d = dt.date(anio, mes, 1)
    primero = d + dt.timedelta(days=(4 - d.weekday()) % 7)
    return primero + dt.timedelta(days=14)


def ultimo_viernes(anio, mes):
    d = dt.date(anio + (mes // 12), mes % 12 + 1, 1) - dt.timedelta(days=1)
    return d - dt.timedelta(days=(d.weekday() - 4) % 7)


def segundo_viernes(anio, mes):
    d = dt.date(anio, mes, 1)
    return d + dt.timedelta(days=(4 - d.weekday()) % 7 + 7)


def ultimo_habil(anio, mes):
    d = dt.date(anio + (mes // 12), mes % 12 + 1, 1) - dt.timedelta(days=1)
    while d.weekday() > 4:
        d -= dt.timedelta(days=1)
    return d


def calendario(dias=120):
    hoy = HOY.date()
    fin = hoy + dt.timedelta(days=dias)
    ev = []
    for anio in range(hoy.year, fin.year + 1):
        for mes in range(1, 13):
            tv = tercer_viernes(anio, mes)
            if mes in (3, 6, 9, 12):
                ev.append((tv, "Vencimiento trimestral de opciones y futuros + rebalanceo del S&P 500", "Opciones"))
            else:
                ev.append((tv, "Vencimiento mensual de opciones", "Opciones"))
            sig = (anio + (mes // 12), mes % 12 + 1)
            vix = tercer_viernes(*sig) - dt.timedelta(days=30)
            ev.append((vix, "Vencimiento del VIX", "Opciones"))
            fm = ultimo_habil(anio, mes)
            if mes == 12:
                ev.append((fm, "Cierre de año (cierre de libros)", "Cierres"))
            elif mes in (3, 6, 9):
                ev.append((fm, "Cierre de trimestre (cierre de libros, rebalanceo de fondos de pensiones)", "Cierres"))
            # Blackout estimado de recompras
            if mes in (3, 6, 9, 12):
                fin_t = dt.date(anio + (mes // 12), mes % 12 + 1, 1) - dt.timedelta(days=1)
                ini_b = fin_t - dt.timedelta(days=20)
                fin_b = fin_t + dt.timedelta(days=24)
                ev.append((ini_b, "Empieza el blackout de recompras (estimado)", "Recompras"))
                ev.append((fin_b, "Termina el blackout de recompras (estimado)", "Recompras"))
        ev.append((ultimo_viernes(anio, 6), "Reconstitución Russell (junio), efectiva al cierre", "Índices"))
        ev.append((segundo_viernes(anio, 12), "Reconstitución Russell (diciembre), efectiva al cierre", "Índices"))
        ev.append((ultimo_habil(anio, 4), "Russell: día de referencia (junio)", "Índices"))
        ev.append((ultimo_habil(anio, 10), "Russell: día de referencia (diciembre)", "Índices"))
        ev.append((dt.date(anio, 3, 31), "Fin del año fiscal en Japón", "Fiscal"))
        ev.append((dt.date(anio, 4, 15), "Día de impuestos en EE. UU.", "Fiscal"))
        ev.append((dt.date(anio, 10, 31), "Fin del ejercicio fiscal de muchos fondos estadounidenses", "Fiscal"))
        ev.append((dt.date(anio, 12, 1), "Empieza la ventana de ventas por pérdidas fiscales", "Fiscal"))
    for a, b in FOMC:
        ev.append((dt.date.fromisoformat(b), f"Reunión de la Fed (FOMC) {a[8:]}-{b[8:]}", "Fed"))
    for f in IPC_EEUU:
        ev.append((dt.date.fromisoformat(f), "IPC EE. UU.", "Datos"))
    ev = sorted({(d, t, c) for d, t, c in ev if hoy <= d <= fin})
    # ¿Estamos en blackout estimado?
    en_blackout = False
    for anio in (hoy.year - 1, hoy.year):
        for mes in (3, 6, 9, 12):
            fin_t = dt.date(anio + (mes // 12), mes % 12 + 1, 1) - dt.timedelta(days=1)
            if fin_t - dt.timedelta(days=20) <= hoy <= fin_t + dt.timedelta(days=24):
                en_blackout = True
    return ev, en_blackout


def estacionalidad(sh, spy_diario):
    out = {}
    if sh is not None:
        p = sh["P"].dropna()
        ret = p.pct_change().dropna() * 100
        tabla = pd.DataFrame({"r": ret.values, "mes": ret.index.month})
        g = tabla.groupby("mes")["r"]
        out["meses"] = pd.DataFrame({"media": g.mean(), "mediana": g.median(),
                                     "positivos": g.apply(lambda x: (x > 0).mean() * 100)})
        anual = p.resample("YS").last().pct_change().dropna() * 100
        anual = anual[anual.index.year < HOY.year]
        ciclo = pd.DataFrame({"r": anual.values, "c": anual.index.year % 4})
        nombres = {0: "Año electoral", 1: "Año 1 tras elecciones", 2: "Año de medio mandato", 3: "Año preelectoral"}
        gc = ciclo.groupby("c")["r"]
        out["ciclo"] = [(nombres[k], gc.mean()[k], gc.median()[k], (ciclo[ciclo.c == k].r > 0).mean() * 100)
                        for k in (1, 2, 3, 0)]
        out["anio_actual"] = nombres[HOY.year % 4]
        out["desde"] = p.index[0].year
    if spy_diario is not None and len(spy_diario) > 500:
        r = spy_diario.pct_change().dropna() * 100
        df = pd.DataFrame({"r": r})
        df["ym"] = df.index.to_period("M")
        df["n"] = df.groupby("ym").cumcount()
        df["m"] = df.groupby("ym")["r"].transform("count") - df["n"]
        cambio = (df["m"] <= 1) | (df["n"] <= 2)
        out["cambio_mes"] = (df[cambio]["r"].mean(), df[~cambio]["r"].mean(), spy_diario.index[0].year)
    return out


# ----------------------------------------------------------------------------
# GRÁFICOS SVG
# ----------------------------------------------------------------------------

def svg_rrg(puntos, titulo_x, titulo_y, centro=100.0, rango=None, ancho=680, alto=520,
            cuadrantes=("LIDERANDO", "DEBILITÁNDOSE", "REZAGADO", "MEJORANDO")):
    """puntos: lista de dict(et, xs, ys, color)."""
    xs = [v for p in puntos for v in p["xs"]]
    ys = [v for p in puntos for v in p["ys"]]
    if not xs:
        return "<p class='nota'>Sin datos suficientes.</p>"
    if rango is None:
        dx = max(abs(max(xs) - centro), abs(min(xs) - centro), 1) * 1.15
        dy = max(abs(max(ys) - centro), abs(min(ys) - centro), 1) * 1.15
        x0, x1, y0, y1 = centro - dx, centro + dx, centro - dy, centro + dy
    else:
        x0, x1, y0, y1 = rango
    m = 40

    def X(v):
        return m + (v - x0) / (x1 - x0) * (ancho - 2 * m)

    def Y(v):
        return alto - m - (v - y0) / (y1 - y0) * (alto - 2 * m)

    cx, cy = X(centro), Y(centro)
    s = [f'<svg viewBox="0 0 {ancho} {alto}" class="grafico" role="img">']
    s.append(f'<rect x="{cx}" y="{m}" width="{ancho - m - cx}" height="{cy - m}" fill="#e8f6ee"/>')
    s.append(f'<rect x="{cx}" y="{cy}" width="{ancho - m - cx}" height="{alto - m - cy}" fill="#fdf5dc"/>')
    s.append(f'<rect x="{m}" y="{cy}" width="{cx - m}" height="{alto - m - cy}" fill="#fbe9e7"/>')
    s.append(f'<rect x="{m}" y="{m}" width="{cx - m}" height="{cy - m}" fill="#e7effc"/>')
    s.append(f'<line x1="{cx}" y1="{m}" x2="{cx}" y2="{alto - m}" stroke="#999" stroke-width="1"/>')
    s.append(f'<line x1="{m}" y1="{cy}" x2="{ancho - m}" y2="{cy}" stroke="#999" stroke-width="1"/>')
    et = [(cuadrantes[0], ancho - m - 6, m + 16, "end", "#1f7a45"),
          (cuadrantes[1], ancho - m - 6, alto - m - 8, "end", "#9a7400"),
          (cuadrantes[2], m + 6, alto - m - 8, "start", "#a33"),
          (cuadrantes[3], m + 6, m + 16, "start", "#2457b0")]
    for t, x, y, a, c in et:
        s.append(f'<text x="{x}" y="{y}" text-anchor="{a}" class="cuad" fill="{c}">{t}</text>')
    s.append(f'<text x="{ancho / 2}" y="{alto - 8}" text-anchor="middle" class="eje">{e(titulo_x)} →</text>')
    s.append(f'<text x="14" y="{alto / 2}" text-anchor="middle" class="eje" transform="rotate(-90 14 {alto / 2})">{e(titulo_y)} →</text>')
    for p in puntos:
        s.append(f'<g class="serie" data-t="{e(p["et"])}">')
        pts = " ".join(f"{X(a):.1f},{Y(b):.1f}" for a, b in zip(p["xs"], p["ys"]))
        s.append(f'<polyline points="{pts}" fill="none" stroke="{p["color"]}" stroke-width="1.6" stroke-opacity="0.55"/>')
        for a, b in zip(p["xs"][:-1], p["ys"][:-1]):
            s.append(f'<circle cx="{X(a):.1f}" cy="{Y(b):.1f}" r="1.8" fill="{p["color"]}" fill-opacity="0.5"/>')
        a, b = p["xs"][-1], p["ys"][-1]
        s.append(f'<circle cx="{X(a):.1f}" cy="{Y(b):.1f}" r="4.5" fill="{p["color"]}"/>')
        if X(a) > ancho - 110:
            s.append(f'<text x="{X(a) - 6:.1f}" y="{Y(b) - 5:.1f}" text-anchor="end" class="etq">{e(p["et"])}</text>')
        else:
            s.append(f'<text x="{X(a) + 6:.1f}" y="{Y(b) - 5:.1f}" class="etq">{e(p["et"])}</text>')
        s.append("</g>")
    s.append("</svg>")
    return "".join(s)


def svg_linea(serie, alto=120, ancho=680, color="#2f6fdb", ref=None):
    s = serie.dropna()
    if len(s) < 2:
        return ""
    v = s.values
    lo, hi = float(np.min(v)), float(np.max(v))
    if ref is not None:
        lo, hi = min(lo, ref), max(hi, ref)
    if hi == lo:
        hi = lo + 1
    m = 6
    pts = " ".join(f"{m + i / (len(v) - 1) * (ancho - 2 * m):.1f},{alto - m - (x - lo) / (hi - lo) * (alto - 2 * m):.1f}"
                   for i, x in enumerate(v))
    out = [f'<svg viewBox="0 0 {ancho} {alto}" class="mini">']
    if ref is not None:
        yr = alto - m - (ref - lo) / (hi - lo) * (alto - 2 * m)
        out.append(f'<line x1="0" x2="{ancho}" y1="{yr:.1f}" y2="{yr:.1f}" stroke="#bbb" stroke-dasharray="4 4"/>')
    out.append(f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8"/></svg>')
    return "".join(out)


# ----------------------------------------------------------------------------
# PROGRAMA PRINCIPAL
# ----------------------------------------------------------------------------

def main():
    os.makedirs(CARPETA_WEB, exist_ok=True)
    open(os.path.join(CARPETA_WEB, ".nojekyll"), "w").close()

    # --- Descargas
    precios = {}
    for t in [m for m, _ in MOTORES] + [REFERENCIA] + OTROS_TIINGO:
        s = seguro(f"Precio {t}", tiingo, t)
        if s is not None:
            precios[t] = s
            registrar(f"Precio {t}", s)
        time.sleep(0.5)
    fr = {}
    for sid in FRED_SERIES:
        s = seguro(FRED_SERIES[sid], fred, sid)
        if s is not None:
            fr[sid] = s
            registrar(FRED_SERIES[sid], s)
    cb = {}
    for n in CBOE_INDICES:
        s = seguro(f"Cboe {n}", cboe_indice, n)
        if s is not None:
            cb[n] = s
            registrar(f"Cboe {n}", s)
    sh = seguro("CAPE / ECY (Shiller)", shiller)
    if sh is not None:
        registrar("CAPE / ECY (Shiller)", sh["CAPE"].dropna())
    fci = seguro("FCI-G (Reserva Federal)", fcig)
    registrar("FCI-G (Reserva Federal)", fci)
    pc = seguro("Ratio put/call (Cboe)", actualizar_putcall)
    if pc is not None:
        registrar("Ratio put/call (Cboe)", pc)
    gm = seguro("Gamma SPX (Cboe)", gamma_spx)
    cot_series = {}
    for etq, ds, cod, l, c in [
        ("S&P 500 · gestoras", "gpe5-46if", "13874A", "asset_mgr_positions_long", "asset_mgr_positions_short"),
        ("S&P 500 · fondos apalancados", "gpe5-46if", "13874A", "lev_money_positions_long", "lev_money_positions_short"),
        ("Nasdaq 100 · gestoras", "gpe5-46if", "209742", "asset_mgr_positions_long", "asset_mgr_positions_short"),
        ("Nasdaq 100 · fondos apalancados", "gpe5-46if", "209742", "lev_money_positions_long", "lev_money_positions_short"),
        ("VIX · fondos apalancados", "gpe5-46if", "1170E1", "lev_money_positions_long", "lev_money_positions_short"),
        ("VIX · gestoras", "gpe5-46if", "1170E1", "asset_mgr_positions_long", "asset_mgr_positions_short"),
        ("Bono 10 años · fondos apalancados", "gpe5-46if", "043602", "lev_money_positions_long", "lev_money_positions_short"),
        ("Bono 10 años · gestoras", "gpe5-46if", "043602", "asset_mgr_positions_long", "asset_mgr_positions_short"),
        ("Oro · especuladores", "72hh-3qpy", "088691", "m_money_positions_long_all", "m_money_positions_short_all"),
        ("Petróleo WTI · especuladores", "72hh-3qpy", "067651", "m_money_positions_long_all", "m_money_positions_short_all"),
        ("Cobre · especuladores", "72hh-3qpy", "085692", "m_money_positions_long_all", "m_money_positions_short_all"),
    ]:
        s = seguro(f"Posicionamiento {etq}", cot, ds, cod, l, c)
        if s is not None:
            cot_series[etq] = s
    if cot_series:
        registrar("Posicionamiento (CFTC)", max(cot_series.values(), key=lambda x: x.index[-1]))

    # --- Series semanales
    W = {t: semanal(s) for t, s in precios.items()}
    ref_w = W.get(REFERENCIA)

    # --- Mapa de rotación de motores
    motores = []
    if ref_w is not None:
        for t, nombre in MOTORES:
            if t not in W or W[t] is None:
                continue
            rs, X, Y = rrg(W[t], ref_w)
            if len(Y) < 12:
                continue
            XY = pd.concat([X, Y], axis=1).dropna()
            X, Y = XY.iloc[:, 0], XY.iloc[:, 1]
            f, extra = fase_motor(W[t].loc[:rs.index[-1]], rs, X, Y)
            motores.append({"t": t, "nombre": nombre, "X": X.iloc[-1], "Y": Y.iloc[-1],
                            "xs": list(X.iloc[-8:].values), "ys": list(Y.iloc[-8:].values),
                            "fase": f, "rsi": extra["rsi"], "alc": extra["alc"], "baj": extra["baj"],
                            "fecha": X.index[-1]})

    # --- Variables
    baa_w = semanal(fr.get("BAA10Y"))
    vix_w = semanal(cb.get("VIX"))
    vix3_w = semanal(cb.get("VIX3M"))
    ratio_vix_w = (vix_w / vix3_w).dropna() if (vix_w is not None and vix3_w is not None) else None
    acwi_w = W.get("ACWI")

    ecy = cape = fecha_ecy = None
    if sh is not None:
        ss = sh.dropna(subset=["ECY"])
        if len(ss):
            ecy = float(ss["ECY"].iloc[-1])
            cape = float(ss["CAPE"].iloc[-1])
            fecha_ecy = ss.index[-1]
    P = puerta(acwi_w, baa_w, ratio_vix_w, ecy)

    # Liquidez neta de la Fed (millones de $)
    liq_w = None
    if all(k in fr for k in ("WALCL", "WTREGEN", "RRPONTSYD")):
        a = semanal(fr["WALCL"])
        b = semanal(fr["WTREGEN"])
        c = semanal(fr["RRPONTSYD"]) * 1000
        liq_w = pd.concat([a, b, c], axis=1).ffill().dropna()
        liq_w = (liq_w.iloc[:, 0] - liq_w.iloc[:, 1] - liq_w.iloc[:, 2]) / 1e6  # billones de $

    macro = []

    def add(v):
        if v is not None:
            macro.append(v)

    if "CPIAUCSL" in fr:
        cpi_m = mensual(fr["CPIAUCSL"])
        add(variable_macro("Inflación (IPC interanual, %)", (cpi_m / cpi_m.shift(12) - 1).dropna() * 100, "m", 6))
    enc = [mensual(fr[k]) for k in ("GACDFSA066MSFRBPHI", "GACDISA066MSFRBNY") if k in fr]
    if enc:
        crec = pd.concat(enc, axis=1).dropna().mean(axis=1).rolling(3).mean().dropna()
        add(variable_macro("Crecimiento (encuestas Fed, media 3 meses)", crec, "m", 6))
    if baa_w is not None:
        add(variable_macro("Estrés de crédito (Baa − bono 10 años)", baa_w, "s", 13))
    if liq_w is not None:
        add(variable_macro("Liquidez neta de la Fed (billones $)", liq_w, "s", 13, "pct"))
    for sid, per in (("NFCI", 13), ("STLFSI4", 13), ("T10Y3M", 13), ("DFII10", 13), ("T10YIE", 13)):
        if sid in fr:
            add(variable_macro(FRED_SERIES[sid], semanal(fr[sid]), "s", per))
    if "DTWEXBGS" in fr:
        add(variable_macro("Dólar (índice amplio)", semanal(fr["DTWEXBGS"]), "s", 13, "pct"))
    if "ICSA" in fr:
        add(variable_macro("Peticiones de subsidio (media 4 semanas)", semanal(fr["ICSA"]).rolling(4).mean().dropna(), "s", 13, "pct"))
    if "SAHMREALTIME" in fr:
        add(variable_macro("Regla de Sahm", mensual(fr["SAHMREALTIME"]), "m", 6))
    if fci is not None:
        add(variable_macro("Condiciones financieras FCI-G (Fed)", mensual(fci), "m", 6))
    if "CPER" in W and "GLD" in W and W["CPER"] is not None and W["GLD"] is not None:
        cg = (W["CPER"] / W["GLD"]).dropna()
        add(variable_macro("Cobre / oro", cg, "s", 13, "pct"))
    if ratio_vix_w is not None:
        add(variable_macro("Estructura del VIX (VIX/VIX3M)", ratio_vix_w, "s", 4))
    for n, nom in (("DSPX", "Dispersión (DSPX)"), ("COR3M", "Correlación implícita (COR3M)")):
        if n in cb:
            add(variable_macro(nom, semanal(cb[n]), "s", 13))
    if "BAMLH0A0HYM2" in fr:
        add(variable_macro("Diferencial high yield (solo 3 años)", semanal(fr["BAMLH0A0HYM2"]), "s", 13))
    if "RSP" in W and "SPY" in W and W["RSP"] is not None and W["SPY"] is not None:
        add(variable_macro("Equiponderado / capitalización (RSP/SPY)", (W["RSP"] / W["SPY"]).dropna(), "s", 13, "pct"))
    mac = {v["nombre"]: v for v in macro}

    # --- Sentimiento (réplica Euphoriameter)
    sent = None
    comp = []
    if sh is not None:
        comp.append(pct(sh["CAPE"].dropna()))
    if vix_w is not None:
        comp.append(100 - pct(vix_w))
    if acwi_w is not None and len(acwi_w) > 210:
        dist = (acwi_w / acwi_w.rolling(200).mean() - 1).dropna()
        comp.append(pct(dist))
    if comp:
        sent = float(np.mean(comp))

    # --- Convicción
    votos = {}
    crec_v = mac.get("Crecimiento (encuestas Fed, media 3 meses)")
    sub_v = mac.get("Peticiones de subsidio (media 4 semanas)")
    sahm = fr["SAHMREALTIME"].iloc[-1] if "SAHMREALTIME" in fr else None
    if crec_v and sub_v:
        mejora_c = crec_v["impulso"] > 0
        mejora_s = sub_v["impulso"] < 0
        if (sahm is not None and sahm >= 0.5) or (not mejora_c and not mejora_s):
            votos["A · Régimen"] = -1
        elif mejora_c and mejora_s:
            votos["A · Régimen"] = 1
        else:
            votos["A · Régimen"] = 0
    if baa_w is not None and ratio_vix_w is not None and len(baa_w) > 14:
        sube = baa_w.iloc[-1] > baa_w.iloc[-14]
        rv = ratio_vix_w.iloc[-1]
        votos["B · Estrés"] = 1 if (not sube and rv < 1) else -1 if (sube and rv > 1) else 0
    if liq_w is not None and "NFCI" in fr:
        nf = semanal(fr["NFCI"])
        l_sube = liq_w.iloc[-1] > liq_w.iloc[-14]
        n_baja = nf.iloc[-1] < nf.iloc[-14]
        votos["C · Liquidez"] = 1 if (l_sube and n_baja) else -1 if (not l_sube and not n_baja) else 0
    if sent is not None:
        votos["E+F+G · Valoración, técnico y sentimiento"] = -1 if sent > 80 else 1 if sent < 20 else 0
    fase_acwi = next((m["fase"] for m in motores if m["t"] == "ACWI"), None)
    direccion = None
    if fase_acwi:
        direccion = 1 if fase_acwi in ("SUELO", "ARRANQUE", "ACELERACIÓN") else -1
    coinciden = sum(1 for v in votos.values() if direccion is not None and v == direccion)
    conv = "BAJA" if coinciden <= 1 else "MEDIA" if coinciden <= 3 else "ALTA"
    ritmo = {"BAJA": "No construir", "MEDIA": "Completar el tramo en 2–3 meses", "ALTA": "Completar el tramo en 1 mes"}[conv]
    d_txt = None
    if "DSPX" in cb and "COR3M" in cb:
        p_d, p_c = pct(cb["DSPX"]), pct(cb["COR3M"])
        d_txt = ("Favorable a selección de valores (alfa)" if (p_d > 60 and p_c < 40)
                 else "Favorable a índice (beta)" if (p_d < 40 and p_c > 60) else "Neutral")

    # --- Calendario y estacionalidad
    eventos, en_blackout = calendario()
    est = estacionalidad(sh, precios.get("SPY"))

    # ------------------------------------------------------------------------
    # HTML
    # ------------------------------------------------------------------------
    H = []
    H.append(CABECERA.replace("{{FECHA}}", f"{AHORA + pd.Timedelta(hours=1):%d-%m-%Y %H:%M}"))

    # ===== PESTAÑA 1: DIAGNOSTICADOR
    H.append('<section id="t1" class="pest activa">')

    # Puerta
    exp_txt = "sin dato" if P["exposicion"] is None else f"{P['exposicion'] * 100:.0f} %"
    H.append('<div class="tarjetas">')
    H.append(f'<div class="tarjeta"><div class="t">Techo de indexación</div><div class="v">{e(P["techo_txt"])}</div>'
             f'<div class="s">ECY {fmt(ecy, 2, " %")} · CAPE {fmt(cape, 1)}'
             f'{" · " + format(fecha_ecy, "%m-%Y") if fecha_ecy is not None else ""}</div></div>')
    H.append(f'<div class="tarjeta"><div class="t">Puntos de la puerta</div><div class="v">{P["puntos"]} / 6</div>'
             f'<div class="s">Tramo indicado: {P["tramo"]} de 3</div></div>')
    H.append(f'<div class="tarjeta"><div class="t">Indexación indicada</div><div class="v">{exp_txt}</div>'
             f'<div class="s">= techo × tramo</div></div>')
    vt = "ACTIVO" if P["veto"] else "no"
    H.append(f'<div class="tarjeta {"alerta" if P["veto"] else ""}"><div class="t">Veto por crédito</div><div class="v">{vt}</div>'
             f'<div class="s">Baa−10a a 13 semanas: {fmt(P.get("baa_13s"), 1, " %")}</div></div>')
    H.append(f'<div class="tarjeta"><div class="t">Convicción</div><div class="v">{conv}</div>'
             f'<div class="s">{e(ritmo)}</div></div>')
    H.append('</div>')

    H.append('<h2>Puerta de indexación</h2><table><tr><th>Condición (últimas 13 semanas)</th><th>Puntos</th><th>¿Se cumple?</th><th>Dato</th></tr>')
    for nom, pts, c, extra in P["condiciones"]:
        estado = "sin dato" if c is None else ("SÍ" if c else "no")
        cls = "si" if c else ""
        H.append(f'<tr><td>{e(nom)}</td><td>{pts}</td><td class="{cls}">{estado}</td><td>{e(extra)}</td></tr>')
    H.append('</table>')

    H.append('<h2>Grado de convicción</h2><table><tr><th>Bloque</th><th>Voto</th></tr>')
    for k, v in votos.items():
        H.append(f'<tr><td>{e(k)}</td><td>{"a favor del riesgo" if v > 0 else "en contra del riesgo" if v < 0 else "neutral"}</td></tr>')
    H.append(f'<tr><td>Dirección del mapa (RV global)</td><td>{e(fase_acwi or "sin dato")}</td></tr>')
    H.append(f'<tr><td>D · Beta o alfa</td><td>{e(d_txt or "sin dato")}</td></tr>')
    H.append(f'<tr><td>Sentimiento (réplica Euphoriameter, 0–100)</td><td>{fmt(sent, 0)}</td></tr>')
    H.append('</table>')

    # Mapa motores
    H.append('<h2>Mapa de rotación de motores</h2>')
    H.append('<p class="nota">Fuerza frente a letras del Tesoro y su impulso. Rastro de 8 semanas. Giro habitual: mejorando → liderando → debilitándose → rezagado.</p>')
    H.append('<div class="filtros">' + "".join(
        f'<label><input type="checkbox" class="fm" value="{m["t"]}" checked> {e(m["t"])}</label>' for m in motores) + '</div>')
    pts = [{"et": m["t"], "xs": m["xs"], "ys": m["ys"], "color": COLOR_FASE[m["fase"]]} for m in motores]
    H.append(f'<div id="mapa-motores">{svg_rrg(pts, "Fuerza relativa", "Impulso")}</div>')
    H.append('<table class="ord"><tr><th>Motor</th><th></th><th>Fase</th><th>Qué hacer</th><th>Fuerza</th><th>Impulso</th><th>RSI sem.</th><th>Agot. ↑/↓</th></tr>')
    orden = ["ARRANQUE", "ACELERACIÓN", "MADUREZ", "GIRO", "SUELO", "REZAGADO"]
    for m in sorted(motores, key=lambda z: orden.index(z["fase"])):
        H.append(f'<tr><td><b>{e(m["t"])}</b></td><td>{e(m["nombre"])}</td>'
                 f'<td><span class="fase" style="background:{COLOR_FASE[m["fase"]]}">{m["fase"]}</span></td>'
                 f'<td>{ACCION[m["fase"]]}</td><td>{fmt(m["X"], 1)}</td><td>{fmt(m["Y"], 1)}</td>'
                 f'<td>{fmt(m["rsi"], 0)}</td><td>{m["alc"]}/{m["baj"]}</td></tr>')
    H.append('</table>')
    if motores:
        H.append(f'<p class="nota">Semana cerrada: {motores[0]["fecha"]:%d-%m-%Y}. Proxies en dólares. Stock picking: ver bloque D. Market neutral: sin proxy.</p>')

    # Mapa macro
    clave = ["Inflación (IPC interanual, %)", "Crecimiento (encuestas Fed, media 3 meses)",
             "Estrés de crédito (Baa − bono 10 años)", "Liquidez neta de la Fed (billones $)"]
    cortos = {clave[0]: "Inflación", clave[1]: "Crecimiento", clave[2]: "Crédito (estrés)", clave[3]: "Liquidez"}
    pm = [{"et": cortos[k], "xs": mac[k]["rx"], "ys": mac[k]["ry"], "color": COLOR_FASE[mac[k]["fase"]]}
          for k in clave if k in mac]
    H.append('<h2>Mapa macro</h2><p class="nota">Nivel (percentil histórico) frente a impulso (percentil de su cambio reciente). Rastro de 8 periodos.</p>')
    H.append(svg_rrg(pm, "Nivel (percentil)", "Impulso (percentil)", centro=50, rango=(0, 100, 0, 100),
                     cuadrantes=("ALTO Y SUBIENDO", "ALTO Y BAJANDO", "BAJO Y BAJANDO", "BAJO Y SUBIENDO")))

    H.append('<h2>Variables</h2><table class="ord"><tr><th>Variable</th><th>Valor</th><th>Fecha</th><th>Percentil</th><th>Cambio reciente</th><th>Fase</th></tr>')
    for v in macro:
        viejo = not antiguedad_ok(v["fecha"], "m" if v["frecuencia"] == "m" else "s")
        unidad = " %" if v["modo"] == "pct" else ""
        per = f'{v["periodos"]} {"meses" if v["frecuencia"] == "m" else "sem."}'
        H.append(f'<tr><td>{e(v["nombre"])}</td><td>{fmt(v["valor"], 2)}</td>'
                 f'<td class="{"viejo" if viejo else ""}">{v["fecha"]:%d-%m-%Y}{" ⚠" if viejo else ""}</td>'
                 f'<td>{fmt(v["nivel"], 0)}</td><td>{fmt(v["impulso"], 2, unidad)} ({per})</td>'
                 f'<td><span class="fase" style="background:{COLOR_FASE[v["fase"]]}">{v["fase"]}</span></td></tr>')
    H.append('</table><p class="nota">⚠ = dato desfasado.</p>')
    H.append('</section>')

    # ===== PESTAÑA 2: OPCIONES
    H.append('<section id="t2" class="pest">')
    H.append('<p class="nota">Señales de días a semanas: sirven para elegir el momento de cada tramo, no para el régimen.</p>')
    H.append('<div class="tarjetas">')
    if gm:
        signo = "POSITIVA" if gm["gex_mm"] > 0 else "NEGATIVA"
        efecto = "amortigua los movimientos" if gm["gex_mm"] > 0 else "amplifica los movimientos"
        H.append(f'<div class="tarjeta {"alerta" if gm["gex_mm"] < 0 else ""}"><div class="t">Gamma de los creadores de mercado</div>'
                 f'<div class="v">{signo}</div><div class="s">{efecto} · {fmt(gm["gex_mm"], 1)} mil M$ por 1 %</div></div>')
        if gm["giro"]:
            dist = (gm["spot"] / gm["giro"] - 1) * 100
            H.append(f'<div class="tarjeta"><div class="t">Nivel de giro de la gamma</div><div class="v">{fmt(gm["giro"], 0)}</div>'
                     f'<div class="s">S&P 500 a {fmt(dist, 1, " %")} del giro</div></div>')
        H.append(f'<div class="tarjeta"><div class="t">Muros (próximos 70 días)</div><div class="v">{fmt(gm["muro_put"], 0)} – {fmt(gm["muro_call"], 0)}</div>'
                 f'<div class="s">soporte put · resistencia call</div></div>')
    else:
        H.append('<div class="tarjeta"><div class="t">Gamma</div><div class="v">sin dato</div></div>')
    if "SKEW" in cb:
        sk = cb["SKEW"]
        H.append(f'<div class="tarjeta"><div class="t">SKEW</div><div class="v">{fmt(sk.iloc[-1], 0)}</div>'
                 f'<div class="s">percentil {fmt(pct(sk), 0)} desde 1990 · {sk.index[-1]:%d-%m}</div></div>')
    H.append('</div>')

    if pc is not None and len(pc):
        H.append('<h2>Ratio put/call</h2><table><tr><th>Tipo</th><th>Último</th><th>Media 10 días</th><th>Percentil (media 10 d.)</th></tr>')
        nombres_pc = {"total": "Total", "acciones": "Acciones", "indices": "Índices", "spx": "S&P 500"}
        for col, nom in nombres_pc.items():
            if col not in pc.columns:
                continue
            s = pc[col].dropna()
            if not len(s):
                continue
            m10 = s.rolling(10).mean().dropna()
            pp = fmt(pct(m10), 0) if len(m10) >= 60 else "historia corta"
            H.append(f'<tr><td>{nom}</td><td>{fmt(s.iloc[-1], 2)}</td><td>{fmt(m10.iloc[-1] if len(m10) else None, 2)}</td><td>{pp}</td></tr>')
        H.append(f'</table><p class="nota">Último dato: {pc.index[-1]:%d-%m-%Y}. Percentil alto = mucha cobertura (miedo); bajo = complacencia. Historia desde {pc.index[0]:%m-%Y}.</p>')
        if "acciones" in pc.columns:
            H.append(svg_linea(pc["acciones"].rolling(10).mean().dropna().iloc[-250:]))
            H.append('<p class="nota">Put/call de acciones, media de 10 días, último año.</p>')
    if gm and len(gm["hist"]) > 5:
        H.append(svg_linea(gm["hist"]["gex_mm"].dropna(), ref=0, color="#7a3fd1"))
        H.append('<p class="nota">Gamma neta, histórico propio.</p>')

    if cot_series:
        H.append('<h2>Posicionamiento (CFTC, semanal)</h2><table><tr><th>Mercado · grupo</th><th>Neto (% del interés abierto)</th>'
                 '<th>Cambio 4 semanas</th><th>Percentil 3 años</th><th>Fecha</th></tr>')
        for k, s in cot_series.items():
            s3 = s[s.index >= s.index[-1] - pd.Timedelta(days=3 * 365)]
            ch = s.iloc[-1] - s.iloc[-5] if len(s) > 5 else np.nan
            p3 = pct(s3)
            cls = "extremo" if (p3 >= 90 or p3 <= 10) else ""
            H.append(f'<tr><td>{e(k)}</td><td>{fmt(s.iloc[-1], 1, " %")}</td><td>{fmt(ch, 1)}</td>'
                     f'<td class="{cls}">{fmt(p3, 0)}</td><td>{s.index[-1]:%d-%m-%Y}</td></tr>')
        H.append('</table><p class="nota">Percentil ≥ 90 o ≤ 10 = posicionamiento extremo (resaltado).</p>')
    H.append('</section>')

    # ===== PESTAÑA 3: CALENDARIO
    H.append('<section id="t3" class="pest">')
    H.append(f'<div class="tarjetas"><div class="tarjeta {"alerta" if en_blackout else ""}"><div class="t">Blackout de recompras (estimado)</div>'
             f'<div class="v">{"SÍ" if en_blackout else "NO"}</div><div class="s">estimación, no dato oficial</div></div>')
    if "anio_actual" in est:
        H.append(f'<div class="tarjeta"><div class="t">Ciclo presidencial</div><div class="v" style="font-size:1.1em">{e(est["anio_actual"])}</div></div>')
    H.append('</div>')
    H.append('<h2>Próximos 120 días</h2><table><tr><th>Fecha</th><th>Evento</th><th>Tipo</th></tr>')
    dias_sem = ["lun", "mar", "mié", "jue", "vie", "sáb", "dom"]
    for d, t, c in eventos:
        H.append(f'<tr><td>{dias_sem[d.weekday()]} {d:%d-%m-%Y}</td><td>{e(t)}</td><td><span class="tipo">{e(c)}</span></td></tr>')
    H.append('</table>')
    if "meses" in est:
        nm = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
        H.append(f'<h2>Estacionalidad del S&P 500 (desde {est["desde"]})</h2>'
                 '<table><tr><th>Mes</th><th>Media</th><th>Mediana</th><th>% meses positivos</th></tr>')
        for mes, fila in est["meses"].iterrows():
            marca = ' class="hoy"' if mes in (HOY.month, HOY.month % 12 + 1) else ""
            H.append(f'<tr{marca}><td>{nm[mes - 1]}</td><td>{fmt(fila["media"], 2, " %")}</td>'
                     f'<td>{fmt(fila["mediana"], 2, " %")}</td><td>{fmt(fila["positivos"], 0, " %")}</td></tr>')
        H.append('</table>')
        H.append('<h2>Ciclo presidencial (rentabilidad anual)</h2><table><tr><th>Año del ciclo</th><th>Media</th><th>Mediana</th><th>% años positivos</th></tr>')
        for nom, me, md, po in est["ciclo"]:
            marca = ' class="hoy"' if nom == est["anio_actual"] else ""
            H.append(f'<tr{marca}><td>{nom}</td><td>{fmt(me, 1, " %")}</td><td>{fmt(md, 1, " %")}</td><td>{fmt(po, 0, " %")}</td></tr>')
        H.append('</table>')
    if "cambio_mes" in est:
        a, b, desde = est["cambio_mes"]
        H.append(f'<p class="nota">Cambio de mes (último día + 3 primeros, desde {desde}): media diaria {fmt(a, 3, " %")} frente a {fmt(b, 3, " %")} el resto de días.</p>')
    H.append('<p class="nota">Evidencia académica de la estacionalidad: débil e inestable.</p>')
    H.append('</section>')

    # ===== Estado de las fuentes
    H.append('<section class="fuentes"><h2>Estado de los datos</h2>')
    if ERRORES:
        H.append('<p class="error"><b>Fuentes con fallo hoy:</b> ' + "; ".join(e(n) for n, _ in ERRORES) + '</p>')
    else:
        H.append('<p>Todas las fuentes se han descargado correctamente.</p>')
    H.append('<details><summary>Última fecha de cada fuente</summary><table>')
    for k in sorted(FECHAS):
        H.append(f'<tr><td>{e(k)}</td><td>{pd.Timestamp(FECHAS[k]):%d-%m-%Y}</td></tr>')
    H.append('</table></details></section>')

    # ===== Resumen en texto (para lectura automática)
    R = [f"RESUMEN {HOY:%d-%m-%Y}",
         f"Techo={P['techo_txt']} (ECY {fmt(ecy, 2)} %), puntos={P['puntos']}/6, tramo={P['tramo']}/3, veto={'sí' if P['veto'] else 'no'}, indexación={exp_txt}",
         f"Convicción={conv}; votos=" + ", ".join(f"{k}:{v:+d}" for k, v in votos.items()) + f"; dirección RV global={fase_acwi}; beta/alfa={d_txt}; sentimiento={fmt(sent, 0)}",
         "Motores: " + "; ".join(f"{m['t']}={m['fase']}" for m in motores),
         "Macro: " + "; ".join(f"{v['nombre']}={v['fase']} (p{fmt(v['nivel'], 0)})" for v in macro)]
    if gm:
        R.append(f"Gamma={'positiva' if gm['gex_mm'] > 0 else 'negativa'} ({fmt(gm['gex_mm'], 1)}), giro={fmt(gm['giro'], 0)}")
    R.append(f"Blackout estimado={'sí' if en_blackout else 'no'}")
    if ERRORES:
        R.append("Fallos: " + ", ".join(n for n, _ in ERRORES))
    H.append('<pre class="resumen">' + e("\n".join(R)) + '</pre>')

    H.append(PIE)
    with open(os.path.join(CARPETA_WEB, "index.html"), "w", encoding="utf-8") as f:
        f.write("".join(H))
    print("\n".join(R))
    for n, m in ERRORES:
        print("FALLO", n, m)


CABECERA = """<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Diagnosticador de mercados</title>
<style>
:root{--f:#1d2733;--s:#5b6776;--b:#e3e7ec;--fondo:#f6f8fa}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;color:var(--f);background:#fff;margin:0;padding:14px;max-width:1100px;margin:auto;font-size:15px;line-height:1.4}
header{display:flex;justify-content:space-between;align-items:baseline;flex-wrap:wrap;gap:6px;margin-bottom:10px}
h1{font-size:1.25em;margin:0}
.fecha{color:var(--s);font-size:.85em}
nav{display:flex;gap:6px;border-bottom:1px solid var(--b);margin-bottom:14px;flex-wrap:wrap}
nav button{border:0;background:none;padding:9px 14px;font-size:1em;cursor:pointer;color:var(--s);border-bottom:3px solid transparent}
nav button.activa{color:var(--f);border-bottom-color:#2f6fdb;font-weight:600}
.pest{display:none}.pest.activa{display:block}
h2{font-size:1.05em;margin:22px 0 8px}
.tarjetas{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
.tarjeta{background:var(--fondo);border:1px solid var(--b);border-radius:10px;padding:10px 12px}
.tarjeta.alerta{background:#fff1ec;border-color:#f3b49b}
.tarjeta .t{font-size:.8em;color:var(--s)}.tarjeta .v{font-size:1.5em;font-weight:700;margin:2px 0}.tarjeta .s{font-size:.8em;color:var(--s)}
table{border-collapse:collapse;width:100%;font-size:.88em;display:block;overflow-x:auto}
th,td{padding:6px 8px;border-bottom:1px solid var(--b);text-align:left;white-space:nowrap}
th{background:var(--fondo);font-weight:600;cursor:default}
table.ord th{cursor:pointer}
td.si{color:#1f7a45;font-weight:700}td.viejo{color:#c0392b}td.extremo{background:#fff1ec;font-weight:700}
tr.hoy td{background:#eef4ff;font-weight:600}
.fase{color:#fff;border-radius:6px;padding:2px 7px;font-size:.8em;font-weight:600}
.tipo{background:var(--fondo);border:1px solid var(--b);border-radius:6px;padding:1px 6px;font-size:.8em}
.nota{color:var(--s);font-size:.82em;margin:6px 0}
.grafico{width:100%;max-width:760px;height:auto;display:block;margin:6px 0}
.mini{width:100%;max-width:760px;height:auto;display:block;background:var(--fondo);border-radius:8px;margin-top:8px}
.grafico .cuad{font-size:12px;font-weight:700;opacity:.7}.grafico .eje{font-size:12px;fill:#5b6776}.grafico .etq{font-size:11px;font-weight:600;fill:#1d2733}
.filtros{display:flex;flex-wrap:wrap;gap:4px 10px;font-size:.8em;margin:4px 0}
.fuentes{margin-top:26px;border-top:1px solid var(--b);padding-top:6px;font-size:.88em}
.error{color:#c0392b}
.resumen{white-space:pre-wrap;font-size:.75em;color:var(--s);background:var(--fondo);padding:8px;border-radius:8px;margin-top:16px}
</style></head><body>
<header><h1>Diagnosticador de mercados</h1><span class="fecha">Actualizado {{FECHA}} (hora peninsular aprox.)</span></header>
<nav><button class="activa" data-t="t1">Diagnosticador</button><button data-t="t2">Opciones</button><button data-t="t3">Calendario</button></nav>
"""

PIE = r"""
<script>
document.querySelectorAll('nav button').forEach(function(b){b.addEventListener('click',function(){
 document.querySelectorAll('nav button').forEach(function(x){x.classList.remove('activa')});
 document.querySelectorAll('.pest').forEach(function(x){x.classList.remove('activa')});
 b.classList.add('activa');document.getElementById(b.dataset.t).classList.add('activa');});});
document.querySelectorAll('.fm').forEach(function(c){c.addEventListener('change',function(){
 document.querySelectorAll('#mapa-motores g.serie').forEach(function(g){
  if(g.getAttribute('data-t')===c.value){g.style.display=c.checked?'':'none';}});});});
document.querySelectorAll('table.ord').forEach(function(tb){tb.querySelectorAll('th').forEach(function(th,i){
 th.addEventListener('click',function(){var filas=Array.from(tb.querySelectorAll('tr')).slice(1);
  var asc=th.dataset.asc!=='1';th.dataset.asc=asc?'1':'0';
  filas.sort(function(a,b){var x=a.children[i].innerText,y=b.children[i].innerText;
   var nx=parseFloat(x.replace(/\./g,'').replace(',','.')),ny=parseFloat(y.replace(/\./g,'').replace(',','.'));
   if(!isNaN(nx)&&!isNaN(ny))return asc?nx-ny:ny-nx;return asc?x.localeCompare(y):y.localeCompare(x);});
  filas.forEach(function(f){f.parentNode.appendChild(f);});});});});
</script>
</body></html>
"""

if __name__ == "__main__":
    main()
