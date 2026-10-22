"""Build the calibrated dataset for the BND Opportunity Cost Clock.

Inputs: yahoo_monthly_ppm.json (Yahoo adjusted-close monthly returns), cpiai.csv (BLS CPI-U NSA),
plus official Vanguard figures typed in below (from investor.vanguard.com fund API and SEC N-CSR filings).
Output: clock_series.json (the calibrated series) and a validation printout. Run from history/.
"""
import json, math, csv, datetime as dt

D = json.load(open('yahoo_monthly_ppm.json'))
N = 233
months = []
y, m = 2007, 6
for _ in range(N):
    months.append(f"{y}-{m:02d}")
    m += 1
    if m == 13:
        m, y = 1, y + 1
assert months[-1] == '2026-10'
idx = {k: i for i, k in enumerate(months)}

def g(series):  # growth factors
    return [1 + x / 1e6 for x in series]

raw = {k: g(D[k]) for k in ['BND', 'VTI', 'SPY', 'BIL']}

# ---------------- Official Vanguard market-price returns (percent) ----------------
BND_ANNUAL = {2011: 7.86, 2012: 3.99, 2013: -2.11, 2014: 5.87, 2015: 0.49}
BND_Q = {
    2016: [2.98, 2.46, 0.35, -3.16], 2017: [0.78, 1.54, 0.80, 0.38], 2018: [-1.57, -0.21, 0.06, 1.67],
    2019: [2.98, 3.06, 2.34, 0.20], 2020: [2.99, 3.34, 0.40, 0.78], 2021: [-3.62, 1.94, -0.03, -0.07],
    2022: [-5.85, -4.77, -4.65, 1.62], 2023: [3.28, -0.83, -3.22, 6.64], 2024: [-0.72, 0.12, 5.20, -3.07],
    2025: [2.78, 1.23, 2.00, 0.90], 2026: [0.05, 0.70, -3.45],
}
BND_YTD_OCT21 = -1.54
VTI_ANNUAL = {2011: 1.00, 2012: 16.45, 2013: 33.48, 2014: 12.57, 2015: 0.37}
VTI_Q = {
    2016: [0.97, 2.68, 4.38, 4.19], 2017: [5.71, 3.07, 4.53, 6.38], 2018: [-0.61, 3.90, 7.06, -14.25],
    2019: [14.04, 4.08, 1.13, 8.97], 2020: [-20.85, 22.06, 9.22, 14.72], 2021: [6.42, 8.24, -0.08, 9.15],
    2022: [-5.37, -16.89, -4.37, 7.03], 2023: [7.16, 8.42, -3.31, 12.27], 2024: [9.93, 3.24, 6.17, 2.68],
    2025: [-4.83, 10.92, 8.27, 2.45], 2026: [-4.01, 15.68, 1.39],
}
VTI_YTD_OCT21 = 14.06


def calibrate(f, annual, quarterly, ytd_oct21, special=None):
    f = list(f)
    for yr, pct in annual.items():
        ii = [idx[f"{yr}-{mm:02d}"] for mm in range(1, 13)]
        prod = math.prod(f[i] for i in ii)
        k = (1 + pct / 100) / prod
        if special and yr in special:
            for mm, w in special[yr].items():
                f[idx[f"{yr}-{mm:02d}"]] *= k ** w
        else:
            for i in ii:
                f[i] *= k ** (1 / 12)
    for yr, qs in quarterly.items():
        for qn, pct in enumerate(qs):
            ii = [idx[f"{yr}-{mm:02d}"] for mm in range(qn * 3 + 1, qn * 3 + 4)]
            prod = math.prod(f[i] for i in ii)
            k = (1 + pct / 100) / prod
            for i in ii:
                f[i] *= k ** (1 / 3)
    # October 2026 month-to-date so that YTD matches the official figure
    jan_sep = math.prod(f[idx[f"2026-{mm:02d}"]] for mm in range(1, 10))
    f[idx['2026-10']] = (1 + ytd_oct21 / 100) / jan_sep
    return f

# 2012: Yahoo is missing the Nov and late-Dec 2012 distributions -> put the fix in those months
cal = {
    'BND': calibrate(raw['BND'], BND_ANNUAL, BND_Q, BND_YTD_OCT21, special={2012: {11: 0.2, 12: 0.8}}),
    'VTI': calibrate(raw['VTI'], VTI_ANNUAL, VTI_Q, VTI_YTD_OCT21),
    'SPY': raw['SPY'],
    'BIL': raw['BIL'],
}

# ---------------- CPI-U (NSA) monthly inflation ----------------
cpi = {}
for row in csv.DictReader(open('cpiai.csv')):
    cpi[row['Date'][:7]] = float(row['Index'])
