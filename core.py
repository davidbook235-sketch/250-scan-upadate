"""Nifty 250 Scanner - core engine (Angel One + indicators + backtest + charts + Telegram)."""
import io
import json
import os
import time
import zlib
import datetime as dt
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

IST = ZoneInfo("Asia/Kolkata")
SCRIP_URL = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
UNIVERSE_URL = "https://niftyindices.com/IndexConstituent/ind_niftylargemidcap250list.csv"
NIFTY_TOKEN = "99926000"  # NSE Nifty 50 index token (Angel One)
UA = {"User-Agent": "Mozilla/5.0"}

DEFAULT_P = dict(score_buy=4, score_watch=3, rsi_min=55, vol_mult=1.5, brk_n=20,
                 atr_mult=1.5, rr=2.0, adx_on=True, adx_min=20,
                 regime_on=True, sector_on=True)


def now_ist():
    return dt.datetime.now(IST).replace(tzinfo=None)


def market_open():
    n = now_ist()
    return n.weekday() < 5 and dt.time(9, 15) <= n.time() <= dt.time(15, 30)


def get_secret(name, default=""):
    v = os.environ.get(name)
    if v:
        return v
    try:
        import streamlit as st
        return st.secrets.get(name, default)
    except Exception:
        return default


# --------------------------------------------------------------------------- indicators
def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(c, n=14):
    d = c.diff()
    up, dn = d.clip(lower=0), -d.clip(upper=0)
    ru = up.ewm(alpha=1 / n, adjust=False).mean()
    rd = dn.ewm(alpha=1 / n, adjust=False).mean()
    return (100 - 100 / (1 + ru / rd.replace(0, np.nan))).fillna(100)


