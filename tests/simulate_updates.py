"""Simulated runs of scripts/update_data.py with made-up market data (no network).

Covers: a normal day, a month rolling over, a year rolling over (with a new Vanguard net-asset figure),
a Tiingo outage with Yahoo fallback, and a total outage (nothing may change except status.json).
Run: python tests/simulate_updates.py
"""
import datetime as dt
import importlib.util
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
ET = ZoneInfo('America/New_York')
spec = importlib.util.spec_from_file_location('update_data', ROOT / 'scripts' / 'update_data.py')
ud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ud)

failures = []


def check(name, cond):
    print(('ok   ' if cond else 'FAIL ') + name)
    if not cond:
        failures.append(name)


def weekdays(start, end):
    d = start
    while d <= end:
        if d.weekday() < 5 and not (d.month == 12 and d.day == 25) and not (d.month == 1 and d.day == 1):
            yield d
        d += dt.timedelta(days=1)


PARAMS = {  # start price, daily drift, daily swing, distribution rule
    'BND': (72.0, -0.00002, 0.002, 'monthly'), 'VTI': (300.0, 0.0006, 0.006, 'quarterly'),
    'VXUS': (72.0, 0.0004, 0.005, 'quarterly'), 'VOO': (570.0, 0.0006, 0.006, 'quarterly'), 'NVDA': (180.0, 0.001, 0.015, 'quarterly'),
}
TBILL_YIELD = 4.2   # flat 3-month Treasury yield in the made-up future
# BND distributions as a price feed reports them: Vanguard's exact amounts rounded to 4 decimals
FEED_ROUNDED = {'2026-09-01': 0.2529, '2026-10-01': 0.2485}


def fake_rows(ticker, start, end):
    p0, drift, swing, rule = PARAMS[ticker]
    rows, price = [], p0
    first = {}
    for d in weekdays(dt.date(2025, 1, 2), end):
        price *= 1 + drift + swing * math.sin(d.toordinal() * (0.7 + len(ticker) / 10))
        key = d.strftime('%Y-%m')
        is_first = key not in first
        first.setdefault(key, d)
        div = 0.0
        if is_first and (rule == 'monthly' or d.month in (3, 6, 9, 12)):
            div = round(price * (0.0035 if rule == 'monthly' else 0.003), 4)
        if ticker == 'BND' and d.isoformat() in FEED_ROUNDED:
            div = FEED_ROUNDED[d.isoformat()]
        if d >= start:
            rows.append({'date': d.isoformat(), 'close': round(price, 2), 'div': div, 'split': 1.0})
    return rows


def run_case(name, now_et, *, tiingo_ok=True, yahoo_ok=True, treasury_ok=True, cpi=None, vanguard=None, workdir):
    state_before = (workdir / 'data' / 'state.json').read_text()
    ud.ROOT, ud.DATA = workdir, workdir / 'data'

    def tiingo(t, start, end, key):
        if not tiingo_ok:
            raise RuntimeError('api.tiingo.com: HTTP Error 503: Service Unavailable')
        return fake_rows(t, start, end)

    def yahoo(t, start, end):
        if not yahoo_ok:
            raise RuntimeError('query1.finance.yahoo.com: HTTP Error 429: Too Many Requests')
        return fake_rows(t, start, end)

    def fake_cpi():
        if cpi is None:
            raise RuntimeError('api.bls.gov: timed out')
        return cpi

    ud.fetch_tiingo, ud.fetch_yahoo, ud.fetch_cpi = tiingo, yahoo, fake_cpi
    def treasury(today):
        if not treasury_ok:
            raise RuntimeError('home.treasury.gov: HTTP Error 503: Service Unavailable')
        last = today - dt.timedelta(days=1)   # published the evening before the run
        daily = {d.isoformat(): TBILL_YIELD for d in weekdays(dt.date(2026, 10, 22), last)}
        real = {d: [2.5, 2.6, 2.7, 3.0] for d in daily}
        return {'rates': {'tbill': TBILL_YIELD, 'tbillLabel': '3-month Treasury yield', 'ust10': 5.0, 'date': max(daily),
                          'source': 'U.S. Treasury daily par yield curve'}, 'daily': daily, 'real': real}
    ud.fetch_treasury = treasury
    ud.fetch_vanguard_expense = lambda t: {'er': 0.03, 'asOf': '2026-04-28', 'source': 'Vanguard'}
    ud.fetch_vanguard = lambda: vanguard or ({}, ['characteristic: investor.vanguard.com: HTTP Error 403: Forbidden'])
    import os
    os.environ['TIINGO_API_KEY'] = 'test-key'
    code = ud.main([], now_et.astimezone(dt.timezone.utc))
    state = json.loads((workdir / 'data' / 'state.json').read_text())
    status = json.loads((workdir / 'data' / 'status.json').read_text())
    page = json.loads((workdir / 'data' / 'clock-data.json').read_text())
    print(f'--- {name}: exit {code}, as of {page["asOf"]}, final through {state["returns"]["final_through"]}')
    return code, state, status, page, state_before


