#!/usr/bin/env python3
"""Refresh the data behind the BND Opportunity Cost Clock and rebuild public/index.html.

Each run:
  1. Loads data/state.json, the clock's running record: monthly total returns since June 2007,
     BND's net-asset anchors, CPI levels, fund facts, distributions and the 2020 peak.
  2. Fetches the latest daily prices and distributions for BND, VTI, SPY and BIL from Tiingo
     (falling back to Yahoo Finance), CPI from the BLS, Treasury yields from the U.S. Treasury,
     and fund facts from Vanguard (best effort; Vanguard often blocks automated requests).
  3. Turns completed months into permanent monthly returns, recomputes the current month so
     far, and updates the 2020-peak, 52-week and distribution figures.
  4. Writes data/clock-data.json (embedded in the page), data/check.json (values the page's
     JavaScript must reproduce) and data/status.json, then rebuilds public/index.html.

Nothing is written unless the whole run succeeds, except data/status.json, which records the error.

Environment: TIINGO_API_KEY (needed for Tiingo), BLS_API_KEY (optional).
Usage:  python scripts/update_data.py            fetch and rebuild
        python scripts/update_data.py --offline  rebuild from data/state.json without fetching
"""
from __future__ import annotations

import calendar
import csv
import datetime as dt
import io
import json
import math
import os
import sys
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / 'data'
ET = ZoneInfo('America/New_York')
TICKERS = ('BND', 'VTI', 'SPY', 'BIL')
ALTS = ('VTI', 'SPY', 'BIL', 'CPI')
START = '2007-06'            # first month of returns (BND began trading Apr 10, 2007)
UA = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0 Safari/537.36')
# A single month's total return outside these bounds is treated as bad data, not news.
BOUNDS = {'BND': (-0.12, 0.12), 'VTI': (-0.40, 0.40), 'SPY': (-0.40, 0.40), 'BIL': (-0.01, 0.02)}
SPYR = 365.25 * 86400        # seconds in an average year


# ---------------------------------------------------------------- small helpers
def ym(key: str) -> tuple[int, int]:
    return int(key[:4]), int(key[5:7])


def mkey(y: int, m: int) -> str:
    return f'{y:04d}-{m:02d}'


def add_months(key: str, n: int) -> str:
    y, m = ym(key)
    t = y * 12 + (m - 1) + n
    return mkey(t // 12, t % 12 + 1)


def month_diff(a: str, b: str) -> int:
    """Months from a to b."""
    (ya, ma), (yb, mb) = ym(a), ym(b)
    return (yb - ya) * 12 + (mb - ma)


def days_in_month(key: str) -> int:
    y, m = ym(key)
    return calendar.monthrange(y, m)[1]


def month_last_day(key: str) -> dt.date:
    y, m = ym(key)
    return dt.date(y, m, days_in_month(key))


def iso_date(s: str) -> dt.date:
    return dt.date.fromisoformat(s[:10])


def et_ms(d: dt.date, hour: int = 0) -> int:
    return int(dt.datetime(d.year, d.month, d.day, hour, tzinfo=ET).timestamp() * 1000)


def ix(key: str) -> int:
    """Index of a month in the return series (0 = June 2007)."""
    return month_diff(START, key)


def ppm(factor: float) -> int:
    return round((factor - 1) * 1e6)


def prod(values) -> float:
    p = 1.0
    for v in values:
        p *= v
    return p


def load_json(name: str, default=None):
    path = DATA / name
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(name: str, obj, compact: bool = False) -> None:
    text = json.dumps(obj, separators=(',', ':')) if compact else json.dumps(obj, indent=1)
    (DATA / name).write_text(text + '\n', encoding='utf-8')


# ---------------------------------------------------------------- fetching
def http_get(url: str, headers: dict | None = None, timeout: int = 40, retries: int = 2) -> bytes:
    hdrs = {'User-Agent': UA, 'Accept': '*/*'}
    hdrs.update(headers or {})
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers=hdrs)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            last = e
            if e.code in (400, 401, 403, 404):
                break
        except Exception as e:  # network errors, timeouts
            last = e
        if attempt < retries:
            time.sleep(4 * (attempt + 1))
    host = urllib.parse.urlsplit(url).netloc
    raise RuntimeError(f'{host}: {last}')


