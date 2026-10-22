# BND Opportunity Cost Clock

A real-time clock, in the style of usdebtclock.org, showing what holders of the Vanguard Total Bond
Market ETF (BND) have given up compared with US stocks (VTI), the S&P 500 (VOO), Nvidia stock (NVDA), 3-month T-bills and
inflation (CPI).

**Live site:** https://delightfullygrateful.com

## How it stays current

| What | When | Where it comes from |
|---|---|---|
| Prices, distributions and splits for BND, VTI, VOO, NVDA | Every weekday after the close | Tiingo (Yahoo Finance as backup) |
| 3-month and 10-year Treasury yields (the 3-month yield is also the T-bill comparison) | Every weekday | U.S. Treasury daily par yield curve |
| CPI | When the BLS publishes it (mid-month) | BLS API |
| BND net assets, SEC yield, duration, unrealized gains | When Vanguard's site allows automated reads | Vanguard fund page (best effort) |
| Year-end net assets, 2007 to 2025 | Already in the data | Vanguard annual reports (SEC Form N-CSR) |

1. **GitHub Actions** (`.github/workflows/update.yml`) runs `scripts/update_data.py` at about
   7:30 pm ET on weekdays and again the next morning. It fetches new data, turns finished months
   into permanent history, rebuilds `public/index.html`, checks the page's math with
   `tests/test_engine.js`, and commits the result.
2. **Cloudflare** (the `bnd-opportunity-cost-clock` Worker, configured by `wrangler.jsonc`)
   redeploys `public/` every time `main` changes, within a minute or two.
3. Between updates, every counter on the page ticks forward on its own, the way a debt clock runs
   between Treasury reports.

If an update fails, nothing on the site changes. The job is marked failed on the Actions tab, and
the reason is written to `data/status.json`. The page itself warns visitors when its data is more
than five days old.

## Secrets (Settings → Secrets and variables → Actions)

- `TIINGO_API_KEY`: required for Tiingo prices. Without it the job falls back to Yahoo Finance.
- `BLS_API_KEY`: optional; raises the BLS request limit.

## Repository layout

| Path | What it is |
|---|---|
| `public/index.html` | The site: one self-contained page (generated, don't edit by hand) |
| `src/template.html` | The page's HTML, CSS and JavaScript; `__CLOCK_DATA__` is replaced with the data |
| `scripts/update_data.py` | Fetches data, updates `data/state.json`, rebuilds the page |
| `data/state.json` | The running record: monthly returns since June 2007, net-asset anchors, CPI, fund facts |
| `data/clock-data.json` | The data embedded in the page |
| `data/check.json` | Values the page's JavaScript must reproduce |
| `data/status.json` | What the last update did, and any problems |
| `tests/` | Engine check and simulated update runs (month and year rollovers, outages) |
| `history/` | How the 2007 to September 2026 history was built and calibrated (one-time) |
| `wrangler.jsonc` | Cloudflare Worker settings: serve `public/` |

## Working on it locally

```
python scripts/update_data.py --offline   # rebuild the page from data/state.json, no network
TIINGO_API_KEY=... python scripts/update_data.py   # full update
node tests/test_engine.js                 # check the page's calculations
python tests/simulate_updates.py          # simulated update runs
```

To change the design or wording, edit `src/template.html` and run the offline rebuild. Pushing
changes to `src/`, `scripts/` or `tests/` also triggers a full update run.

## Keeping Vanguard figures fresh

Vanguard's site often blocks automated requests. When it does, the clock keeps the last values,
with their dates shown on the page, and estimates net assets from the recent pace of new shares.
To add a month-end net-asset figure by hand, append `["YYYY-MM", net assets in $ millions,
month-end price]` to `assets.anchors` in `data/state.json` (the month must already be complete),
and update the matching fields in `fund` (`tna`, `tnaDate`, `secYield`, `secYieldDate`, and so on).
Or ask Claude to refresh them.

## Method in brief

- Monthly total returns with distributions reinvested. History through September 2026 comes from
  Yahoo Finance, with BND and VTI calibrated to Vanguard's official quarterly and annual returns
  (their 1-, 3-, 5- and 10-year returns agree with Vanguard's to within 0.01 percentage point).
  Later months come from daily
  closes and distributions.
- T-bills are fee-free 3-month Treasury bills, as bought at TreasuryDirect and rolled over: each
  calendar day earns 1/365 of the latest 3-month Treasury yield. (Until October 2026 the clock used
  the BIL ETF, whose fee and shorter maturity made it trail the bills by 0.2 to 0.35 points a year.
  `history/tbill_from_treasury.py` rebuilds the history from the Treasury's files.)
- Nvidia's returns include its dividends, reinvested, and its stock splits (3-for-2 in 2007,
  4-for-1 in 2021, 10-for-1 in 2024). History to September 2026 is from Yahoo Finance daily closes.
- The S&P 500 is VOO (0.03% fee) from October 2010. VOO launched Sept 7, 2010, so June 2007 to
  September 2010 use SPY (0.0945%), which tracks the same index; the page marks figures that include
  those months with an asterisk.
- BND, VTI and VOO returns are after their funds' expense ratios, since fees come out of the
  funds' assets and so out of their prices.
- Opportunity cost for a month = dollars at work in BND × (alternative's return − BND's return),
  where dollars at work = previous month-end net assets + half of the month's new money. The
  clock adds the monthly amounts without compounding them.
- Between updates the counters project forward using the trailing return gap chosen on the page
  (1, 3, 5 or 10 years, or since 2007) and the recent pace of new money into BND.

## Limits

This is a hindsight comparison that ignores risk. In 2008 US stocks fell 37% while BND gained 6.9%,
and people hold bonds for stability, income and diversification. Taxes, trading costs and personal
timing also differ. An independent project built from public data, not affiliated with or
endorsed by Vanguard, State Street, Tiingo, Yahoo or the U.S. government. Not investment advice.