def split_check():
    """A 10-for-1 split as Tiingo reports it (raw closes plus the split factor) must not look like a 90% loss."""
    rows = [{'date': '2026-09-30', 'close': 1000.0, 'div': 0.0, 'split': 1.0},
            {'date': '2026-10-01', 'close': 101.0, 'div': 0.0, 'split': 10.0},     # split day: $1,000 -> $101 a share
            {'date': '2026-10-02', 'close': 102.0, 'div': 0.01, 'split': 1.0}]
    f = ud.month_factors(rows, '2026-09')[0]['2026-10'][0]
    check(f'10-for-1 split counted as a gain, not a loss ({f - 1:+.4%})', abs(f - 1010 / 1000 * 102.01 / 101) < 1e-12)


def tips_check():
    """A maturing TIPS returns 100 plus interest and inflation, then rolls into a new 10-year TIPS."""
    cpi = {'2027-02': 340.0, '2027-03': 341.7}
    bond = {'coupon': 0.375, 'maturity': '2027-05', 'price': 99.9}
    f, p = ud.tips_month(bond, '2027-05', [2.5, 2.6, 2.7, 3.0], cpi)
    check(f'TIPS maturity month pays par plus interest and inflation ({f - 1:+.4%})',
          p == 100.0 and abs(f - (100 + 0.375 / 12) / 99.9 * 341.7 / 340.0) < 1e-12)
    new = ud.tips_roll('2027-05', [2.5, 2.6, 2.7, 3.0])
    check('TIPS roll into a new 10-year bond with a 1/8-step coupon',
          new['maturity'] == '2037-05' and new['coupon'] == 2.625 and 99 < new['price'] <= 100)


