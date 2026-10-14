// Read-only regression harness for the root dashboard.html (no browser).
// Runs the page's real <script> with stubbed DOM, fetch, Plotly and PapaParse, drives the controls,
// and prints captured Plotly inputs, titles, caveats, KPI cards and error messages as JSON on stdout.
// It never writes files. Run from the repository root:
//   node --no-opt --no-sparkplug experiments/ev_redistribution_c1/dashboard_regression/dashboard_harness.js <mode> [redirect.json]
// mode: ev | ev_local | nonev | path
// redirect.json (optional): {"ev_actuals_dno.csv": "<abs path>", ...} replaces integration fetches by file name.
// --no-opt/--no-sparkplug are required: optimised Node mis-handles repeated reassignment of the page's let-bound state.
'use strict';
const fs = require('fs');
const path = require('path');

const [, , mode, redirectFile] = process.argv;
const REPO = process.cwd();
const REDIRECT = redirectFile ? JSON.parse(fs.readFileSync(redirectFile, 'utf8')) : {};
const html = fs.readFileSync(path.join(REPO, 'dashboard.html'), 'utf8');
let src = [...html.matchAll(/<script>([\s\S]*?)<\/script>/g)].map(m => m[1]).pop();

const listeners = {}, els = {};
const mk = id => ({
    id, style: {}, textContent: '', value: '', innerHTML: '', placeholder: '', disabled: false, checked: true, children: [], dataset: {},
    classList: { add() {}, remove() {} },
    addEventListener(t, f) { (listeners[id] = listeners[id] || {})[t] = f; },
    appendChild(c) { this.children.push(c); },
    querySelectorAll() { return []; }
});
const geoButtons = ['Network', 'LA', 'LAEP'].map(l => { const e = mk('geo-' + l); e.dataset.level = l; return e; });
global.document = {
    getElementById(id) { if (!els[id]) { els[id] = mk(id); if (id === 'licenceArea') els[id].value = 'UKPN Total'; } return els[id]; },
    querySelectorAll(s) { return s === '.geo-btn' ? geoButtons : []; },
    createElement() { return mk('created'); }
};
let plots = {};
global.Plotly = { newPlot(id, traces, layout) { plots[id] = { traces: JSON.parse(JSON.stringify(traces)), layout: JSON.parse(JSON.stringify(layout)) }; } };
const fetched = [];
global.fetch = async url => {
    const name = url.split('/').pop();
    const target = (url.includes('/integration/') && REDIRECT[name]) ? REDIRECT[name] : path.join(REPO, url);
    fetched.push({ url, target: path.relative(REPO, target) });
    if (!fs.existsSync(target)) return { ok: false, status: 404, text: async () => '', json: async () => ({}) };
    const text = fs.readFileSync(target, 'utf8');
    return { ok: true, status: 200, text: async () => text, json: async () => JSON.parse(text) };
};
const FLOAT = /^\s*-?(\d+\.?|\.\d+|\d+\.\d+)([eE][-+]?\d+)?\s*$/;
global.Papa = { parse(text) {   // header + dynamicTyping + skipEmptyLines, as used by the page (no quoted fields in these CSVs)
    const lines = text.split(/\r?\n/).filter(l => l.length);
    const head = lines.length ? lines[0].split(',') : [];
    const data = [], errors = [];
    for (let i = 1; i < lines.length; i++) {
        const v = lines[i].split(',');
        if (v.length !== head.length) errors.push({ message: `Row ${i}: expected ${head.length} fields, saw ${v.length}` });
        const o = {};
        head.forEach((k, j) => { let z = v[j]; if (z === '' || z === undefined) z = null; else if (z === 'true' || z === 'TRUE') z = true; else if (z === 'false' || z === 'FALSE') z = false; else if (FLOAT.test(z)) z = parseFloat(z); o[k] = z; });
        data.push(o);
    }
    return { data, errors };
} };
global.window = {};
const consoleOut = [];
global.console = { log: (...a) => consoleOut.push('LOG ' + a.join(' ')), error: (...a) => consoleOut.push('ERR ' + a.join(' ')), warn: (...a) => consoleOut.push('WARN ' + a.join(' ')) };

src = src.replace(/applyTechControls\(\);\s*loadData\(\);\s*$/, '') +
    ';global.__H={loadData,get state(){return{tech:currentTech,area:currentArea,la:currentLa,laep:currentLaep,ev:currentEvSubtype,level:currentGeographyLevel}}};';
eval(src);
const H = global.__H;

