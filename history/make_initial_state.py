"""One-time: turn the calibrated 2007-2026 history into data/state.json, the update pipeline's starting point.

Inputs (this folder): clock_series.json (from calibrate_2007_2026.py), yahoo_monthly_ppm.json, cpiai.csv,
plus fund facts read from Vanguard's BND page and the U.S. Treasury on Oct 21, 2026 (typed in below).
Run from the repository root: python history/make_initial_state.py
"""
import csv
import json
import math
from pathlib import Path

H = Path(__file__).resolve().parent
ROOT = H.parent
S = json.load(open(H / 'clock_series.json'))
Y = json.load(open(H / 'yahoo_monthly_ppm.json'))
n = S['n']                      # 233 months, June 2007 .. October 2026 (October through Oct 21)
TICKERS = ('BND', 'VTI', 'SPY', 'BIL')  # BIL was replaced by fee-free T-bills on Oct 22, 2026 (history/tbill_from_treasury.py)

cpi = {}
for row in csv.DictReader(open(H / 'cpiai.csv')):
    if row['Date'] >= '2006-01':
        cpi[row['Date'][:7]] = float(row['Index'])
cpi['2026-09'] = 336.940                                   # BLS, released October 2026
cpi['2025-10'] = math.sqrt(cpi['2025-09'] * cpi['2025-11'])   # never published (2025 shutdown)

closes = [c / 100 for c in Y['BND_close_cents_2007-05_to_2026-10']]   # 2007-05 .. 2026-10 (Oct 21)
NA = {2007: 1095, 2008: 2946, 2009: 6242, 2010: 9048, 2011: 14596, 2012: 17968, 2013: 17637, 2014: 26041,
      2015: 27279, 2016: 31445, 2017: 37247, 2018: 36528, 2019: 48456, 2020: 68245, 2021: 84254, 2022: 84879,
      2023: 104824, 2024: 121366, 2025: 146392}
NAV = {2007: 76.61, 2008: 76.93, 2009: 78.31, 2010: 80.21, 2011: 83.31, 2012: 83.92, 2013: 79.91, 2014: 82.33,
       2015: 80.58, 2016: 80.64, 2017: 81.46, 2018: 79.16, 2019: 83.71, 2020: 88.03, 2021: 84.77, 2022: 71.83,
       2023: 73.57, 2024: 71.90, 2025: 74.07}
anchors = [[f'{y}-12', NA[y], NAV[y]] for y in sorted(NA)] + [['2026-09', 161400, 70.15]]

dists = [['2025-04-01', 0.240374], ['2025-05-01', 0.236371], ['2025-06-02', 0.240315], ['2025-07-01', 0.235347],
         ['2025-08-01', 0.241866], ['2025-09-02', 0.242759], ['2025-10-01', 0.237328], ['2025-11-03', 0.243631],
         ['2025-12-01', 0.238572], ['2025-12-18', 0.246638], ['2026-02-02', 0.245470], ['2026-03-02', 0.227824],
         ['2026-04-01', 0.250016], ['2026-05-01', 0.241713], ['2026-06-01', 0.247259], ['2026-07-01', 0.244470],
         ['2026-08-03', 0.251550], ['2026-09-01', 0.252886], ['2026-10-01', 0.248502]]

level_now = 1.0
for v in S['r']['BND']:
    level_now *= 1 + v / 1e6
TR_FROM_PEAK = -0.041300895982886554     # Aug 6, 2020 close to Oct 21, 2026 close, distributions reinvested

state = {
    'version': 1,
    'start': '2007-06',
    'inception': '2007-04-03',
    'returns': {'final_through': '2026-09', **{t: S['r'][t][:n - 1] for t in TICKERS}},
    'provisional': {'month': '2026-10', 'asOf': '2026-10-21', **{t: S['r'][t][n - 1] for t in TICKERS},
                    'bndClose': closes[-1]},
    'bnd_close': {'start': '2007-05', 'values': closes[:-1]},
    'cpi': dict(sorted(cpi.items())),
    'cpi_interpolated': ['2025-10'],
    'assets': {'anchors': anchors},
    'dists': dists,
    'fund': {
        'tna': 161400.0, 'tnaDate': '2026-09-30', 'fundTna': 389300.0,
        'duration': 5.7, 'avgMaturity': 8.3, 'avgCoupon': 3.9, 'ytm': 5.6, 'bonds': 11434, 'portfolioDate': '2026-09-30',
        'secYield': 5.11, 'secYieldDate': '2026-10-19', 'nav': 70.72, 'navDate': '2026-10-21',
        'unrealizedPct': -9.98, 'unrealizedPerShare': -7.00, 'unrealizedDate': '2026-09-30',
        'expenseRatio': 0.03, 'peerExpenseRatio': 0.53,
        'high52': 75.23, 'high52Date': '2026-02-27', 'low52': 69.652, 'low52Date': '2026-10-01',
        'rangeBasis': 'intraday prices',
    },
    'rates': {'tbill': 4.25, 'tbillLabel': '3-month Treasury yield', 'ust10': 5.13, 'date': '2026-10-21',
              'source': 'U.S. Treasury daily par yield curve'},
    'peak': {'date': '2020-08-06', 'level': level_now / (1 + TR_FROM_PEAK),
             'closestDate': '2026-02-27', 'closestGap': -0.008534617699184732,
             'priceHigh': 89.48, 'priceHighDate': '2020-08-06'},
    'priceSource': 'Yahoo Finance',
}
assert len(state['bnd_close']['values']) == len(state['returns']['BND']) + 1
(ROOT / 'data' / 'state.json').write_text(json.dumps(state, indent=1) + '\n', encoding='utf-8')
print('wrote data/state.json')