def main():
    split_check()
    tips_check()
    with tempfile.TemporaryDirectory() as tmp:
        w = Path(tmp)
        for sub in ('data', 'src', 'scripts', 'tests'):
            shutil.copytree(ROOT / sub, w / sub)
        n0 = json.loads((w / 'data' / 'state.json').read_text())
        months0 = len(n0['returns']['BND'])

        # 1. A normal weekday evening in the same month
        code, st, status, page, before = run_case('same month', dt.datetime(2026, 10, 23, 19, 30, tzinfo=ET), workdir=w)
        check('same month: success', code == 0)
        old_d, new_d = dict(json.loads(before)['dists']), dict(st['dists'])
        check("same month: Vanguard's exact distribution amounts survive the feed's rounded ones",
              new_d['2026-09-01'] == 0.252886 and new_d['2026-10-01'] == 0.248502)
        feed = {r['date']: r['div'] for r in fake_rows('BND', dt.date(2025, 9, 18), dt.date(2026, 10, 23)) if r['div'] > 0}
        check('same month: real corrections from the feed are taken',
              all(new_d[d] == a for d, a in feed.items() if abs(old_d.get(d, -1) - a) > 0.00006))
        check('same month: data through Oct 23', page['asOf'] == '2026-10-23')
        check('same month: no month finalized', st['returns']['final_through'] == '2026-09' and len(st['returns']['BND']) == months0)
        check('same month: status compares the new numbers with the old ones', status.get('checks', {}).get('month') == '2026-10')
        check('same month: CPI outage is reported, not fatal', status['sources']['cpi']['ok'] is False)
        check('same month: Vanguard block is reported, not fatal', status['sources']['vanguard']['ok'] is False)
        check('same month: Treasury yields refreshed', page['rates']['ust10'] == 5.0)
        r = subprocess.run(['node', str(w / 'tests' / 'test_engine.js')], capture_output=True, text=True, cwd=w)
        check('same month: page engine matches Python (' + r.stdout.strip().splitlines()[-1] + ')', r.returncode == 0)

        # 2. Evening run before the market closes must not use the unfinished day
        code, st, status, page, _ = run_case('during market hours', dt.datetime(2026, 10, 26, 11, 0, tzinfo=ET), workdir=w)
        check('market hours: ignores the unfinished session', page['asOf'] == '2026-10-23')

        # 3. Month rolls over: October becomes permanent, November is the running month
        code, st, status, page, _ = run_case('month rollover', dt.datetime(2026, 11, 4, 19, 30, tzinfo=ET),
                                             cpi={'2026-09': 336.94}, workdir=w)
        check('rollover: success', code == 0)
        check('rollover: October finalized', st['returns']['final_through'] == '2026-10' and status['finalizedThisRun'] == ['2026-10'])
        check('rollover: one more month of history', len(st['returns']['BND']) == months0 + 1)
        check('rollover: November is the running month', st['provisional']['month'] == '2026-11' and page['n'] == months0 + 2)
        oct_tb = st['returns']['TBILL'][-1] / 1e6
        exp = ud.tbill_factor({**{d: v for d, v in st['tbill_daily'].items()}}, dt.date(2026, 10, 1), dt.date(2026, 10, 31)) - 1
        check(f'rollover: October T-bill return from Treasury yields ({oct_tb:.4%})', abs(oct_tb - exp) < 1e-6 and 0.003 < oct_tb < 0.004)
        check('rollover: November distribution recorded', any(d.startswith('2026-11') for d, a in st['dists']))
        check('rollover: October CPI estimated until published', page['r']['CPI'][-2] > 0)
        r = subprocess.run(['node', str(w / 'tests' / 'test_engine.js')], capture_output=True, text=True, cwd=w)
        check('rollover: page engine matches Python', r.returncode == 0)

        # 4. Year rolls over, Vanguard reachable again with a new month-end net-asset figure
        vg = ({'tna': 170000.0, 'tnaDate': '2026-11-30', 'fundTna': 400000.0, 'duration': 5.8, 'avgMaturity': 8.2,
               'avgCoupon': 3.9, 'ytm': 5.4, 'bonds': 11500, 'portfolioDate': '2026-11-30', 'secYield': 5.0,
               'secYieldDate': '2027-01-05', 'nav': 70.0, 'navDate': '2027-01-05', 'unrealizedPct': -8.5,
               'unrealizedPerShare': -6.0, 'unrealizedDate': '2026-12-31', 'expenseRatio': 0.03, 'peerExpenseRatio': 0.52}, [])
        code, st, status, page, _ = run_case('year rollover', dt.datetime(2027, 1, 6, 19, 30, tzinfo=ET),
                                             cpi={'2026-10': 337.5, '2026-11': 337.2}, vanguard=vg, workdir=w)
        check('new year: success', code == 0)
        check('new year: Nov and Dec finalized', st['returns']['final_through'] == '2026-12')
        check('new year: January is the running month', st['provisional']['month'] == '2027-01' and page['ytdIdx'] == ud.ix('2027-01'))
        check('new year: Vanguard November net assets became an anchor', st['assets']['anchors'][-1][0] == '2026-11')
        check('new year: published CPI replaced the estimate', st['cpi'].get('2026-11') == 337.2)
        check('new year: income counts only 2027 distributions', page['income']['count'] == 1)
        r = subprocess.run(['node', str(w / 'tests' / 'test_engine.js')], capture_output=True, text=True, cwd=w)
        check('new year: page engine matches Python', r.returncode == 0)
        if os.environ.get('SIM_KEEP'):
            shutil.copy(w / 'public' / 'index.html', os.environ['SIM_KEEP'])

        # 4b. Treasury down in the middle of a month: carry on with the last published yield
        code, st, status, page, _ = run_case('Treasury outage mid-month', dt.datetime(2027, 1, 7, 19, 30, tzinfo=ET),
                                             treasury_ok=False, workdir=w)
        check('Treasury outage mid-month: still updates', code == 0 and status['sources']['treasury']['ok'] is False)
        check('Treasury outage mid-month: says the T-bill month uses the last yield',
              any('last published 3-month yield' in n for n in status['warnings']))

        # 5. Tiingo down, Yahoo works
        code, st, status, page, _ = run_case('Tiingo outage', dt.datetime(2027, 1, 7, 19, 30, tzinfo=ET),
                                             tiingo_ok=False, workdir=w)
        check('Tiingo outage: falls back to Yahoo', code == 0 and status['sources']['prices']['source'] == 'Yahoo Finance')

        # 5b. Treasury down when a month must be finalized: the update stops, nothing changes
        before = {p: (w / p).read_text() for p in ('data/state.json', 'public/index.html')}
        code, st, status, page, _ = run_case('Treasury outage at month end', dt.datetime(2027, 2, 2, 19, 30, tzinfo=ET),
                                             treasury_ok=False, workdir=w)
        check('Treasury outage at month end: January is not finalized without its yields',
              code == 1 and before == {p: (w / p).read_text() for p in before} and 'not finalizing' in status['lastError']['message'])

        # 6. Everything down: nothing changes except status.json
        before = {p: (w / p).read_text() for p in ('data/state.json', 'data/clock-data.json', 'public/index.html')}
        code, st, status, page, _ = run_case('total outage', dt.datetime(2027, 1, 8, 19, 30, tzinfo=ET),
                                             tiingo_ok=False, yahoo_ok=False, workdir=w)
        after = {p: (w / p).read_text() for p in before}
        check('total outage: exits with an error', code == 1)
        check('total outage: data and page untouched', before == after)
        check('total outage: error recorded in status.json', 'Could not fetch prices' in status.get('lastError', {}).get('message', ''))

    print(f'\n{"All simulated runs passed" if not failures else str(len(failures)) + " failures"}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
