"""Markets exporter: pulls prices from Yahoo Finance's free chart API and serves them to Prometheus.

Standard library only (no pip install). Every FETCH_SECONDS it downloads 10 years of daily closes per symbol, computes the
long-term numbers itself (Prometheus here keeps only 10 days), and serves the latest snapshot on :9101/metrics.
Symbols come from symbols.json: [{"symbol": "IWDA.AS", "name": "MSCI World", "group": "Indexes"}, ...].
"""

import datetime as dt
import json
import math
import os
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

SYMBOLS_FILE = os.environ.get("SYMBOLS_FILE", "/config/symbols.json")
FETCH_SECONDS = int(os.environ.get("FETCH_SECONDS", "300"))
PORT = int(os.environ.get("PORT", "9101"))
# 10 years, so the 5-year change always has a close on or before its start date.
URL = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=10y&interval=1d"
# Today's change uses Yahoo's own previous close (only in the 1-day view): the daily bars of 24/7 markets (crypto, FX)
# don't line up with a "previous close", and for exchange-traded symbols both give the same number.
URL_1D = "https://query1.finance.yahoo.com/v8/finance/chart/{}?range=1d&interval=1d"
HEADERS = {"User-Agent": "Mozilla/5.0 (homelab-markets-exporter)"}  # Yahoo rejects Python's default agent

# Look-back periods, in calendar days. "ytd" is handled separately.
PERIODS = {"1w": 7, "1m": 30, "3m": 91, "6m": 182, "1y": 365, "3y": 1095, "5y": 1826}

snapshot = "# no data yet\n"
lock = threading.Lock()


def get_chart(url, symbol):
    req = urllib.request.Request(url.format(urllib.parse.quote(symbol)), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)["chart"]["result"][0]


def fetch(symbol):
    result = get_chart(URL, symbol)
    meta = result["meta"]
    closes = result["indicators"]["quote"][0]["close"]
    tz = dt.timezone(dt.timedelta(seconds=meta.get("gmtoffset", 0)))
    days = [(dt.datetime.fromtimestamp(t, tz).date(), c) for t, c in zip(result["timestamp"], closes) if c is not None]
    day = get_chart(URL_1D, symbol)["meta"]
    meta["previousClose"] = day.get("previousClose") or day.get("chartPreviousClose")  # FX and crypto use the latter
    return meta, days


def close_on_or_before(days, day):
    """Last daily close on or before `day` (the list is oldest first)."""
    best = None
    for d, c in days:
        if d > day:
            break
        best = c
    return best


def pct(now, then):
    return None if not then else (now / then - 1) * 100


def compute(meta, days):
    price = meta["regularMarketPrice"]
    quote_time = meta.get("regularMarketTime", time.time())
    tz = dt.timezone(dt.timedelta(seconds=meta.get("gmtoffset", 0)))
    today = dt.datetime.fromtimestamp(quote_time, tz).date()

    # History up to yesterday; today's bar (if any) is replaced by the live price.
    past = [(d, c) for d, c in days if d < today]
    series = [c for _, c in past] + [price]

    out = {"price": price, "quote_time": quote_time}
    changes = {"1d": pct(price, meta.get("previousClose") or (past[-1][1] if past else None))}
    for name, n in PERIODS.items():
        changes[name] = pct(price, close_on_or_before(past, today - dt.timedelta(days=n)))
    changes["ytd"] = pct(price, close_on_or_before(past, dt.date(today.year - 1, 12, 31)))
    out["changes"] = changes

    out["high_52w"] = meta.get("fiftyTwoWeekHigh")
    out["low_52w"] = meta.get("fiftyTwoWeekLow")
    if out["high_52w"] and out["low_52w"] and out["high_52w"] > out["low_52w"]:
        out["position_52w"] = (price - out["low_52w"]) / (out["high_52w"] - out["low_52w"]) * 100

    five_years_ago = today - dt.timedelta(days=PERIODS["5y"])
    high_5y = max([c for d, c in past if d >= five_years_ago] + [price])
    out["high_5y"] = high_5y
    out["drawdown"] = (price / high_5y - 1) * 100

    for window in (50, 200):
        if len(series) >= window:
            out[f"sma{window}"] = pct(price, sum(series[-window:]) / window)

    # Annualized volatility of the last 30 daily returns. Crypto trades every day, the rest ~252 days a year.
    returns = [math.log(b / a) for a, b in zip(series[-31:], series[-30:]) if a and b]
    if len(returns) >= 10:
        mean = sum(returns) / len(returns)
        sd = math.sqrt(sum((r - mean) ** 2 for r in returns) / (len(returns) - 1))
        per_year = 365 if meta.get("instrumentType") == "CRYPTOCURRENCY" else 252
        out["volatility_30d"] = sd * math.sqrt(per_year) * 100
    return out


