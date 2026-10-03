import time

import pandas as pd
import streamlit as st

import core

try:
    from streamlit_autorefresh import st_autorefresh
except Exception:  # noqa: BLE001
    st_autorefresh = None

st.set_page_config(page_title="Nifty250 Scanner", page_icon="📈", layout="centered",
                   initial_sidebar_state="collapsed")
st.markdown("<style>.block-container{padding:1rem .8rem 3rem}</style>", unsafe_allow_html=True)
ss = st.session_state
ss.setdefault("res", None)
ss.setdefault("last_scan", 0.0)
ss.setdefault("sent", set())
ss.setdefault("top5", [])

st.title("📈 Nifty250 Scanner")

# ------------------------------------------------------------------ settings
with st.expander("⚙️ Settings", expanded=False):
    c1, c2 = st.columns(2)
    mode_label = c1.radio("Mode", ["Swing (1D)", "Intraday"], horizontal=True)
    mode = "swing" if mode_label.startswith("Swing") else "intraday"
    iv_label = c2.selectbox("Intraday candle", ["15 min", "5 min", "30 min"], disabled=(mode == "swing"))
    interval = {"15 min": "FIFTEEN_MINUTE", "5 min": "FIVE_MINUTE", "30 min": "THIRTY_MINUTE"}[iv_label]
    st.markdown("**Filters (ON/OFF)**")
    f1, f2, f3 = st.columns(3)
    adx_on = f1.toggle("ADX", True)
    regime_on = f2.toggle("Nifty regime", True)
    sector_on = f3.toggle("Sector", True)
    adx_min = st.slider("Min ADX", 10, 40, 20, disabled=not adx_on)
    st.markdown("**Strategy**")
    s1, s2 = st.columns(2)
    score_buy = s1.slider("BUY min score (of 5)", 3, 5, 4)
    rsi_min = s2.slider("RSI >", 50, 70, 55)
    s3, s4, s5 = st.columns(3)
    vol_mult = s3.number_input("Volume x", 1.0, 5.0, 1.5, 0.1)
    atr_mult = s4.number_input("SL = ATR x", 0.5, 4.0, 1.5, 0.1)
    rr = s5.number_input("Reward:Risk", 1.0, 5.0, 2.0, 0.5)
    st.markdown("**Scan / Alerts**")
    topn = st.slider("Kitne stocks scan karne hain", 20, 250, 250, 10)
    auto = st.toggle("🔄 Live auto-scan", False)
    every = st.slider("Auto-scan har (min)", 5, 30, 10, disabled=not auto)
    tg_auto = st.toggle("📨 Naye BUY par Telegram auto-send", False)
    demo = st.toggle("🧪 Demo data (Angel login ke bina test)", False)

P = dict(score_buy=score_buy, score_watch=3, rsi_min=rsi_min, vol_mult=vol_mult, brk_n=20,
         atr_mult=atr_mult, rr=rr, adx_on=adx_on, adx_min=adx_min, regime_on=regime_on, sector_on=sector_on)


# ------------------------------------------------------------------ helpers
def cred(name):
    return ss.get(f"in_{name}") or core.get_secret(name)


@st.cache_resource(ttl=6 * 3600, show_spinner="Angel One login...")
def angel_login(k, c, p, t):
    return core.Angel(k, c, p, t)


@st.cache_data(ttl=86400, show_spinner=False)
def universe():
    return core.load_universe()


@st.cache_resource(ttl=86400, show_spinner="Token list load ho rahi hai...")
def tokens():
    return core.load_tokens()


