# 📈 Nifty250 Scanner (Angel One + Streamlit + Telegram)

Swing (1D) aur Intraday scanner. Strategy aapki CSV wali hi hai: 5 conditions ka score
(EMA, RSI>55, MACD, 20-bar breakout, Volume spike) — **BUY = score 4+**, WATCH = 3.
SL = 1.5×ATR, Target = 1:2 (Settings me badal sakte ho).

## Features
- Angel One SmartAPI se live candles, stock list (Smallcap 250 / LargeMidcap 250 / Nifty 500, sector ke saath) auto-fetch
- ON/OFF filters: **ADX**, **Nifty regime** (Nifty > EMA50 & EMA20>EMA50), **Sector strength**
- Top 5 BUY ke chart (Support/Resistance, Entry/SL/Target) Telegram par
- Live auto-scan (5–30 min), CSV download
- Backtest (win rate, avg R, profit factor, drawdown, equity curve)
- Demo mode: Angel login ke bina app test karo

## Mobile se setup (sab phone par)
1. GitHub app/mobile browser → **New repository** (Private rakho).
2. Repo me **Add file → Create new file** se in files ko ek-ek karke paste karo:
   `app.py`, `core.py`, `run_scan.py`, `requirements.txt`, `universe.csv`,
   `.github/workflows/scan.yml` (file name me slash likhne se folder ban jata hai).
3. share.streamlit.io par GitHub se login → **Create app** → repo chuno → Main file: `app.py`.
4. App → **Settings → Secrets** me daalo:
   ```toml
   ANGEL_API_KEY = "..."
   ANGEL_CLIENT_ID = "..."
   ANGEL_PIN = "..."
   ANGEL_TOTP_SECRET = "..."   # Angel ke TOTP QR ke neeche wala text key
   TELEGRAM_BOT_TOKEN = "..."  # @BotFather se
   TELEGRAM_CHAT_ID = "..."    # @userinfobot se apna id; bot ko pehle /start bhejo
   ```
5. Same keys GitHub repo → Settings → Secrets and variables → Actions me daalo
   (tab Actions bina app khole Telegram alert bhejega: swing 3:20 PM, intraday har 30 min).

## Dhyan rakhne wali baatein
- **Keys kabhi code/CSV me commit mat karo.**
- Angel `getCandleData` ~3 requests/sec allow karta hai, isliye 250 stocks ka scan ~1.5–2 min leta hai.
  Isi wajah se auto-scan ka minimum 5 min rakha hai.
- Historical data ke liye SmartAPI portal me Historical API wali key chahiye ho sakti hai.
- Streamlit free app inactive hone par sleep ho jata hai — 24×7 alerts ke liye GitHub Actions use karo.
- Live candle adhoora hota hai; intraday me sirf **complete candles** use hote hain.
- Backtest history par hai, future ki guarantee nahi. Ye educational tool hai, investment advice nahi.
