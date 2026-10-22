"""Rebuild the TIPS monthly returns (June 2007 to September 2026) from the U.S. Treasury's real yield curve and CPI.

The clock's TIPS comparison is one 10-year TIPS bought at TreasuryDirect (no fee) at the end of May 2007,
held to maturity in May 2017 and rolled into a new 10-year TIPS, valued each month at market prices
(scripts/update_data.py: tips_month, tips_roll). Month-end real yields come from the Treasury's yearly
CSV files; CPI-U from data/state.json.

Run from the repo root where home.treasury.gov is reachable (e.g. a GitHub Actions runner):
    python history/tips_from_treasury.py      prints any month that differs from data/state.json
"""
import csv
import datetime as dt
import importlib.util
import io
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location('ud', ROOT / 'scripts' / 'update_data.py')
ud = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ud)

real = {}
for year in range(2007, 2027):
    url = ('https://home.treasury.gov/resource-center/data-chart-center/interest-rates/daily-treasury-rates.csv/'
           f'{year}/all?type=daily_treasury_real_yield_curve&field_tdr_date_value={year}&page&_format=csv')
    for r in csv.DictReader(io.StringIO(ud.http_get(url, {'Accept': 'text/csv'}).decode('utf-8-sig'))):
        r = {k.strip().upper(): (v or '').strip() for k, v in r.items() if k}
        cols = [r.get(c, '') for c in ('5 YR', '7 YR', '10 YR', '20 YR')]
        if r.get('DATE') and all(cols):
            real[dt.datetime.strptime(r['DATE'], '%m/%d/%Y').date().isoformat()] = [float(c) for c in cols]
dates = sorted(real)


def month_end_row(mk):
    return real[[d for d in dates if d <= ud.month_last_day(mk).isoformat()][-1]]


state = json.loads((ROOT / 'data' / 'state.json').read_text())
stored, cpi = state['returns']['TIPS'], state['cpi']
bond, diffs, mk = ud.tips_roll('2007-05', month_end_row('2007-05')), 0, '2007-05'
for i in range(len(stored)):
    mk = ud.add_months(mk, 1)
    row = month_end_row(mk)
    f, bond['price'] = ud.tips_month(bond, mk, row, cpi)
    if ud.ppm(f) != stored[i]:
        diffs += 1
        print(f'{mk}: Treasury data gives {ud.ppm(f)} ppm, state.json has {stored[i]}')
    if ud.month_diff(mk, bond['maturity']) <= 0:
        bond = ud.tips_roll(mk, row)
print(f'{diffs} months differ out of {len(stored)}')