cpi['2026-09'] = 336.940  # BLS API, released Oct 2026
cpi['2025-10'] = math.sqrt(cpi['2025-09'] * cpi['2025-11'])  # not published (2025 shutdown) -> geometric interpolation
cpi_f = []
for k in months:
    if k == '2026-10':
        yoy = cpi['2026-09'] / cpi['2025-09']
        cpi_f.append(yoy ** (1 / 12 * 21 / 31))  # estimate: Oct 1-21 at trailing 12-month pace
    else:
        y_, m_ = map(int, k.split('-'))
        prev = f"{y_ if m_ > 1 else y_ - 1}-{(m_ - 1 if m_ > 1 else 12):02d}"
        cpi_f.append(cpi[k] / cpi[prev])
cal['CPI'] = cpi_f

# ---------------- BND (ETF share class) net assets: official anchors ----------------
# Net Assets, End of Period ($M) and NAV, End of Period, from Vanguard Bond Index Funds N-CSR filings (SEC EDGAR)
NA = {2007: 1095, 2008: 2946, 2009: 6242, 2010: 9048, 2011: 14596, 2012: 17968, 2013: 17637, 2014: 26041,
      2015: 27279, 2016: 31445, 2017: 37247, 2018: 36528, 2019: 48456, 2020: 68245, 2021: 84254, 2022: 84879,
      2023: 104824, 2024: 121366, 2025: 146392}
NAV = {2007: 76.61, 2008: 76.93, 2009: 78.31, 2010: 80.21, 2011: 83.31, 2012: 83.92, 2013: 79.91, 2014: 82.33,
       2015: 80.58, 2016: 80.64, 2017: 81.46, 2018: 79.16, 2019: 83.71, 2020: 88.03, 2021: 84.77, 2022: 71.83,
       2023: 73.57, 2024: 71.90, 2025: 74.07}
TNA_SEP2026 = 161400  # $161.4 billion, Vanguard, as of 2026-09-30
closes = [c / 100 for c in D['BND_close_cents_2007-05_to_2026-10']]  # 2007-05 .. 2026-10 (234)
close_by = {('2007-05' if i == 0 else months[i - 1]): c for i, c in enumerate(closes)}

# shares outstanding anchors (millions) at month index positions (month-end labels)
anchor = {}
for yr in NA:
    anchor[f"{yr}-12"] = NA[yr] / NAV[yr]
anchor['2026-09'] = TNA_SEP2026 / close_by['2026-09']
all_months = ['2007-05'] + months
pos = {k: i for i, k in enumerate(all_months)}
shares = [None] * len(all_months)
# 2007: linear from 0 at inception (Apr 3, 2007) to Dec 2007 anchor
inc = dt.date(2007, 4, 3)
dec07 = dt.date(2007, 12, 31)
def month_end(k):
    y_, m_ = map(int, k.split('-'))
    nxt = dt.date(y_ + (m_ == 12), m_ % 12 + 1, 1)
    return nxt - dt.timedelta(days=1)
for k in all_months[:pos['2007-12'] + 1]:
    shares[pos[k]] = anchor['2007-12'] * (month_end(k) - inc).days / (dec07 - inc).days
keys = sorted(anchor, key=lambda k: pos[k])
for a, b in zip(keys, keys[1:]):
    ia, ib = pos[a], pos[b]
    for i in range(ia, ib + 1):
        t = (i - ia) / (ib - ia)
        shares[i] = math.exp(math.log(anchor[a]) * (1 - t) + math.log(anchor[b]) * t)
# after Sep 2026: continue at 2026 year-to-date pace (shares/month)
pace = (anchor['2026-09'] - anchor['2025-12']) / 9
shares[pos['2026-10']] = anchor['2026-09'] + pace * (21 / 31)
aum = []
for k in all_months:
    i = pos[k]
    yr = int(k[:4])
    if k.endswith('-12') and yr in NA:
        aum.append(NA[yr])
    elif k == '2026-09':
        aum.append(TNA_SEP2026)
    else:
        aum.append(shares[i] * close_by[k])

# ---------------- Aggregate opportunity cost ----------------
def invested(i):  # month i in `months` (0-based); capital at work during that month ($M)
    a0 = aum[i]       # end of previous month (all_months index i)
    a1 = aum[i + 1]
    flows = a1 - a0 * cal['BND'][i]
    return a0 + 0.5 * flows

def agg(alt, i0=0, i1=N):
    return sum(invested(i) * (cal[alt][i] - cal['BND'][i]) for i in range(i0, i1))

def cum(series, i0, i1):
    return math.prod(series[i0:i1])

def ann(series, i0, i1):
    n = i1 - i0
    return cum(series, i0, i1) ** (12 / n) - 1

