# Markets

A small exporter that pulls prices from Yahoo Finance's free chart API every 5 minutes and serves them to Prometheus,
for Grafana's **Homelab / Markets** dashboard (`../grafana-dashboards/markets.json`). Plain Python, standard library only,
running from a ConfigMap on the stock `python` image: nothing to build. Argo CD deploys it from `main`
(`../argocd/applications/markets.yaml`).

## What it tracks

[`symbols.json`](symbols.json): MSCI World (IWDA, EUR), S&P 500, Nasdaq 100, Emerging Markets (EIMI), ten big tech
companies (Apple, Microsoft, Nvidia, Alphabet, Amazon, Meta, Broadcom, TSMC, AMD, Intel), Bitcoin and
Ethereum (EUR), gold (USD/oz), EUR/USD and the VIX. To add one, find its symbol on finance.yahoo.com (the bit in the
URL, e.g. `IWDA.AS`), add a line, and push: the ConfigMap's hash suffix restarts the pod.

## Metrics

| Metric | Meaning |
|---|---|
| `market_price` | Latest price, in the instrument's currency (label `currency`) |
| `market_change_percent{period}` | Change over `1d`, `1w`, `1m`, `3m`, `6m`, `ytd`, `1y`, `3y`, `5y` |
| `market_52w_high`, `market_52w_low`, `market_52w_position_percent` | 52-week range, and where the price sits in it (0-100) |
| `market_5y_high`, `market_drawdown_percent` | Highest close in 5 years, and the distance below it |
| `market_sma_distance_percent{window}` | Distance from the 50- and 200-day moving averages |
| `market_volatility_30d_percent` | Annualized volatility of the last 30 daily returns |
| `market_quote_timestamp_seconds`, `market_fetch_success` | Freshness and health |

All series carry `symbol`, `name` and `group`.

## Limits

- **Long-term numbers come from Yahoo, not Prometheus.** Prometheus keeps 10 days with no disk, so the exporter computes
  YTD, 1y, 5y, drawdown and averages itself from 10 years of daily closes. The dashboard's price charts only show what was
  collected here (10 days at most, and gone after a Prometheus restart).
- **Yahoo's API is unofficial.** It needs no key, but it can change or rate-limit without notice. If
  `market_fetch_success` drops to 0, check `kubectl -n markets logs deploy/markets-exporter`. The last good values are
  kept until a fetch works again.
- **Prices are delayed** about 15 minutes for some exchanges, and don't move outside market hours (crypto trades 24/7).
- **Price changes, not total return:** dividends aren't added back (IWDA and EIMI reinvest theirs, so they're unaffected).
- **Today's change** uses Yahoo's own previous close, so it matches finance.yahoo.com. For 24/7 markets (crypto, FX) that
  reference is Yahoo's, not a real market close.
- **Cluster DNS drops the odd lookup** (cross-node), so each symbol gets 3 attempts and the pod uses `ndots:1`.
- **"5-year high"** is the highest daily close in the last 5 years, not the all-time high.
- Not investment advice, just numbers.
