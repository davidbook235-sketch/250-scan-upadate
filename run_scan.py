"""Headless scan for GitHub Actions: scan -> Telegram top-5 BUY charts."""
import os
import sys

import core

mode = os.environ.get("SCAN_MODE", "swing")
interval = os.environ.get("INTRADAY_INTERVAL", "FIFTEEN_MINUTE")
g = core.get_secret

if mode == "intraday" and not core.market_open():
    print("Market band hai, intraday scan skip.")
    sys.exit(0)

angel = core.Angel(g("ANGEL_API_KEY"), g("ANGEL_CLIENT_ID"), g("ANGEL_PIN"), g("ANGEL_TOTP_SECRET"))
res = core.run_scan(angel.candles, core.load_universe(), core.load_tokens(), mode, interval,
                    dict(core.DEFAULT_P), ttl=60)
print(f"{res['scanned']} scanned | regime {res['regime']['state']} | BUY: "
      f"{(res['table']['Signal'] == 'BUY').sum() if not res['table'].empty else 0}")
items = core.build_top5(res)
if items:
    core.tg_send_top5(g("TELEGRAM_BOT_TOKEN"), g("TELEGRAM_CHAT_ID"), res, items)
    print("Telegram bheja:", [i["symbol"] for i in items])
else:
    print("Koi BUY signal nahi.")