print('=== Validation vs official Vanguard figures (as of 2026-09-30, market price) ===')
i_sep26 = idx['2026-09'] + 1  # exclusive end index through Sep 2026
for label, nm, off in [('1Y', 12, -1.85), ('3Y', 36, 4.03), ('5Y', 60, -0.63), ('10Y', 120, 1.12)]:
    print(f"BND {label}: computed {ann(cal['BND'], i_sep26 - nm, i_sep26) * 100:6.2f}%  official {off:6.2f}%")
for label, nm, off in [('1Y', 12, 15.35), ('3Y', 36, 22.34), ('5Y', 60, 12.56), ('10Y', 120, 14.70)]:
    print(f"VTI {label}: computed {ann(cal['VTI'], i_sep26 - nm, i_sep26) * 100:6.2f}%  official {off:6.2f}%")
si = cum(cal['BND'], 0, i_sep26)
print(f"BND cumulative May-31-2007 -> Sep-30-2026: {(si - 1) * 100:.2f}%  official since inception 73.07% "
      f"(=> implied {((1.7307 / 0.995427) - 1) * 100:.2f}% from May 31, 2007)")
for yr in [2008, 2009, 2010, 2017, 2018, 2022, 2025]:
    ii = [idx[f"{yr}-{mm:02d}"] for mm in range(1, 13)]
    print(yr, 'BND', round((math.prod(cal['BND'][i] for i in ii) - 1) * 100, 2), 'VTI',
          round((math.prod(cal['VTI'][i] for i in ii) - 1) * 100, 2), 'SPY',
          round((math.prod(cal['SPY'][i] for i in ii) - 1) * 100, 2), 'BIL',
          round((math.prod(cal['BIL'][i] for i in ii) - 1) * 100, 2), 'CPI',
          round((math.prod(cal['CPI'][i] for i in ii) - 1) * 100, 2))

print('\n=== AUM model ($B) ===')
for k in ['2007-05', '2007-12', '2010-12', '2015-12', '2020-07', '2020-12', '2022-12', '2025-12', '2026-09', '2026-10']:
    print(k, round(aum[pos[k]] / 1000, 2), 'shares(M)', round(shares[pos[k]], 1))

print('\n=== Aggregate opportunity cost since May 31, 2007 ($B) ===')
for alt in ['VTI', 'SPY', 'BIL', 'CPI']:
    tot = agg(alt)
    ytd = agg(alt, idx['2026-01'], N)
    y5 = agg(alt, N - 60, N)
    y10 = agg(alt, N - 120, N)
    print(f"{alt}: total {tot / 1000:9.2f}  YTD {ytd / 1000:8.2f}  last60m {y5 / 1000:8.2f}  last120m {y10 / 1000:8.2f}")

print('\n=== Per-year aggregate gap vs VTI ($B) ===')
for yr in range(2007, 2027):
    ii = [i for i, k in enumerate(months) if k.startswith(str(yr))]
    print(yr, round(sum(invested(i) * (cal['VTI'][i] - cal['BND'][i]) for i in ii) / 1000, 2),
          'BIL', round(sum(invested(i) * (cal['BIL'][i] - cal['BND'][i]) for i in ii) / 1000, 2),
          'CPI', round(sum(invested(i) * (cal['CPI'][i] - cal['BND'][i]) for i in ii) / 1000, 2))

# TR index peak analysis
tr = [1.0]
for x in cal['BND']:
    tr.append(tr[-1] * x)
real = [1.0]
for x, c in zip(cal['BND'], cal['CPI']):
    real.append(real[-1] * x / c)
pk = max(range(len(tr)), key=lambda i: tr[i])
print('\nNominal TR index peak at', all_months[pk], round(tr[pk], 4), 'now', round(tr[-1], 4), 'Jul-2020', round(tr[pos['2020-07']], 4))
rpk = max(range(len(real)), key=lambda i: real[i])
print('Real TR index peak at', all_months[rpk], round(real[rpk], 4), 'now', round(real[-1], 4), 'drawdown', round((real[-1] / real[rpk] - 1) * 100, 2))
pre = max(range(pos['2021-12']), key=lambda i: tr[i])
print('Pre-2022 nominal peak', all_months[pre], 'recovered?', [all_months[i] for i in range(pre + 1, len(tr)) if tr[i] >= tr[pre]][:3])

out = {
    'start': '2007-06', 'n': N,
    'r': {k: [round((x - 1) * 1e6) for x in v] for k, v in cal.items()},
    'aum': [round(a, 1) for a in aum],
    'shares': [round(s, 2) for s in shares],
}
json.dump(out, open('clock_series.json', 'w'), separators=(',', ':'))
print('\nwrote clock_series.json', len(json.dumps(out, separators=(",", ":"))), 'bytes')