def atr(df, n=14):
    pc = df["close"].shift()
    tr = pd.concat([df["high"] - df["low"], (df["high"] - pc).abs(), (df["low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def adx(df, n=14):
    up, dn = df["high"].diff(), -df["low"].diff()
    pdm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=df.index)
    mdm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=df.index)
    a = atr(df, n)
    pdi = 100 * pdm.ewm(alpha=1 / n, adjust=False).mean() / a
    mdi = 100 * mdm.ewm(alpha=1 / n, adjust=False).mean() / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(alpha=1 / n, adjust=False).mean()


def add_indicators(df, mode, P):
    d = df.copy()
    f, s = (20, 50) if mode == "swing" else (9, 21)
    c = d["close"]
    d["ema_f"], d["ema_s"] = ema(c, f), ema(c, s)
    d["rsi"] = rsi(c)
    d["macd"] = ema(c, 12) - ema(c, 26)
    d["macd_sig"] = ema(d["macd"], 9)
    d["atr"] = atr(d)
    d["adx"] = adx(d)
    d["vol_ma"] = d["volume"].rolling(20).mean().shift(1)
    d["hh"] = d["high"].rolling(P["brk_n"]).max().shift(1)
    if mode == "intraday":
        day = d.index.normalize()
        tp = (d["high"] + d["low"] + c) / 3
        d["vwap"] = (tp * d["volume"]).groupby(day).cumsum() / d["volume"].groupby(day).cumsum().replace(0, np.nan)
        d["vwap_ok"] = c > d["vwap"]
    else:
        d["vwap_ok"] = True
    d["c_ema"] = d["ema_f"] > d["ema_s"]
    d["c_rsi"] = d["rsi"] > P["rsi_min"]
    d["c_macd"] = d["macd"] > d["macd_sig"]
    d["c_brk"] = c > d["hh"]
    d["c_vol"] = d["volume"] > P["vol_mult"] * d["vol_ma"]
    d["score"] = d[["c_ema", "c_rsi", "c_macd", "c_brk", "c_vol"]].astype(int).sum(axis=1)
    return d


def reason_labels(mode, P):
    f, s = (20, 50) if mode == "swing" else (9, 21)
    unit = "day" if mode == "swing" else "bar"
    return [f"EMA{f} > EMA{s}", f"RSI > {P['rsi_min']}", "MACD bullish",
            f"{P['brk_n']}-{unit} breakout", "Volume spike"]


# --------------------------------------------------------------------------- support / resistance
def sr_levels(df, price, atr_v, window=5, lookback=160, n=2):
    d = df.tail(lookback)
    hi, lo = d["high"].values, d["low"].values
    piv = []
    for i in range(window, len(d) - window):
        if hi[i] == hi[i - window:i + window + 1].max():
            piv.append(hi[i])
        if lo[i] == lo[i - window:i + window + 1].min():
            piv.append(lo[i])
    piv.sort()
    clusters = []
    tol = max(0.5 * atr_v, price * 0.003)
    for p in piv:
        if clusters and abs(p - np.mean(clusters[-1])) <= tol:
            clusters[-1].append(p)
        else:
            clusters.append([p])
    lv = [float(np.mean(c)) for c in clusters]
    sup = sorted([x for x in lv if x < price], reverse=True)[:n]
    res = sorted([x for x in lv if x > price])[:n]
    if not res and d["high"].max() > price:
        res = [float(d["high"].max())]
    return sup, res


# --------------------------------------------------------------------------- regime / analysis
def regime_info(nifty):
    if nifty is None or len(nifty) < 55:
        return dict(state="UNKNOWN", ok=True, close=np.nan, ema20=np.nan, ema50=np.nan)
    c = nifty["close"]
    e20, e50 = ema(c, 20).iloc[-1], ema(c, 50).iloc[-1]
    last = c.iloc[-1]
    if last > e50 and e20 > e50:
        st = "BULLISH"
    elif last > e50:
        st = "NEUTRAL"
    else:
        st = "BEARISH"
    return dict(state=st, ok=(st == "BULLISH"), close=float(last), ema20=float(e20), ema50=float(e50))


def regime_flags(nifty):
    c = nifty["close"]
    return (c > ema(c, 50)) & (ema(c, 20) > ema(c, 50))


def align_regime(flags, idx, mode):
    s = flags.astype(float).copy()
    s.index = s.index.normalize()
    s = s[~s.index.duplicated()]
    if mode == "intraday":
        s = s.shift(1)
    return s.reindex(idx.normalize()).ffill().fillna(0).astype(bool).values


def analyse(sym, df, mode, P):
    if df is None or len(df) < 60:
        return None, None
    d = add_indicators(df, mode, P)
    r = d.iloc[-1]
    if pd.isna(r["atr"]) or pd.isna(r["adx"]):
        return None, None
    entry = float(r["close"])
    risk = P["atr_mult"] * float(r["atr"])
    lb = 20 if mode == "swing" else 25
    ret = float(d["close"].iloc[-1] / d["close"].iloc[-lb - 1] - 1) * 100 if len(d) > lb else 0.0
    flags = [bool(r[k]) for k in ("c_ema", "c_rsi", "c_macd", "c_brk", "c_vol")]
    labels = reason_labels(mode, P)
    row = dict(Symbol=sym, Score=int(r["score"]), Entry=round(entry, 2),
               StopLoss=round(entry - risk, 2), Target=round(entry + P["rr"] * risk, 2),
               Reasons=", ".join(l for l, f in zip(labels, flags) if f),
               ADX=round(float(r["adx"]), 1), RSI=round(float(r["rsi"]), 1),
               VolX=round(float(r["volume"] / r["vol_ma"]), 2) if r["vol_ma"] and r["vol_ma"] > 0 else 0.0,
               Ret=round(ret, 2), vwap_ok=bool(r["vwap_ok"]))
    return row, d


def finalize(rows, universe, regime, P, mode):
    tbl = pd.DataFrame(rows)
    if tbl.empty:
        return tbl
    smap = dict(zip(universe["Symbol"], universe.get("Sector", pd.Series(["Unknown"] * len(universe)))))
    tbl["Sector"] = tbl["Symbol"].map(smap).fillna("Unknown")
    sec = tbl[tbl["Sector"] != "Unknown"].groupby("Sector")["Ret"].median()
    sec_rank = sec.rank(pct=True) if len(sec) >= 3 else pd.Series(1.0, index=sec.index)
    tbl["SectorRank"] = tbl["Sector"].map(sec_rank).fillna(1.0).round(2)
    tbl["SectorOK"] = tbl["SectorRank"] >= 0.5
    sigs, blocked = [], []
    for _, r in tbl.iterrows():
        fails = []
        if P["adx_on"] and r["ADX"] < P["adx_min"]:
            fails.append(f"ADX<{P['adx_min']}")
        if P["regime_on"] and not regime["ok"]:
            fails.append(f"Nifty {regime['state']}")
        if P["sector_on"] and not r["SectorOK"]:
            fails.append("Weak sector")
        if mode == "intraday" and not r["vwap_ok"]:
            fails.append("Below VWAP")
        if r["Score"] >= P["score_buy"] and not fails:
            sigs.append("BUY")
        elif r["Score"] >= P["score_watch"]:
            sigs.append("WATCH")
        else:
            sigs.append("-")
        blocked.append(", ".join(fails) if r["Score"] >= P["score_buy"] else "")
    tbl["Signal"], tbl["Blocked"] = sigs, blocked
    tbl = tbl[tbl["Signal"] != "-"].copy()
    tbl["_o"] = tbl["Signal"].map({"BUY": 0, "WATCH": 1})
    tbl = tbl.sort_values(["_o", "Score", "ADX"], ascending=[True, False, False]).drop(columns="_o")
    return tbl.reset_index(drop=True)


def run_scan(fetch, universe, tokens, mode, interval, P, topn=250, progress=None, ttl=300):
    """fetch(token, interval, days) -> OHLCV DataFrame indexed by datetime."""
    nifty = cached(("nifty",), 600, lambda: fetch(NIFTY_TOKEN, "ONE_DAY", 400))
    regime = regime_info(nifty)
    iv, days = ("ONE_DAY", 400) if mode == "swing" else (interval, 12)
    mins = {"FIVE_MINUTE": 5, "FIFTEEN_MINUTE": 15, "THIRTY_MINUTE": 30}.get(iv, 0)
    uni = universe.head(topn)
    rows, frames, missing = [], {}, []
    n = len(uni)
    for i, sym in enumerate(uni["Symbol"], 1):
        if progress:
            progress(i, n, sym)
        tok = tokens.get(sym)
        if not tok:
            missing.append(sym)
            continue
        try:
            df = cached((tok, iv, days), ttl, lambda: fetch(tok, iv, days))
            if mode == "intraday" and mins:
                df = df[df.index + pd.Timedelta(minutes=mins) <= now_ist()]
            row, d = analyse(sym, df, mode, P)
        except Exception as e:  # noqa: BLE001
            missing.append(f"{sym} ({str(e)[:40]})")
            continue
        if row:
            rows.append(row)
            frames[sym] = d
    tbl = finalize(rows, uni, regime, P, mode)
    return dict(table=tbl, frames=frames, regime=regime, missing=missing, mode=mode,
                time=now_ist(), scanned=len(rows))


_CACHE = {}


def cached(key, ttl, fn):
    now = time.time()
    v = _CACHE.get(key)
    if v and now - v[0] < ttl:
        return v[1].copy()
    df = fn()
    _CACHE[key] = (now, df)
    return df.copy()


# --------------------------------------------------------------------------- backtest
def backtest_symbol(sym, df, mode, P, regime_ok=None, max_hold=10, cost_pct=0.1):
    d = add_indicators(df, mode, P)
    sig = (d["score"] >= P["score_buy"]) & d["vwap_ok"]
    if P["adx_on"]:
        sig &= d["adx"] >= P["adx_min"]
    if P["regime_on"] and regime_ok is not None:
        sig &= pd.Series(regime_ok, index=d.index)
    sig = sig.fillna(False).values
    o, h, l, c = (d[k].values for k in ("open", "high", "low", "close"))
    a, idx, n = d["atr"].values, d.index, len(d)
    day_last = None
    if mode == "intraday":
        codes = pd.factorize(idx.normalize())[0]
        day_last = pd.Series(np.arange(n)).groupby(codes).transform("max").values
    trades, i = [], 60
    while i < n - 1:
        if not sig[i] or np.isnan(a[i]):
            i += 1
            continue
        if mode == "intraday" and day_last[i + 1] != day_last[i]:
            i += 1
            continue
        entry = o[i + 1]
        risk = P["atr_mult"] * a[i]
        if risk <= 0 or entry <= 0:
            i += 1
            continue
        sl, tgt = entry - risk, entry + P["rr"] * risk
        last = day_last[i + 1] if mode == "intraday" else min(i + 1 + max_hold, n - 1)
        exit_p, why, j = c[last], "TIME", last
        for k in range(i + 1, last + 1):
            if k > i + 1 and o[k] <= sl:
                exit_p, why, j = o[k], "SL", k
                break
            if l[k] <= sl:
                exit_p, why, j = sl, "SL", k
                break
            if h[k] >= tgt:
                exit_p, why, j = tgt, "TARGET", k
                break
        risk_pct = risk / entry * 100
        pnl = (exit_p / entry - 1) * 100 - cost_pct
        trades.append(dict(Symbol=sym, Entry_time=idx[i + 1], Exit_time=idx[j], Entry=round(entry, 2),
                           Exit=round(exit_p, 2), Exit_reason=why, PnL_pct=round(pnl, 2),
                           R=round(pnl / risk_pct, 2), Bars=j - i))
        i = j + 1
    return trades


def backtest_stats(trades, risk_pct=1.0):
    if not trades:
        return None
    t = pd.DataFrame(trades).sort_values("Exit_time").reset_index(drop=True)
    eq = (1 + t["R"] * risk_pct / 100).cumprod() * 100
    peak = eq.cummax()
    gp, gl = t.loc[t["R"] > 0, "R"].sum(), -t.loc[t["R"] <= 0, "R"].sum()
    stats = dict(Trades=len(t), WinRate=round(float((t["R"] > 0).mean() * 100), 1), AvgR=round(float(t["R"].mean()), 2),
                 ProfitFactor=round(float(gp / gl), 2) if gl > 0 else float("inf"),
                 Return_pct=round(float(eq.iloc[-1] - 100), 1), MaxDD_pct=round(float((eq / peak - 1).min() * 100), 1),
                 AvgBars=round(float(t["Bars"].mean()), 1))
    return dict(stats=stats, trades=t, equity=pd.Series(eq.values, index=t["Exit_time"]))


def run_backtest(fetch, symbols, tokens, mode, interval, P, days, max_hold=10, cost_pct=0.1,
                 risk_pct=1.0, progress=None):
    nifty = fetch(NIFTY_TOKEN, "ONE_DAY", max(days, 60) + 120 if mode == "swing" else days + 120)
    flags = regime_flags(nifty)
    iv = "ONE_DAY" if mode == "swing" else interval
    all_t = []
    for i, sym in enumerate(symbols, 1):
        if progress:
            progress(i, len(symbols), sym)
        tok = tokens.get(sym)
        if not tok:
            continue
        try:
            df = fetch(tok, iv, days + (120 if mode == "swing" else 10))
            reg = align_regime(flags, df.index, mode)
            all_t += backtest_symbol(sym, df, mode, P, reg, max_hold, cost_pct)
        except Exception:  # noqa: BLE001
            continue
    out = backtest_stats(all_t, risk_pct)
    if out:
        t = out["trades"]
        out["per_symbol"] = t.groupby("Symbol").agg(Trades=("R", "size"), WinRate=("R", lambda x: round((x > 0).mean() * 100, 1)),
                                                    AvgR=("R", "mean"), TotalR=("R", "sum")).round(2).sort_values("TotalR", ascending=False)
    return out


# --------------------------------------------------------------------------- charts + telegram
BG, FG, GREEN, RED, BLUE, AMBER = "#0e1117", "#e6e6e6", "#26a69a", "#ef5350", "#42a5f5", "#ffca28"


def make_chart(sym, d, row, mode, bars=None):
    n = bars or (90 if mode == "swing" else 130)
    v = d.tail(n)
    price, a = row["Entry"], float(d["atr"].iloc[-1])
    sup, res = sr_levels(d, price, a)
    x = np.arange(len(v))
    up = (v["close"] >= v["open"]).values
    col = [GREEN if u else RED for u in up]
    fig, (ax, axv) = plt.subplots(2, 1, figsize=(8, 6.4), sharex=True, facecolor=BG,
                                  gridspec_kw={"height_ratios": [4, 1], "hspace": 0.05})
    for a_ in (ax, axv):
        a_.set_facecolor(BG)
        a_.tick_params(colors=FG, labelsize=8)
        for s in a_.spines.values():
            s.set_color("#333")
        a_.grid(color="#222", lw=0.5)
    ax.vlines(x, v["low"], v["high"], color=col, lw=1)
    body = (v["close"] - v["open"]).abs().clip(lower=price * 0.0003)
    ax.bar(x, body, bottom=np.minimum(v["open"], v["close"]), color=col, width=0.65)
    ef, es = ("ema_f", "ema_s")
    f, s = (20, 50) if mode == "swing" else (9, 21)
    ax.plot(x, v[ef], color=AMBER, lw=1.1, label=f"EMA{f}")
    ax.plot(x, v[es], color=BLUE, lw=1.1, label=f"EMA{s}")
    if mode == "intraday" and "vwap" in v:
        ax.plot(x, v["vwap"], color="#ab47bc", lw=1, ls=":", label="VWAP")
    xr = len(v) + 9

    def hl(y, color, label, ls="--", lw=1.0):
        ax.axhline(y, color=color, ls=ls, lw=lw, alpha=0.9)
        ax.text(xr, y, f"{label} {y:.2f}", color=color, fontsize=7.5, va="center", ha="right",
                bbox=dict(facecolor=BG, edgecolor="none", pad=1, alpha=0.85))
    for k, y in enumerate(sup):
        hl(y, "#66bb6a", f"S{k + 1}")
    for k, y in enumerate(res):
        hl(y, "#ff7043", f"R{k + 1}")
    hl(row["Entry"], BLUE, "Entry", "-", 1.2)
    hl(row["StopLoss"], RED, "SL", "-", 1.2)
    hl(row["Target"], GREEN, "TGT", "-", 1.2)
    lo = min(v["low"].min(), row["StopLoss"], *(sup or [1e12])) * 0.995
    hi = max(v["high"].max(), row["Target"], *(res or [0])) * 1.005
    ax.set_ylim(lo, hi)
    ax.set_xlim(-1, xr + 1)
    axv.bar(x, v["volume"], color=col, width=0.65, alpha=0.8)
    ticks = np.linspace(0, len(v) - 1, 6).astype(int)
    fmt = "%d %b" if mode == "swing" else "%d %b %H:%M"
    axv.set_xticks(ticks)
    axv.set_xticklabels([v.index[t].strftime(fmt) for t in ticks])
    ax.legend(loc="upper left", fontsize=7, facecolor=BG, edgecolor="#333", labelcolor=FG)
    ax.set_title(f"{sym}  |  {row['Signal']} {row['Score']}/5  |  RSI {row['RSI']}  ADX {row['ADX']}  "
                 f"({'Swing 1D' if mode == 'swing' else 'Intraday'})", color=FG, fontsize=10)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=130, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return buf.getvalue(), sup, res


def build_top5(res, n=5):
    t = res["table"]
    if t.empty:
        return []
    buys = t[t["Signal"] == "BUY"].sort_values(["Score", "ADX", "VolX"], ascending=False).head(n)
    out = []
    for k, (_, r) in enumerate(buys.iterrows(), 1):
        png, sup, rs = make_chart(r["Symbol"], res["frames"][r["Symbol"]], r, res["mode"])
        cap = (f"🟢 BUY #{k}: {r['Symbol']}  (Score {r['Score']}/5)\n"
               f"Entry {r['Entry']} | SL {r['StopLoss']} | Target {r['Target']}\n"
               f"Support: {', '.join(f'{x:.2f}' for x in sup) or '-'}\n"
               f"Resistance: {', '.join(f'{x:.2f}' for x in rs) or '-'}\n"
               f"ADX {r['ADX']} | RSI {r['RSI']} | Vol x{r['VolX']} | {r['Sector']}\n"
               f"Why: {r['Reasons']}")
        out.append(dict(symbol=r["Symbol"], png=png, caption=cap))
    return out


def tg_send_text(token, chat_id, text):
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      data={"chat_id": chat_id, "text": text}, timeout=30)
    return r.ok, r.text[:200]


def tg_send_photo(token, chat_id, png, caption):
    r = requests.post(f"https://api.telegram.org/bot{token}/sendPhoto",
                      data={"chat_id": chat_id, "caption": caption[:1000]},
                      files={"photo": ("chart.png", png, "image/png")}, timeout=60)
    return r.ok, r.text[:200]


def tg_send_top5(token, chat_id, res, items):
    rg = res["regime"]
    head = (f"📊 Nifty250 {res['mode'].upper()} scan  {res['time']:%d-%b %H:%M}\n"
            f"Nifty regime: {rg['state']}  |  Top {len(items)} BUY")
    tg_send_text(token, chat_id, head)
    ok = True
    for it in items:
        ok &= tg_send_photo(token, chat_id, it["png"], it["caption"])[0]
        time.sleep(0.6)
    return ok


# --------------------------------------------------------------------------- Angel One
class Angel:
    MAX_DAYS = {"ONE_DAY": 2000, "ONE_HOUR": 400, "THIRTY_MINUTE": 200, "FIFTEEN_MINUTE": 200,
                "FIVE_MINUTE": 100, "ONE_MINUTE": 30}

    def __init__(self, api_key, client_id, pin, totp_secret):
        self.api_key, self.client_id, self.pin, self.totp = api_key, client_id, pin, totp_secret
        self._last = 0.0
        self.login()

    def login(self):
        import pyotp
        from SmartApi import SmartConnect
        self.api = SmartConnect(api_key=self.api_key)
        data = self.api.generateSession(self.client_id, self.pin, pyotp.TOTP(self.totp).now())
        if not data or not data.get("status"):
            raise RuntimeError(f"Angel login failed: {data.get('message') if data else 'no response'}")

    def _wait(self):
        gap = 0.38 - (time.time() - self._last)
        if gap > 0:
            time.sleep(gap)
        self._last = time.time()

    def candles(self, token, interval, days, exchange="NSE"):
        to = now_ist()
        start = to - dt.timedelta(days=days)
        step = dt.timedelta(days=self.MAX_DAYS.get(interval, 100))
        parts, cur = [], start
        while cur < to:
            end = min(cur + step, to)
            parts.append(self._chunk(token, interval, cur, end, exchange))
            cur = end
        df = pd.concat([p for p in parts if p is not None and len(p)]) if parts else pd.DataFrame()
        if df.empty:
            raise RuntimeError("no candle data")
        return df[~df.index.duplicated()].sort_index()

    def _chunk(self, token, interval, frm, to, exchange):
        p = {"exchange": exchange, "symboltoken": str(token), "interval": interval,
             "fromdate": frm.strftime("%Y-%m-%d %H:%M"), "todate": to.strftime("%Y-%m-%d %H:%M")}
        for attempt in range(4):
            self._wait()
            try:
                res = self.api.getCandleData(p)
            except Exception:  # noqa: BLE001
                time.sleep(1.5)
                continue
            if res and res.get("status") and res.get("data"):
                df = pd.DataFrame(res["data"], columns=["ts", "open", "high", "low", "close", "volume"])
                df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize(None)
                return df.set_index("ts").astype(float)
            code = (res or {}).get("errorcode", "")
            if code in ("AG8001", "AG8002", "AB8050"):
                self.login()
            elif res and res.get("status") and not res.get("data"):
                return None
            else:
                time.sleep(1.5)
        return None


def load_tokens(path="tokens_nse.json"):
    """symbol -> Angel token for NSE equity (-EQ). Cached for the day."""
    if os.path.exists(path) and dt.date.fromtimestamp(os.path.getmtime(path)) == dt.date.today():
        return json.load(open(path))
    try:
        data = requests.get(SCRIP_URL, timeout=120).json()
        m = {x["name"]: x["token"] for x in data if x.get("exch_seg") == "NSE" and x.get("symbol", "").endswith("-EQ")}
        json.dump(m, open(path, "w"))
        return m
    except Exception:  # noqa: BLE001
        return json.load(open(path)) if os.path.exists(path) else {}


def load_universe(path="universe.csv", fetch=True):
    """Nifty LargeMidcap 250 list (with sector). Falls back to universe.csv."""
    if fetch:
        try:
            r = requests.get(UNIVERSE_URL, headers=UA, timeout=20)
            r.raise_for_status()
            u = pd.read_csv(io.StringIO(r.text)).rename(columns={"Industry": "Sector"})
            u = u[["Symbol", "Sector"]].dropna()
            u.to_csv(path, index=False)
            return u
        except Exception:  # noqa: BLE001
            pass
    u = pd.read_csv(path)
    if "Sector" not in u:
        u["Sector"] = "Unknown"
    return u[["Symbol", "Sector"]]


# --------------------------------------------------------------------------- demo feed (offline test)
def demo_fetch(token, interval, days, exchange="NSE"):
    rng = np.random.default_rng(zlib.crc32(str(token).encode()))
    if interval == "ONE_DAY":
        idx = pd.bdate_range(end=pd.Timestamp(now_ist()).normalize(), periods=max(int(days * 0.68), 90))
    else:
        bars = {"FIVE_MINUTE": 75, "FIFTEEN_MINUTE": 25, "THIRTY_MINUTE": 13}.get(interval, 25)
        step = {"FIVE_MINUTE": 5, "FIFTEEN_MINUTE": 15, "THIRTY_MINUTE": 30}.get(interval, 15)
        ds = pd.bdate_range(end=pd.Timestamp(now_ist()).normalize(), periods=max(int(days * 0.68), 5))
        idx = pd.DatetimeIndex([d + pd.Timedelta(hours=9, minutes=15 + step * k) for d in ds for k in range(bars)])
    n = len(idx)
    drift = rng.normal(0.0004, 0.0003)
    ret = rng.normal(drift, 0.012 if interval == "ONE_DAY" else 0.003, n)
    c = 100 * rng.uniform(1, 20) * np.exp(np.cumsum(ret))
    o = np.r_[c[0], c[:-1]] * (1 + rng.normal(0, 0.002, n))
    h = np.maximum(o, c) * (1 + rng.uniform(0, 0.01, n))
    l = np.minimum(o, c) * (1 - rng.uniform(0, 0.01, n))
    v = rng.uniform(1e5, 5e5, n) * (1 + 2 * (rng.random(n) > 0.9))
    return pd.DataFrame(dict(open=o, high=h, low=l, close=c, volume=v), index=idx)
