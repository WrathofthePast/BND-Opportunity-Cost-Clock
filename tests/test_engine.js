// Checks that the calculations embedded in public/index.html reproduce the independent Python values in
// data/check.json, and that the projections behave. Run: node tests/test_engine.js
'use strict';
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'public', 'index.html'), 'utf8');
const D = JSON.parse(html.match(/<script type="application\/json" id="clock-data">([\s\S]*?)<\/script>/)[1]);
const engineSrc = html.match(/<script id="engine">([\s\S]*?)<\/script>/)[1];
const BNDClock = new Function(engineSrc + '\nreturn BNDClock;')();
const X = JSON.parse(fs.readFileSync(path.join(root, 'data', 'check.json'), 'utf8'));
const E = BNDClock.build(D);
const T0 = D.asOfMs;

let checks = 0, fails = 0;
function close(name, a, b, tol = 1e-9) {
  checks++;
  const ok = Number.isFinite(a) && Number.isFinite(b) && Math.abs(a - b) <= tol * Math.max(1, Math.abs(b));
  if (!ok) { fails++; console.log('FAIL', name, a, b); }
}
function truthy(name, cond) { checks++; if (!cond) { fails++; console.log('FAIL', name); } }

const BASES = ['1Y', '3Y', '5Y', '10Y', 'SI'];
const PERIODS = ['si', 'y10', 'peak', 'y5', 'm12', 'ytd'];
for (const k of Object.keys(X.G)) for (const b of BASES) close(`G ${k} ${b}`, E.G[k][b], X.G[k][b]);
for (const a of BNDClock.ALTS) {
  for (const p of PERIODS) {
    close(`${a} oc ${p}`, E.ocAt(a, '10Y', p, T0), X.oc[a][p]);
    const tk = E.tenKAt(a, '10Y', p, T0);
    close(`${a} tenK ${p} alt`, tk.x, X.tenK[a][p][0]);
    close(`${a} tenK ${p} BND`, tk.b, X.tenK[a][p][1]);
  }
  for (const b of BASES) close(`${a} rate ${b}`, E.ratePerSec(a, b, T0), X.ratePerSec[a][b]);
  const rows = E.years(a);
  truthy(`${a} year count`, rows.length === X.years[a].length);
  rows.forEach((r, i) => {
    const x = X.years[a][i];
    truthy(`${a} year ${r.year}`, r.year === x.year);
    close(`${a} ${r.year} oc`, r.oc, x.oc);
    close(`${a} ${r.year} cum`, r.cum, x.cum);
    close(`${a} ${r.year} aum`, r.aumEnd, x.aumEnd);
    close(`${a} ${r.year} ret`, r.ret[a], x.ret);
    close(`${a} ${r.year} tenK`, r.tenKX, x.tenKX);
  });
}
close('assets now', E.aumAt(T0), X.aum0);
close('shares now', E.sharesAt(T0), X.shares0);
close('income now', E.incomeAt(T0), X.income0);
close('fees now', E.feesAt(T0), X.fees0);

// Projection behaviour
const day = 86400000;
const r0 = E.ratePerSec('VTI', '10Y', T0);
const d1 = E.ocAt('VTI', '10Y', 'si', T0 + day) - E.ocAt('VTI', '10Y', 'si', T0);
close('one day of ticking matches the rate', d1, r0 * 86400, 1e-3);
truthy('counters do not run backwards before the data date', E.ocAt('VTI', '10Y', 'si', T0 - day) === E.ocAt('VTI', '10Y', 'si', T0));
const ye = Date.UTC(E.YEAR + 1, 0, 1, 5);
close('this-year counters stop at year end', E.ocAt('VTI', '10Y', 'ytd', ye + 30 * day), E.ocAt('VTI', '10Y', 'ytd', ye), 1e-12);
close('income stops at year end', E.incomeAt(ye + 30 * day), E.incomeAt(ye), 1e-12);
const p = E.personal(10000, BNDClock.ix(2021, 1), 100, 'TBILL');
truthy('personal clock adds monthly contributions', p.put === 10000 + 100 * (E.N - BNDClock.ix(2021, 1) - 1));
const proj = E.projYear('VTI', '10Y', E.YEAR + 1);
truthy('projection is finite', Number.isFinite(proj.cum) && Number.isFinite(proj.oc) && Number.isFinite(proj.aumEnd));
for (const key of ['asOf', 'asOfMs', 'n', 'ytdIdx', 'peakIdx']) truthy(`data has ${key}`, D[key] !== undefined && D[key] !== null);
truthy('series lengths line up', ['BND', 'VTI', 'VOO', 'VXUS', 'NVDA', 'TIPS', 'TBILL', 'CPI'].every(k => D.r[k].length === D.n) && D.aum.length === D.n + 1 && D.shares.length === D.n + 1);

console.log(`${checks - fails}/${checks} checks passed (data through ${D.asOf}, ${D.n} months)`);
process.exit(fails ? 1 : 0);
