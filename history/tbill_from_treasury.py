"""Rebuild the T-bill monthly returns (June 2007 to September 2026) from the U.S. Treasury's daily yields.

The clock's T-bill comparison is fee-free 3-month Treasury bills, as bought at TreasuryDirect and
rolled over: each calendar day earns 1/365 of the latest 3-month par yield published before that
day (scripts/update_data.py, tbill_factor). This replaced the BIL ETF on Oct 22, 2026; the series in
data/state.json was computed this way from the Treasury's yearly CSV files for 2006 to 2026.

Run from the repo root where home.treasury.gov is reachable (e.g. a GitHub Actions runner):
    python history/tbill_from_treasury.py      prints any month that differs from data/state.json
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

daily = {}
for year in range(2006, 2027):
    url = ('https://home.treasury.gov/resource-center/data-chart-center/interest-rates/'
           f'daily-treasury-rates.csv/{year}/all?type=daily_treasury_yield_curve&field_tdr_date_value={year}&page&_format=csv')
    for r in csv.DictReader(io.StringIO(ud.http_get(url, {'Accept': 'text/csv'}).decode('utf-8-sig'))):
        if r.get('Date') and (r.get('3 Mo') or '').strip():
            daily[dt.datetime.strptime(r['Date'], '%m/%d/%Y').date().isoformat()] = float(r['3 Mo'])

state = json.loads((ROOT / 'data' / 'state.json').read_text())
stored = state['returns']['TBILL']
diffs = 0
for i in range(ud.month_diff(ud.START, '2026-09') + 1):
    mk = ud.add_months(ud.START, i)
    value = ud.ppm(ud.tbill_factor(daily, ud.iso_date(mk + '-01'), ud.month_last_day(mk)))
    if value != stored[i]:
        diffs += 1
        print(f'{mk}: Treasury gives {value} ppm, state.json has {stored[i]}')
print(f'{diffs} months differ out of {i + 1}')