def fetch_tiingo(ticker: str, start: dt.date, end: dt.date, key: str) -> list[dict]:
    q = urllib.parse.urlencode({'startDate': start.isoformat(), 'endDate': end.isoformat(),
                                'format': 'json', 'resampleFreq': 'daily'})
    url = f'https://api.tiingo.com/tiingo/daily/{ticker}/prices?{q}'
    raw = json.loads(http_get(url, {'Authorization': f'Token {key}', 'Content-Type': 'application/json',
                                    'Accept': 'application/json'}))
    if not isinstance(raw, list) or not raw:
        raise RuntimeError(f'Tiingo {ticker}: unexpected response {str(raw)[:160]}')
    rows = []
    for r in raw:
        rows.append({'date': r['date'][:10], 'close': float(r['close']),
                     'div': float(r.get('divCash') or 0.0), 'split': float(r.get('splitFactor') or 1.0)})
    return rows


def fetch_yahoo(ticker: str, start: dt.date, end: dt.date) -> list[dict]:
    p1 = int(dt.datetime(start.year, start.month, start.day, tzinfo=dt.timezone.utc).timestamp())
    p2 = int(dt.datetime(end.year, end.month, end.day, tzinfo=dt.timezone.utc).timestamp()) + 2 * 86400
    url = (f'https://query1.finance.yahoo.com/v8/finance/chart/{ticker}'
           f'?period1={p1}&period2={p2}&interval=1d&events=div%7Csplit')
    res = json.loads(http_get(url, {'Accept': 'application/json'}))['chart']['result'][0]
    off = res['meta'].get('gmtoffset', -14400)

    def day(ts):
        return dt.datetime.fromtimestamp(ts + off, dt.timezone.utc).date().isoformat()

    events = res.get('events') or {}
    divs: dict[str, float] = {}
    for ev in (events.get('dividends') or {}).values():
        divs[day(ev['date'])] = divs.get(day(ev['date']), 0.0) + float(ev['amount'])
    splits = {day(ev['date']): float(ev['numerator']) / float(ev['denominator'])
              for ev in (events.get('splits') or {}).values()}
    rows = []
    for ts, c in zip(res['timestamp'], res['indicators']['quote'][0]['close']):
        if c is None:
            continue
        d = day(ts)
        rows.append({'date': d, 'close': float(c), 'div': divs.get(d, 0.0), 'split': splits.get(d, 1.0)})
    return rows


def fetch_prices(start: dt.date, now_et: dt.datetime, key: str, notes: list) -> tuple[dict, str]:
    errors = []
    sources = []
    if key:
        sources.append(('Tiingo', lambda t: fetch_tiingo(t, start, now_et.date(), key)))
    else:
        errors.append('Tiingo: TIINGO_API_KEY is not set')
    sources.append(('Yahoo Finance', lambda t: fetch_yahoo(t, start, now_et.date())))
    for name, fn in sources:
        try:
            data = {}
            for t in TICKERS:
                rows = sorted(fn(t), key=lambda r: r['date'])
                # Never treat a price from a session that hasn't closed yet as a close.
                if rows and rows[-1]['date'] == now_et.date().isoformat() and now_et.time() < dt.time(16, 30):
                    rows = rows[:-1]
                if len(rows) < 30:
                    raise RuntimeError(f'{t}: only {len(rows)} days of prices')
                data[t] = rows
            if errors:
                notes.append(f'Prices came from {name} because ' + '; '.join(errors))
            return data, name
        except Exception as e:
            errors.append(f'{name}: {e}')
    raise RuntimeError('Could not fetch prices. ' + ' | '.join(errors))