def get_feed():
    if demo:
        return core.demo_fetch, {s: s for s in universe()["Symbol"]}
    keys = [cred(k) for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PIN", "ANGEL_TOTP_SECRET")]
    if not all(keys):
        st.error("Angel One keys nahi mili. Setup tab me daalo (ya Demo ON karo).")
        st.stop()
    return angel_login(*keys).candles, tokens()


def uni():
    u = ss.get("uni_override")
    return u if u is not None else universe()


def do_scan():
    fetch, tok = get_feed()
    bar = st.progress(0.0, text="Scan shuru...")
    ttl = 60 if not auto else max(60, every * 60 - 30)
    res = core.run_scan(fetch, uni(), tok, mode, interval, P, topn,
                        progress=lambda i, n, s: bar.progress(i / n, text=f"{s}  ({i}/{n})"), ttl=ttl)
    bar.empty()
    ss.res, ss.last_scan = res, time.time()
    ss.top5 = core.build_top5(res)
    if tg_auto:
        send_telegram(only_new=True)


def send_telegram(only_new=False):
    token, chat = cred("TELEGRAM_BOT_TOKEN"), cred("TELEGRAM_CHAT_ID")
    if not (token and chat):
        st.warning("Telegram token/chat id Setup tab me daalo.")
        return
    day = time.strftime("%Y%m%d")
    items = [i for i in ss.top5 if not only_new or f"{day}|{ss.res['mode']}|{i['symbol']}" not in ss.sent]
    if not items:
        if not only_new:
            st.info("Abhi koi BUY signal nahi hai.")
        return
    ok = core.tg_send_top5(token, chat, ss.res, items)
    for i in items:
        ss.sent.add(f"{day}|{ss.res['mode']}|{i['symbol']}")
    st.toast("Telegram bhej diya ✅" if ok else "Telegram me error ❌")


# ------------------------------------------------------------------ tabs
tab1, tab2, tab3 = st.tabs(["📡 Scanner", "🧪 Backtest", "🔑 Setup"])

with tab1:
    open_ = core.market_open()
    st.caption(("🟢 Market OPEN" if open_ else "🔴 Market CLOSED — last available session ka data") +
               f"  •  IST {core.now_ist():%H:%M}")
    if auto and st_autorefresh:
        st_autorefresh(interval=every * 60 * 1000, key="auto")
    due = auto and (time.time() - ss.last_scan) >= every * 60 - 5
    if st.button("🔍 Scan Now", type="primary", use_container_width=True) or due:
        do_scan()

    res = ss.res
    if res is None:
        st.info("Settings check karke **Scan Now** dabao.")
    else:
        rg = res["regime"]
        icon = {"BULLISH": "🟢", "NEUTRAL": "🟡", "BEARISH": "🔴"}.get(rg["state"], "⚪")
        st.markdown(f"**Nifty regime:** {icon} {rg['state']}  \nNifty {rg['close']:.0f} | "
                    f"EMA20 {rg['ema20']:.0f} | EMA50 {rg['ema50']:.0f}")
        t = res["table"]
        nb = int((t["Signal"] == "BUY").sum()) if not t.empty else 0
        st.caption(f"Scan {res['time']:%H:%M:%S} • {res['scanned']} stocks • {nb} BUY • "
                   f"{len(t) - nb} WATCH • {len(res['missing'])} skip")
        if t.empty:
            st.warning("Is waqt koi BUY/WATCH signal nahi mila.")
        else:
            show = ["Symbol", "Signal", "Score", "Entry", "StopLoss", "Target", "ADX", "RSI", "VolX", "Sector", "Blocked", "Reasons"]
            st.dataframe(t[show], use_container_width=True, hide_index=True,
                         column_config={"Score": st.column_config.ProgressColumn("Score", min_value=0, max_value=5, format="%d")})
            st.download_button("⬇️ CSV download", t[show].to_csv(index=False).encode(),
                               f"scan_{mode}_{res['time']:%Y%m%d_%H%M}.csv", "text/csv", use_container_width=True)
            st.subheader("🏆 Top 5 BUY — Support / Resistance")
            if not ss.top5:
                st.caption("Abhi koi BUY rating nahi.")
            for it in ss.top5:
                st.image(it["png"], use_container_width=True)
                st.code(it["caption"], language=None)
            if ss.top5 and st.button("📨 Top 5 Telegram par bhejo", use_container_width=True):
                send_telegram()
            if sector_on and "SectorRank" in t:
                with st.expander("Sector strength"):
                    sec = t.groupby("Sector").agg(Stocks=("Symbol", "size"), AvgRet=("Ret", "mean"),
                                                  Rank=("SectorRank", "first")).round(2).sort_values("Rank", ascending=False)
                    st.dataframe(sec, use_container_width=True)
        if res["missing"]:
            with st.expander("Skip hue symbols"):
                st.write(", ".join(res["missing"][:80]))

with tab2:
    st.caption("Same strategy + filters (ADX, Nifty regime) historical data par. Sector filter backtest me nahi lagta.")
    default_syms = []
    if ss.res is not None and not ss.res["table"].empty:
        default_syms = list(ss.res["table"].head(10)["Symbol"])
    syms = st.multiselect("Stocks", list(uni()["Symbol"]), default=default_syms or list(uni()["Symbol"][:5]))
    b1, b2 = st.columns(2)
    days = b1.slider("History (days)", 90 if mode == "swing" else 10, 1000 if mode == "swing" else 90,
                     500 if mode == "swing" else 30)
    max_hold = b2.slider("Max hold (days)", 2, 30, 10, disabled=(mode == "intraday"))
    b3, b4 = st.columns(2)
    cost = b3.number_input("Cost+slippage % / trade", 0.0, 1.0, 0.1, 0.05)
    risk = b4.number_input("Risk % per trade", 0.25, 5.0, 1.0, 0.25)
    if st.button("▶️ Run Backtest", type="primary", use_container_width=True):
        if not syms:
            st.warning("Kam se kam 1 stock chuno.")
        else:
            fetch, tok = get_feed()
            bar = st.progress(0.0, text="Backtest...")
            ss.bt = core.run_backtest(fetch, syms, tok, mode, interval, P, days, max_hold, cost, risk,
                                      progress=lambda i, n, s: bar.progress(i / n, text=f"{s} ({i}/{n})"))
            bar.empty()
            ss.bt_none = ss.bt is None
    bt = ss.get("bt")
    if ss.get("bt_none"):
        st.warning("Is period me koi trade nahi bana. Filters ya score kam karke dekho.")
    elif bt:
        s = bt["stats"]
        m1, m2, m3 = st.columns(3)
        m1.metric("Trades", s["Trades"])
        m2.metric("Win rate", f"{s['WinRate']}%")
        m3.metric("Avg R", s["AvgR"])
        m4, m5, m6 = st.columns(3)
        m4.metric("Profit factor", s["ProfitFactor"])
        m5.metric("Return", f"{s['Return_pct']}%")
        m6.metric("Max DD", f"{s['MaxDD_pct']}%")
        st.line_chart(bt["equity"], height=220)
        st.markdown("**Symbol-wise**")
        st.dataframe(bt["per_symbol"], use_container_width=True)
        with st.expander("Saare trades"):
            st.dataframe(bt["trades"], use_container_width=True, hide_index=True)
        st.caption("Entry: signal ke agle candle ka open. SL aur Target ek hi candle me lage to SL maana gaya (conservative).")

with tab3:
    st.markdown("**Best tareeka:** Streamlit Cloud → App → Settings → *Secrets* me daalo (neeche format). "
                "Yahan daalne se sirf is session tak chalega.")
    st.code('ANGEL_API_KEY = "..."\nANGEL_CLIENT_ID = "..."\nANGEL_PIN = "..."\nANGEL_TOTP_SECRET = "..."\n'
            'TELEGRAM_BOT_TOKEN = "..."\nTELEGRAM_CHAT_ID = "..."', language="toml")
    for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PIN", "ANGEL_TOTP_SECRET", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        st.text_input(k, type="password" if k != "ANGEL_CLIENT_ID" and k != "TELEGRAM_CHAT_ID" else "default",
                      key=f"in_{k}", placeholder="secrets me set hai" if core.get_secret(k) else "")
    if st.button("Telegram test message"):
        ok, msg = core.tg_send_text(cred("TELEGRAM_BOT_TOKEN"), cred("TELEGRAM_CHAT_ID"), "✅ Nifty250 Scanner connected")
        st.success("Bhej diya") if ok else st.error(msg)
    up = st.file_uploader("Apni stock list upload karo (CSV: Symbol[, Sector])", type="csv")
    if up is not None:
        u = pd.read_csv(up)
        u["Sector"] = u["Sector"] if "Sector" in u else "Unknown"
        ss.uni_override = u[["Symbol", "Sector"]]
        st.success(f"{len(u)} symbols load ho gaye.")
    st.caption(f"Universe: {len(uni())} symbols (Nifty LargeMidcap 250 auto-fetch, fail ho to universe.csv).")