const change = async (id, value) => { els[id].value = value; const r = listeners[id].change({ target: { value } }); if (r && r.then) await r; };
// The page's level-button handler has no catch: a rejected lazy load (LA/LAEP files) becomes an unhandled rejection
// in a browser. Record it against the next snapshot instead of aborting the run.
let pendingErrors = [];
const level = async l => {
    try { await listeners['geo-' + l].click({ target: geoButtons.find(b => b.dataset.level === l) }); }
    catch (e) { pendingErrors.push(`level ${l}: ${e.message}`); }
};
const results = [];
const snap = label => results.push({
    label, state: H.state, handlerErrors: pendingErrors.splice(0),
    cumulativeVisible: els.evCumulativeChartContainer ? els.evCumulativeChartContainer.style.display : undefined,
    titles: ['evCumulativeChartTitle', 'monthlyChartTitle', 'stockChartTitle'].map(i => (els[i] || {}).textContent),
    caveats: ['evCumulativeCaveat', 'monthlyCaveat', 'stockCaveat'].map(i => (els[i] || {}).textContent),
    kpis: els.kpis.children.map(c => c.innerHTML.replace(/<[^>]+>/g, '|').replace(/\s+/g, ' ').replace(/(\| ?)+/g, '|').trim()),
    kpiError: els.kpis.innerHTML.includes('error'),
    plots: JSON.parse(JSON.stringify(plots))
});
const step = async (label, fn) => {
    els.kpis.children = []; plots = {};          // a step that fails to redraw must not inherit the previous step's charts
    try { await fn(); } catch (e) { pendingErrors.push(`${label}: ${e.message}`); }
    snap(label);
};

(async () => {
    await H.loadData();
    const startup = { error: els.errorMessage ? els.errorMessage.textContent : '', errorShown: els.errorMessage ? els.errorMessage.style.display === 'block' : false };
    if (!startup.errorShown) {
        if (mode === 'nonev') {
            for (const tech of ['Heat Pump', 'Solar PV', 'EV Chargers']) {
                await level('Network'); await change('technology', tech);
                for (const a of ['UKPN Total', 'EPN', 'LPN', 'SPN']) await step(`${tech}|Network|${a}`, () => change('licenceArea', a));
                if (tech === 'Heat Pump') for (const s of ['Domestic', 'Hybrid', 'Total']) await step(`${tech}|Network|UKPN|${s}`, async () => { await change('licenceArea', 'UKPN Total'); await change('hpSubtype', s); });
                await change('licenceArea', 'UKPN Total');
                await level('LA'); await step(`${tech}|LA|Peterborough`, () => change('laSelector', 'E06000031'));
                await level('LAEP');
                for (const l of ['Outer East LAEP', 'Norwich LAEP']) await step(`${tech}|LAEP|${l}`, () => change('laepSelector', l));
            }
        } else if (mode === 'ev') {
            await change('technology', 'EV Vehicles');
            for (const a of ['UKPN Total', 'EPN', 'LPN', 'SPN']) {
                await change('licenceArea', a);
                for (const s of ['Total', 'BEV', 'PHEV']) await step(`Network|${a}|${s}`, () => change('evSubtype', s));
            }
            await change('evSubtype', 'Total'); await change('licenceArea', 'UKPN Total');
        } else if (mode === 'ev_local') {
            await change('technology', 'EV Vehicles');
            await level('LA');
            for (const [code, name] of [['E06000031', 'Peterborough'], ['E09000033', 'Westminster']]) {
                await change('laSelector', code);
                for (const s of ['Total', 'BEV', 'PHEV']) await step(`LA|${name}|${s}`, () => change('evSubtype', s));
                await change('evSubtype', 'Total');
            }
            await level('LAEP');
            for (const l of ['Peterborough LAEP', 'Norwich LAEP', 'Westminster LAEP', 'Outer East LAEP', 'West London LAEP']) {
                await change('laepSelector', l);
                for (const s of ['Total', 'BEV', 'PHEV']) await step(`LAEP|${l}|${s}`, () => change('evSubtype', s));
                await change('evSubtype', 'Total');
            }
        } else if (mode === 'path') {
            await change('technology', 'EV Vehicles');
            for (const s of ['Total', 'BEV', 'PHEV']) {
                await change('evSubtype', s);
                await level('Network'); await step(`PATH|${s}|Network|UKPN Total`, () => change('licenceArea', 'UKPN Total'));
                await level('LA'); await step(`PATH|${s}|LA|Peterborough`, () => change('laSelector', 'E06000031'));
                await level('LAEP'); await step(`PATH|${s}|LAEP|Outer East LAEP`, () => change('laepSelector', 'Outer East LAEP'));
                await level('Network'); await step(`PATH|${s}|Network|EPN`, () => change('licenceArea', 'EPN'));
                await level('LAEP'); await step(`PATH|${s}|LAEP|Norwich LAEP`, () => change('laepSelector', 'Norwich LAEP'));
                await level('Network'); await step(`PATH|${s}|Network|UKPN Total#2`, () => change('licenceArea', 'UKPN Total'));
            }
        } else {
            throw new Error('unknown mode ' + mode);
        }
    }
    process.stdout.write(JSON.stringify({ mode, startup, fetched, results, console: consoleOut }));
})().catch(e => { process.stdout.write(JSON.stringify({ fatal: String(e && e.stack || e) })); process.exitCode = 1; });