def fetch_cpi() -> dict[str, float]:
    key = os.environ.get('BLS_API_KEY', '').strip()
    base = 'https://api.bls.gov/publicAPI/v2/timeseries/data/'
    if key:
        this_year = dt.date.today().year
        body = json.dumps({'seriesid': ['CUUR0000SA0'], 'startyear': str(this_year - 3),
                           'endyear': str(this_year), 'registrationkey': key}).encode()
        req = urllib.request.Request(base, data=body, headers={'Content-Type': 'application/json', 'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=40) as resp:
            j = json.loads(resp.read())
    else:
        j = json.loads(http_get(base + 'CUUR0000SA0', {'Accept': 'application/json'}))
    if j.get('status') != 'REQUEST_SUCCEEDED':
        raise RuntimeError(f"BLS: {j.get('status')} {j.get('message')}")
    out = {}
    for d in j['Results']['series'][0]['data']:
        if not d['period'].startswith('M') or d['period'] == 'M13':
            continue
        try:
            out[mkey(int(d['year']), int(d['period'][1:]))] = float(d['value'])
        except ValueError:
            continue  # e.g. October 2025, never published
    if not out:
        raise RuntimeError('BLS: no CPI values in the response')
    return out


def fetch_treasury(today: dt.date) -> dict:
    for year in (today.year, today.year - 1):
        url = ('https://home.treasury.gov/resource-center/data-chart-center/interest-rates/'
               f'daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve'
               f'&field_tdr_date_value={year}&page&_format=csv')
        text = http_get(url, {'Accept': 'text/csv'}).decode('utf-8-sig')
        rows = [r for r in csv.DictReader(io.StringIO(text)) if r.get('Date')]
        if not rows:
            continue
        best = max(rows, key=lambda r: dt.datetime.strptime(r['Date'], '%m/%d/%Y'))
        d = dt.datetime.strptime(best['Date'], '%m/%d/%Y').date()
        return {'tbill': float(best['3 Mo']), 'tbillLabel': '3-month Treasury yield', 'ust10': float(best['10 Yr']),
                'date': d.isoformat(), 'source': 'U.S. Treasury daily par yield curve'}
    raise RuntimeError('Treasury: no rows')


def parse_money_millions(text: str) -> float:
    """'$161.4 billion' -> 161400.0 (millions)."""
    parts = text.replace('$', '').replace(',', '').split()
    value = float(parts[0])
    unit = parts[1].lower() if len(parts) > 1 else ''
    return value * {'trillion': 1e6, 'billion': 1e3, 'million': 1.0}.get(unit, 1.0)


def fetch_vanguard() -> tuple[dict, list]:
    """Best-effort read of Vanguard's public fund data. Returns (fields, errors)."""
    base = 'https://investor.vanguard.com/vmf/api/BND/'
    hdr = {'Accept': 'application/json, text/plain, */*', 'Accept-Language': 'en-US,en;q=0.9',
           'Referer': 'https://investor.vanguard.com/investment-products/etfs/profile/bnd'}
    out: dict = {}
    errors: list = []

    def get(endpoint):
        return json.loads(http_get(base + endpoint, hdr, retries=1))

    try:
        ch = get('characteristic')['fixedIncomeCharacteristic']['fund']
        out.update({
            'tna': parse_money_millions(ch['shareClassTotalNetAssets']),
            'tnaDate': ch['shareClassTotalNetAssetsDate'][:10],
            'fundTna': parse_money_millions(ch['totalNetAssets']),
            'duration': float(ch['averageDuration'].split()[0]),
            'avgMaturity': float(ch['averageMaturity'].split()[0]),
            'avgCoupon': float(ch['averageCoupon']),
            'ytm': float(ch['yieldToMaturity']),
            'bonds': int(ch['numberOfBonds']),
            'portfolioDate': ch['asOfDate'][:10],
        })
    except Exception as e:
        errors.append(f'characteristic: {e}')
    try:
        pr = get('price')['currentPrice']
        out.update({'secYield': float(pr['yield']['yieldPct']), 'secYieldDate': pr['yield']['asOfDate'][:10]})
        nav = pr['dailyPrice']['regular']
        out.update({'nav': float(nav['price']), 'navDate': nav['asOfDate'][:10]})
    except Exception as e:
        errors.append(f'price: {e}')
    try:
        ru = get('distribution')['realizedUnrealizedGain']
        out.update({'unrealizedPct': float(ru['unRealizedPctNAV']), 'unrealizedPerShare': float(ru['unRealized']),
                    'unrealizedDate': ru['asOfDate'][:10]})
    except Exception as e:
        errors.append(f'distribution: {e}')
    try:
        ex = get('expense')
        out.update({'expenseRatio': float(ex['expenseRatio']), 'peerExpenseRatio': float(ex['avgExpenseRatio'])})
    except Exception as e:
        errors.append(f'expense: {e}')
    return out, errors


# ---------------------------------------------------------------- price processing
def align(prices: dict) -> tuple[dict, str]:
    last = min(rows[-1]['date'] for rows in prices.values())
    return {t: [r for r in rows if r['date'] <= last] for t, rows in prices.items()}, last


def month_factors(rows: list[dict], final: str) -> tuple[dict, int]:
    """Total-return factors for each month after `final`, from daily closes and distributions.

    Returns ({month: [factor, last close, last date]}, index of the base row)."""
    cutoff = month_last_day(final)
    base = None
    for i, r in enumerate(rows):
        if r['date'] <= cutoff.isoformat():
            base = i
    if base is None:
        raise RuntimeError(f'price history does not reach back to {cutoff}')
    if (cutoff - iso_date(rows[base]['date'])).days > 6:
        raise RuntimeError(f'no close near {cutoff}; the latest before it is {rows[base]["date"]}')
    out: dict = {}
    for i in range(base + 1, len(rows)):
        r, p = rows[i], rows[i - 1]
        f = (r['close'] + r['div']) * r['split'] / p['close']
        rec = out.setdefault(r['date'][:7], [1.0, None, None])
        rec[0] *= f
        rec[1], rec[2] = r['close'], r['date']
    return out, base


def update_peak(state: dict, rows: list[dict], base: int) -> None:
    """Track the total-return peak and the closest approach to it, day by day."""
    pk = state['peak']
    level = prod(1 + v / 1e6 for v in state['returns']['BND'])   # index level at the base close
    for i in range(base + 1, len(rows)):
        r, p = rows[i], rows[i - 1]
        level *= (r['close'] + r['div']) * r['split'] / p['close']
        if level > pk['level'] * (1 + 1e-9):
            pk.update(level=level, date=r['date'], closestDate=None, closestGap=None)
        else:
            gap = level / pk['level'] - 1
            if pk.get('closestGap') is None or gap > pk['closestGap']:
                pk['closestDate'], pk['closestGap'] = r['date'], gap
    for r in rows:
        if r['close'] > pk['priceHigh']:
            pk['priceHigh'], pk['priceHighDate'] = r['close'], r['date']


def update_range(state: dict, rows: list[dict], asof: str) -> None:
    since = (iso_date(asof) - dt.timedelta(days=365)).isoformat()
    window = [r for r in rows if r['date'] > since]
    hi = max(window, key=lambda r: r['close'])
    lo = min(window, key=lambda r: r['close'])
    state['fund'].update(high52=hi['close'], high52Date=hi['date'], low52=lo['close'], low52Date=lo['date'],
                         rangeBasis='closing prices')


# ---------------------------------------------------------------- the refresh
def refresh(state: dict, status: dict, now_utc: dt.datetime) -> None:
    now_et = now_utc.astimezone(ET)
    notes = status['warnings']
    srcs = status['sources']
    final = state['returns']['final_through']
    start = min(month_last_day(final) - dt.timedelta(days=12), now_et.date() - dt.timedelta(days=400))
    key = os.environ.get('TIINGO_API_KEY', '').strip()

    prices, source = fetch_prices(start, now_et, key, notes)
    rows, asof = align(prices)
    srcs['prices'] = {'ok': True, 'source': source, 'through': asof}

    factors, bases = {}, {}
    for t in TICKERS:
        factors[t], bases[t] = month_factors(rows[t], final)
    months_new = sorted(factors['BND'])
    if not months_new:
        raise RuntimeError(f'no trading days after {final}')
    for t in TICKERS:
        if sorted(factors[t]) != months_new:
            raise RuntimeError(f'{t} months {sorted(factors[t])} differ from BND {months_new}')
        for mk in months_new:
            lo, hi = BOUNDS[t]
            r = factors[t][mk][0] - 1
            if not lo < r < hi:
                raise RuntimeError(f'{t} return for {mk} looks wrong ({r:+.2%}); not updating')

    # How the fresh numbers compare with what the page showed for the same month
    prov = state['provisional']
    if prov['month'] in factors['BND']:
        status['checks'] = {
            'month': prov['month'],
            'previousThrough': prov['asOf'],
            'newThrough': factors['BND'][prov['month']][2],
            'changePct': {t: round((factors[t][prov['month']][0] - 1) * 100 - prov[t] / 1e4, 4) for t in TICKERS},
        }

    update_peak(state, rows['BND'], bases['BND'])   # before months are finalized (uses the old base)

    finalized = []
    for mk in months_new[:-1]:
        for t in TICKERS:
            state['returns'][t].append(ppm(factors[t][mk][0]))
        state['bnd_close']['values'].append(factors['BND'][mk][1])
        state['returns']['final_through'] = mk
        finalized.append(mk)
    cur = months_new[-1]
    state['provisional'] = {'month': cur, 'asOf': asof, **{t: ppm(factors[t][cur][0]) for t in TICKERS},
                            'bndClose': factors['BND'][cur][1]}
    status['finalizedThisRun'] = finalized

    dists = dict((d, a) for d, a in state['dists'])
    for r in rows['BND']:
        if r['div'] > 0:
            amount = round(r['div'], 6)
            # Price feeds sometimes round to 4 decimals; keep Vanguard's exact figure unless this is a real correction.
            if r['date'] not in dists or abs(dists[r['date']] - amount) > 0.00006:
                dists[r['date']] = amount
    keep_from = (iso_date(asof) - dt.timedelta(days=3 * 366)).isoformat()
    state['dists'] = sorted([d, a] for d, a in dists.items() if d >= keep_from)
    update_range(state, rows['BND'], asof)

    try:
        cpi = fetch_cpi()
        for k, v in cpi.items():
            state['cpi'][k] = v
            if k in state.get('cpi_interpolated', []):
                state['cpi_interpolated'].remove(k)
        srcs['cpi'] = {'ok': True, 'through': max(cpi)}
    except Exception as e:
        srcs['cpi'] = {'ok': False, 'error': str(e)[:200], 'through': max(state['cpi'])}
        notes.append('CPI not refreshed: ' + str(e)[:200])

    try:
        rates = fetch_treasury(now_et.date())
        if rates['date'] >= state['rates'].get('date', ''):
            state['rates'] = rates
        srcs['treasury'] = {'ok': True, 'through': rates['date']}
    except Exception as e:
        srcs['treasury'] = {'ok': False, 'error': str(e)[:200], 'through': state['rates'].get('date')}
        notes.append('Treasury yields not refreshed: ' + str(e)[:200])

    vg, vg_errors = fetch_vanguard()
    if vg:
        state['fund'].update(vg)
        anchors = state['assets']['anchors']
        m = vg.get('tnaDate', '')[:7]
        if ('tna' in vg and month_last_day(m).isoformat() == vg['tnaDate'] and m > anchors[-1][0]
                and m <= state['returns']['final_through']):
            nav = state['bnd_close']['values'][month_diff('2007-05', m)]
            anchors.append([m, vg['tna'], nav])
            notes.append(f'Added Vanguard net assets for {m}: ${vg["tna"] / 1000:.1f} billion')
    srcs['vanguard'] = {'ok': not vg_errors, 'fields': sorted(vg), 'error': '; '.join(vg_errors)[:300] or None}
    if vg_errors:
        notes.append('Vanguard fund facts not fully refreshed (kept the last values with their dates)')


# ---------------------------------------------------------------- page data
def assets_model(state: dict, all_months: list[str], closes: list[float], asof: dt.date):
    """Month-end shares outstanding and net assets ($M) for BND's ETF share class."""
    pos = {k: i for i, k in enumerate(all_months)}
    anchors = [a for a in state['assets']['anchors'] if a[0] in pos]
    shares: list = [None] * len(all_months)
    m0, na0, nav0 = anchors[0]
    s0 = na0 / nav0
    inception = iso_date(state['inception'])
    d0 = month_last_day(m0)
    for i in range(pos[m0] + 1):   # ramp up from launch to the first anchor
        shares[i] = s0 * (month_last_day(all_months[i]) - inception).days / (d0 - inception).days
    for (ma, na_a, nav_a), (mb, na_b, nav_b) in zip(anchors, anchors[1:]):
        ia, ib = pos[ma], pos[mb]
        sa, sb = na_a / nav_a, na_b / nav_b
        for i in range(ia, ib + 1):
            t = (i - ia) / (ib - ia)
            shares[i] = math.exp(math.log(sa) * (1 - t) + math.log(sb) * t)
    lm, lna, lnav = anchors[-1]
    ls = lna / lnav
    prev = [a for a in anchors[:-1] if month_diff(a[0], lm) >= 6][-1]
    ps = prev[1] / prev[2]
    pace = (ls - ps) / month_diff(prev[0], lm)        # million shares a month
    last = len(all_months) - 1
    frac = asof.day / calendar.monthrange(asof.year, asof.month)[1]
    for i in range(pos[lm] + 1, len(all_months)):
        k = month_diff(lm, all_months[i])
        if i == last:
            k = k - 1 + frac
        shares[i] = ls + pace * k
    anchor_na = {m: na for m, na, nav in anchors}
    aum = [anchor_na.get(all_months[i], shares[i] * closes[i]) for i in range(len(all_months))]
    pace_info = {'perMonth': pace * 1e6, 'from': prev[0], 'to': lm, 'sharesFrom': ps * 1e6, 'sharesTo': ls * 1e6}
    return shares, aum, pace_info


def build_page_data(state: dict) -> dict:
    final = state['returns']['final_through']
    prov = state['provisional']
    asof = prov['asOf']
    asof_d = iso_date(asof)
    cur = prov['month']
    if add_months(final, 1) != cur or asof[:7] != cur:
        raise RuntimeError(f'inconsistent state: final {final}, provisional {cur}, as of {asof}')
    months = [add_months(START, i) for i in range(month_diff(START, cur) + 1)]
    n = len(months)
    all_months = [add_months(START, -1)] + months
    r = {t: list(state['returns'][t]) + [prov[t]] for t in TICKERS}
    for t in TICKERS:
        if len(r[t]) != n:
            raise RuntimeError(f'{t} has {len(r[t])} months, expected {n}')

    cpi = state['cpi']
    latest = max(cpi)
    yoy = cpi[latest] / cpi[add_months(latest, -12)]
    frac = asof_d.day / days_in_month(cur)
    cpi_r = []
    for k in months:
        prev = add_months(k, -1)
        if k in cpi and prev in cpi:
            f = cpi[k] / cpi[prev]
        else:   # not yet published: assume the latest 12-month pace
            f = yoy ** ((frac if k == cur else 1.0) / 12)
        cpi_r.append(ppm(f))
    r['CPI'] = cpi_r

    closes = list(state['bnd_close']['values']) + [prov['bndClose']]
    if len(closes) != len(all_months):
        raise RuntimeError('month-end closes do not line up with the months')
    shares, aum, pace = assets_model(state, all_months, closes, asof_d)
    aum_r = [round(a, 1) for a in aum]
    shares_r = [round(s, 2) for s in shares]

    asof_ms = et_ms(asof_d, 16)
    fund = state['fund']
    er = fund['expenseRatio'] / 100
    f_bnd = [1 + v / 1e6 for v in r['BND']]
    aum_d = [a * 1e6 for a in aum_r]
    inv = [aum_d[i] + 0.5 * (aum_d[i + 1] - aum_d[i] * f_bnd[i]) for i in range(n)]
    y = asof_d.year
    y0 = ix(mkey(y, 1))
    fees = 0.0
    for i in range(y0, n):
        days = days_in_month(months[i]) if i < n - 1 else (asof_ms - et_ms(dt.date(y, asof_d.month, 1))) / 86400000
        fees += inv[i] * er * days / 365.25

    pos = {k: i for i, k in enumerate(all_months)}
    dists = [d for d in state['dists'] if d[0] <= asof]
    year_d = [d for d in dists if d[0][:4] == str(y)]
    paid = sum(a * shares_r[pos[add_months(d[:7], -1)]] * 1e6 for d, a in year_d)
    last_d, last_a = dists[-1]
    accrual_from = max(last_d, f'{y}-01-01')
    ttm_from = (asof_d - dt.timedelta(days=365)).isoformat()
    ttm = sum(a for d, a in dists if d > ttm_from)

    pk = state['peak']
    level_now = prod(f_bnd)
    gap = level_now / pk['level'] - 1
    cpi_now = cpi[latest] * prod(1 + v / 1e6 for k, v in zip(months, cpi_r) if k > latest)
    cpi_since = cpi_now / cpi[pk['date'][:7]] - 1
    prev_dec = closes[pos[mkey(y - 1, 12)]]

    fund_out = {k: v for k, v in fund.items()}
    fund_out.update(price=prov['bndClose'], priceYtd=prov['bndClose'] / prev_dec - 1, ttmDist=round(ttm, 6),
                    lastDist=last_a, lastDistDate=last_d)
    return {
        'asOf': asof, 'asOfMs': asof_ms, 'generated': None, 'start': START, 'n': n,
        'ytdIdx': y0, 'peakIdx': ix(pk['date'][:7]),
        'r': r, 'aum': aum_r, 'shares': shares_r, 'pace': pace,
        'anchors': [[m, na] for m, na, nav in state['assets']['anchors']],
        'fund': fund_out,
        'rates': state['rates'],
        'cpi': {'month': latest, 'yoy': yoy - 1, 'interpolated': state.get('cpi_interpolated', [])},
        'peak': {'date': pk['date'], 'ms': et_ms(iso_date(pk['date']), 16), 'gap': gap,
                 'cpiSince': cpi_since, 'real': (1 + gap) / (1 + cpi_since) - 1,
                 'closestDate': pk.get('closestDate'), 'closestGap': pk.get('closestGap'),
                 'priceHigh': pk['priceHigh'], 'priceHighDate': pk['priceHighDate']},
        'income': {'paidYtd': paid, 'perShareYtd': round(sum(a for d, a in year_d), 6), 'count': len(year_d),
                   'lastDist': last_a, 'lastDistDate': last_d, 'accrualFromMs': et_ms(iso_date(accrual_from))},
        'fees': {'ytd': fees},
        'priceSource': state.get('priceSource', 'Yahoo Finance'),
    }


# ---------------------------------------------------------------- expected values for the page test
def expected_values(page: dict) -> dict:
    """Independent Python version of the page's calculations, checked by tests/test_engine.js."""
    n = page['n']
    nf = n - 1
    f = {k: [1 + v / 1e6 for v in vals] for k, vals in page['r'].items()}
    aum = [v * 1e6 for v in page['aum']]
    sh = [v * 1e6 for v in page['shares']]
    inv = [aum[i] + 0.5 * (aum[i + 1] - aum[i] * f['BND'][i]) for i in range(n)]
    gap = {a: [inv[i] * (f[a][i] - f['BND'][i]) for i in range(n)] for a in ALTS}
    win = {'1Y': 12, '3Y': 36, '5Y': 60, '10Y': 120, 'SI': nf}
    G = {k: {b: prod(f[k][nf - w:nf]) ** (12 / w) - 1 for b, w in win.items()} for k in f}
    periods = {'si': 0, 'y10': n - 120, 'peak': page['peakIdx'], 'y5': n - 60, 'm12': n - 12, 'ytd': page['ytdIdx']}
    y_last = int(page['asOf'][:4])
    out = {'G': G, 'oc': {}, 'tenK': {}, 'years': {}, 'ratePerSec': {},
           'aum0': aum[n], 'shares0': sh[n]}
    for a in ALTS:
        out['oc'][a] = {p: sum(gap[a][s:]) for p, s in periods.items()}
        out['tenK'][a] = {p: [10000 * prod(f[a][s:]), 10000 * prod(f['BND'][s:])] for p, s in periods.items()}
        out['ratePerSec'][a] = {b: aum[n] * (G[a][b] - G['BND'][b]) / SPYR for b in win}
        rows = []
        for yy in range(2007, y_last + 1):
            i0 = 0 if yy == 2007 else ix(mkey(yy, 1))
            i1 = n if yy == y_last else ix(mkey(yy, 12)) + 1
            rows.append({'year': yy, 'oc': sum(gap[a][i0:i1]), 'cum': sum(gap[a][:i1]), 'aumEnd': aum[i1],
                         'ret': prod(f[a][i0:i1]) - 1, 'tenKX': 10000 * prod(f[a][:i1])})
        out['years'][a] = rows
    accrued = (page['asOfMs'] - page['income']['accrualFromMs']) / 1000
    out['income0'] = page['income']['paidYtd'] + page['income']['lastDist'] / (SPYR / 12) * sh[n] * accrued
    out['fees0'] = page['fees']['ytd']
    return out


# ---------------------------------------------------------------- build
def build_index(page: dict) -> None:
    tpl = (ROOT / 'src' / 'template.html').read_text(encoding='utf-8')
    body = tpl.replace('__CLOCK_DATA__', json.dumps(page, separators=(',', ':')))
    if '__CLOCK_DATA__' in body:
        raise RuntimeError('template placeholder not replaced')
    head_end = body.index('</style>') + len('</style>')
    doc = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
           '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
           + body[:head_end] + '\n</head>\n<body>\n' + body[head_end:].lstrip('\n') + '\n</body>\n</html>\n')
    out = ROOT / 'public' / 'index.html'
    out.parent.mkdir(exist_ok=True)
    out.write_text(doc, encoding='utf-8')


def stable(obj: dict) -> str:
    return json.dumps({k: v for k, v in obj.items() if k != 'generated'}, sort_keys=True)


def main(argv: list | None = None, now_utc: dt.datetime | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    offline = '--offline' in argv
    now_utc = now_utc or dt.datetime.now(dt.timezone.utc)
    state = load_json('state.json')
    old_status = load_json('status.json', {}) or {}
    status = {'asOf': None, 'dataUpdated': old_status.get('dataUpdated'), 'warnings': [],
              'sources': dict(old_status.get('sources', {})), 'finalizedThisRun': []}
    try:
        if not offline:
            refresh(state, status, now_utc)
            if status['sources'].get('prices', {}).get('ok'):
                state['priceSource'] = status['sources']['prices']['source']
        page = build_page_data(state)
        old_page = load_json('clock-data.json')
        if old_page and stable(old_page) == stable(page):
            page['generated'] = old_page['generated']
        else:
            page['generated'] = now_utc.isoformat(timespec='seconds')
            status['dataUpdated'] = page['generated']
        status['asOf'] = page['asOf']
        check = expected_values(page)
    except Exception as e:
        status['lastError'] = {'time': now_utc.isoformat(timespec='seconds'), 'message': f'{type(e).__name__}: {e}',
                               'trace': traceback.format_exc()[-1500:]}
        status['asOf'] = old_status.get('asOf')
        write_json('status.json', status)
        print(f'Update failed: {e}', file=sys.stderr)
        return 1

    write_json('state.json', state)
    write_json('clock-data.json', page, compact=True)
    write_json('check.json', check, compact=True)
    build_index(page)
    if offline:
        status['sources'] = old_status.get('sources', status['sources'])
        status['warnings'] = old_status.get('warnings', [])
        status['finalizedThisRun'] = old_status.get('finalizedThisRun', [])
        status['checks'] = old_status.get('checks')
    if json.dumps(status, sort_keys=True) != json.dumps(old_status, sort_keys=True):
        write_json('status.json', status)
    print(f"Clock data through {page['asOf']} ({page['n']} months). Generated {page['generated']}.")
    for w in status['warnings']:
        print('Note:', w)
    return 0


if __name__ == '__main__':
    sys.exit(main())