def esc(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"')


def render(rows, ok, took):
    lines = []

    def metric(name, help_text, samples):
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} gauge")
        for labels, value in samples:
            if value is None:
                continue
            lab = ",".join(f'{k}="{esc(v)}"' for k, v in labels.items())
            lines.append(f"{name}{{{lab}}} {value}")

    base = lambda s: {"symbol": s["symbol"], "name": s["name"], "group": s["group"], "currency": s.get("currency", "")}
    metric("market_price", "Latest price, in the instrument's currency.", [(base(s), d["price"]) for s, d in rows])
    metric("market_change_percent", "Price change over a period, percent.",
           [({**base(s), "period": p}, v) for s, d in rows for p, v in d["changes"].items()])
    metric("market_52w_high", "52-week high.", [(base(s), d.get("high_52w")) for s, d in rows])
    metric("market_52w_low", "52-week low.", [(base(s), d.get("low_52w")) for s, d in rows])
    metric("market_52w_position_percent", "Where the price sits in its 52-week range: 0 = at the low, 100 = at the high.",
           [(base(s), d.get("position_52w")) for s, d in rows])
    metric("market_5y_high", "Highest daily close in the last 5 years.", [(base(s), d["high_5y"]) for s, d in rows])
    metric("market_drawdown_percent", "Distance from the 5-year high, percent (0 = at the high).",
           [(base(s), d["drawdown"]) for s, d in rows])
    metric("market_sma_distance_percent", "Distance from the simple moving average, percent (window in days).",
           [({**base(s), "window": w}, d.get(f"sma{w}")) for s, d in rows for w in (50, 200)])
    metric("market_volatility_30d_percent", "Annualized volatility of the last 30 daily returns, percent.",
           [(base(s), d.get("volatility_30d")) for s, d in rows])
    metric("market_quote_timestamp_seconds", "When the latest price was quoted (Unix time).",
           [(base(s), d["quote_time"]) for s, d in rows])
    metric("market_fetch_success", "1 if the last fetch of this symbol worked.",
           [({"symbol": sym}, v) for sym, v in ok.items()])
    metric("market_fetch_duration_seconds", "How long the last round of fetches took.", [({}, took)])
    return "\n".join(lines) + "\n"


def loop():
    global snapshot
    last = {}  # keep the previous good values when a fetch fails
    while True:
        with open(SYMBOLS_FILE) as f:
            symbols = json.load(f)
        start, ok = time.time(), {}
        for s in symbols:
            ok[s["symbol"]] = 0
            for attempt in range(1, 4):  # cluster DNS drops the odd packet; don't lose a symbol for 5 minutes over it
                try:
                    meta, days = fetch(s["symbol"])
                    last[s["symbol"]] = ({**s, "currency": meta.get("currency", "")}, compute(meta, days))
                    ok[s["symbol"]] = 1
                    break
                except Exception as e:  # one bad symbol mustn't stop the rest
                    print(f"{s['symbol']} (attempt {attempt}/3): {e}", flush=True)
                    time.sleep(5 * attempt)
            time.sleep(2)  # be gentle with Yahoo
        rows = [last[s["symbol"]] for s in symbols if s["symbol"] in last]
        with lock:
            snapshot = render(rows, ok, time.time() - start)
        print(f"fetched {sum(ok.values())}/{len(symbols)} symbols", flush=True)
        time.sleep(FETCH_SECONDS)


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/metrics", "/healthz"):
            self.send_error(404)
            return
        with lock:
            body = (snapshot if self.path == "/metrics" else "ok\n").encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; version=0.0.4")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    ThreadingHTTPServer(("", PORT), Handler).serve_forever()
