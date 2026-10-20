"""
Proposed dashboard.html change: data-driven SMMT provisional nowcast series for the EV Vehicles charts.

dashboard.html is pinned by the C1 release protections (release_config.PINNED_SHA256), so this change is NOT applied
to the live file. `patched_dashboard()` applies it, in memory, to the exact pinned version; the data manager serves the
result only on candidate preview pages. Adopting it in dashboard.html needs a separately approved pin change.

Data: the optional, versioned files written by data_manager/smmt_publish.py (integration/smmt_nowcast_{dno,la,laep}.csv
and smmt_nowcast_manifest.json), loaded by the existing Network/LA/LAEP loaders so every selector gets the nowcast. They
are used only when the manifest baseline equals metadata.json latest_actual_period and each file matches its SHA-256;
otherwise (absent, stale, partial or altered) the charts are shown without the nowcast and the caveat says why.
SMMT_NOWCAST rows inside the monthly forecast files are ignored, so forecasts and the nowcast cannot mix.

Behaviour (the page is unchanged when no SMMT files are published, i.e. with today's live files):
  * nowcast rows after the latest actual become a green dotted "SMMT provisional nowcast"
    series that starts at the latest actual observation (that point is shared, never counted twice);
  * when the nowcast is shown, the charts extend to the latest nowcast month (never beyond the supplied rows) and
    DFES/NESO are shown over the same months for comparison;
  * cumulative additions are measured from the same 31 March 2025 opening as the actual series; quarterly changes use
    the actual quarter boundaries plus any full quarter ends inside the nowcast;
  * caveats state that the series is provisional and where it starts and ends; a "SMMT nowcast" checkbox hides it.
"""

import hashlib
import os

PINNED_DASHBOARD_SHA256 = "51fcb61fa47f4a245d2905cb60a692db10171f553c08b9369843c34d860a4fc8"
# The only dashboard.html content the C1 re-pin log accepts (c1_release.DASHBOARD_REPIN): this patch applied to the pin.
PATCHED_DASHBOARD_SHA256 = "f93b0b5c0c3efd39363a0013e0cb6f755f62fcbf295d13ffa126c799c40f6f6f"

SMMT_LOADER = '''        // SMMT provisional nowcast: optional, versioned integration files written by the SMMT publication step
        // (data_manager/smmt_publish.py). Shown only when the manifest's baseline equals the latest C1 actual and every
        // file matches its SHA-256; otherwise the charts fall back to actuals and forecasts only.
        const SMMT_BASE = './project/output_processed/integration/';
        const smmtLevelState = {};
        const SMMT_FETCH = { cache: 'no-store' };       // never show a cached manifest or file after a republish/withdrawal
        let smmtManifestPromise = null;

        // SHA-256 of the file text: Web Crypto where available (HTTPS / localhost), otherwise a small JS implementation
        // so the integrity check also works when the dashboard is served over plain HTTP.
        function sha256HexJs(bytes) {
            const K = [0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
                0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
                0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
                0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
                0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
                0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
                0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
                0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2];
            const H = [0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19];
            const len = bytes.length, total = Math.ceil((len + 9) / 64) * 64;
            const m = new Uint8Array(total);
            m.set(bytes);
            m[len] = 0x80;
            const dv = new DataView(m.buffer);
            dv.setUint32(total - 8, Math.floor(len / 0x20000000));
            dv.setUint32(total - 4, (len * 8) >>> 0);
            const w = new Uint32Array(64);
            const rotr = (x, n) => (x >>> n) | (x << (32 - n));
            for (let off = 0; off < total; off += 64) {
                for (let i = 0; i < 16; i++) w[i] = dv.getUint32(off + i * 4);
                for (let i = 16; i < 64; i++) {
                    const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
                    const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
                    w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
                }
                let [a, b, c, d, e, f, g, h] = H;
                for (let i = 0; i < 64; i++) {
                    const t1 = (h + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) >>> 0;
                    const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) >>> 0;
                    h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
                }
                H[0] = (H[0] + a) >>> 0; H[1] = (H[1] + b) >>> 0; H[2] = (H[2] + c) >>> 0; H[3] = (H[3] + d) >>> 0;
                H[4] = (H[4] + e) >>> 0; H[5] = (H[5] + f) >>> 0; H[6] = (H[6] + g) >>> 0; H[7] = (H[7] + h) >>> 0;
            }
            return H.map(x => x.toString(16).padStart(8, '0')).join('');
        }

        async function sha256Hex(text) {
            const bytes = new TextEncoder().encode(text);
            if (typeof crypto !== 'undefined' && crypto.subtle) {
                const buf = await crypto.subtle.digest('SHA-256', bytes);
                return Array.from(new Uint8Array(buf)).map(b => b.toString(16).padStart(2, '0')).join('');
            }
            return sha256HexJs(bytes);
        }

        function loadSmmtManifest() {
            if (!smmtManifestPromise) smmtManifestPromise = (async () => {
                try {
                    const [mRes, metaRes] = await Promise.all([fetch(SMMT_BASE + 'smmt_nowcast_manifest.json', SMMT_FETCH), fetch(SMMT_BASE + 'metadata.json', SMMT_FETCH)]);
                    if (!mRes.ok) return { state: 'none' };
                    const m = await mRes.json();
                    const meta = metaRes.ok ? await metaRes.json() : {};
                    if (!m || m.baseline_period !== meta.latest_actual_period) {
                        return { state: 'stale', manifest: m || {}, latest: meta.latest_actual_period };
                    }
                    return { state: 'current', manifest: m };
                } catch (e) {
                    return { state: 'invalid', reason: e.message };
                }
            })();
            return smmtManifestPromise;
        }

        async function loadSmmtNowcast(level) {
            const st = await loadSmmtManifest();
            if (st.state !== 'current') { smmtLevelState[level] = st; return []; }
            const name = `smmt_nowcast_${level}.csv`;
            try {
                const res = await fetch(SMMT_BASE + name, SMMT_FETCH);
                if (!res.ok) throw new Error(`${name} is missing`);
                const text = await res.text();
                if (!st.manifest.files || await sha256Hex(text) !== st.manifest.files[name]) {
                    throw new Error(`${name} does not match the published manifest`);
                }
                const rows = parseCsv(text);
                const months = new Set(st.manifest.nowcast_months || []);
                if (rows.some(r => r.forecast_source !== 'SMMT_NOWCAST' || !months.has(r.period))) {
                    throw new Error(`${name} has rows outside the published nowcast months`);
                }
                smmtLevelState[level] = st;
                return rows;
            } catch (e) {
                smmtLevelState[level] = { state: 'invalid', reason: e.message, manifest: st.manifest };
                return [];
            }
        }

        async function loadEvIntegrationNetwork() {'''

REPLACEMENTS = [
    # 0. optional SMMT files loaded at every geographic level through the existing loaders
    ('''        async function loadEvIntegrationNetwork() {''', SMMT_LOADER),
    ('''            evForecastDno = parseCsv(await forecastRes.text());
''',
     '''            evForecastDno = parseCsv(await forecastRes.text()).filter(r => r.forecast_source !== 'SMMT_NOWCAST')
                .concat(await loadSmmtNowcast('dno'));
'''),
    ('''            evForecastLa = parseCsv(await forecastRes.text());
''',
     '''            evForecastLa = parseCsv(await forecastRes.text()).filter(r => r.forecast_source !== 'SMMT_NOWCAST')
                .concat(await loadSmmtNowcast('la'));
'''),
    ('''            evForecastLaep = parseCsv(await forecastRes.text());
''',
     '''            evForecastLaep = parseCsv(await forecastRes.text()).filter(r => r.forecast_source !== 'SMMT_NOWCAST')
                .concat(await loadSmmtNowcast('laep'));
'''),
    # 1. series toggle
    ('''                    <label><input type="checkbox" id="showNesoHtb" checked> <span id="seriesHtbLabel">Holistic Transition Basic</span></label>
''',
     '''                    <label><input type="checkbox" id="showNesoHtb" checked> <span id="seriesHtbLabel">Holistic Transition Basic</span></label>
                    <label id="showSmmtLabel"><input type="checkbox" id="showSmmt" checked> SMMT nowcast</label>
'''),
    ('''        let showNesoHtb = true;
''',
     '''        let showNesoHtb = true;
        let showSmmt = true;
'''),
    ('''        ['showActual', 'showDfes', 'showNesoHt', 'showNesoHtb'].forEach(id => {
            document.getElementById(id).addEventListener('change', () => {
                showActual = document.getElementById('showActual').checked;
                showDfes = document.getElementById('showDfes').checked;
                showNesoHt = document.getElementById('showNesoHt').checked;
                showNesoHtb = document.getElementById('showNesoHtb').checked;
''',
     '''        ['showActual', 'showDfes', 'showNesoHt', 'showNesoHtb', 'showSmmt'].forEach(id => {
            document.getElementById(id).addEventListener('change', () => {
                showActual = document.getElementById('showActual').checked;
                showDfes = document.getElementById('showDfes').checked;
                showNesoHt = document.getElementById('showNesoHt').checked;
                showNesoHtb = document.getElementById('showNesoHtb').checked;
                showSmmt = document.getElementById('showSmmt').checked;
'''),
    # 2. selected series: SMMT nowcast anchored at the latest actual; window extends only to supplied nowcast months
    ('''            const latestActualPeriod = observed[observed.length - 1].period;

            const windowRows = sorted.filter(r => r.period >= EV_BASELINE_PERIOD && r.period <= latestActualPeriod);
''',
     '''            const latestActualPeriod = observed[observed.length - 1].period;

            // SMMT provisional nowcast: only rows after the latest actual; it starts from that actual (shared point)
            const smmtRows = sorted.filter(r => r.smmt != null && r.period > latestActualPeriod);
            const smmtEnd = smmtRows.length ? smmtRows[smmtRows.length - 1].period : null;
            const smmt = smmtEnd ? {
                name: 'SMMT provisional nowcast', show: showSmmt, end: smmtEnd,
                line: { color: '#2E7D32', width: 3, dash: 'dot' },
                points: [{ period: latestActualPeriod, value: observed[observed.length - 1].value }]
                    .concat(smmtRows.map(r => ({ period: r.period, value: r.smmt })))
            } : null;
            const windowEnd = smmt && smmt.show ? smmtEnd : latestActualPeriod;

            const windowRows = sorted.filter(r => r.period >= EV_BASELINE_PERIOD && r.period <= windowEnd);
'''),
    ('''            return {
                observed,
                baselineSource: baseline.source,
                latestActualPeriod,
                forecasts: [''',
     '''            return {
                observed,
                baselineSource: baseline.source,
                latestActualPeriod,
                smmt,
                windowEnd,
                forecasts: ['''),
    # 3. cumulative chart
    ('''                series.forecasts.filter(f => f.show).forEach(f => {
                    traces.push(evTrace(evCumulativePoints(f.points), f.name, { type: 'scatter', mode: 'lines', line: f.line }));
                });
            }
            Plotly.newPlot('evCumulativeChart',''',
     '''                series.forecasts.filter(f => f.show).forEach(f => {
                    traces.push(evTrace(evCumulativePoints(f.points), f.name, { type: 'scatter', mode: 'lines', line: f.line }));
                });
                if (series.smmt && series.smmt.show) {
                    const opening = series.observed[0].value;   // same 31 March 2025 opening as the estimated series
                    traces.push(evTrace(series.smmt.points.map(p => ({ period: p.period, value: p.value - opening })),
                        series.smmt.name, { type: 'scatter', mode: 'lines+markers', line: series.smmt.line, marker: { size: 6 } }));
                }
            }
            Plotly.newPlot('evCumulativeChart','''),
    ('''            document.getElementById('evCumulativeCaveat').textContent = caveat;
''',
     '''            document.getElementById('evCumulativeCaveat').textContent = caveat + evSmmtCaveat(series);
'''),
    # 4. quarterly change chart
    ('''                const boundaries = series.observed.map(p => p.period);
''',
     '''                const boundaries = series.observed.map(p => p.period).concat(evSmmtQuarterEnds(series));
'''),
    ('''                series.forecasts.filter(f => f.show).forEach(f => {
                    traces.push(evTrace(evQuarterlyChangePoints(f.points, boundaries), f.name, { type: 'bar', marker: { color: f.line.color } }));
                });
''',
     '''                series.forecasts.filter(f => f.show).forEach(f => {
                    traces.push(evTrace(evQuarterlyChangePoints(f.points, boundaries), f.name, { type: 'bar', marker: { color: f.line.color } }));
                });
                if (series.smmt && series.smmt.show && boundaries.length > series.observed.length) {
                    traces.push(evTrace(evQuarterlyChangePoints(series.smmt.points, boundaries), series.smmt.name,
                        { type: 'bar', marker: { color: series.smmt.line.color } }));
                }
'''),
    ('''            document.getElementById('monthlyCaveat').textContent = caveat;
''',
     '''            document.getElementById('monthlyCaveat').textContent = caveat + evSmmtCaveat(series, true);
'''),
    # 5. stock chart
    ('''                series.forecasts.filter(f => f.show).forEach(f => {
                    traces.push(evTrace(f.points, f.name, { type: 'scatter', mode: 'lines', line: f.line }));
                });
            }
            Plotly.newPlot('stockChart', traces, evDateLayout('Vehicle Stock', series, emptyMessage), { responsive: true });''',
     '''                series.forecasts.filter(f => f.show).forEach(f => {
                    traces.push(evTrace(f.points, f.name, { type: 'scatter', mode: 'lines', line: f.line }));
                });
                if (series.smmt && series.smmt.show) {
                    traces.push(evTrace(series.smmt.points, series.smmt.name,
                        { type: 'scatter', mode: 'lines+markers', line: series.smmt.line, marker: { size: 6 } }));
                }
            }
            Plotly.newPlot('stockChart', traces, evDateLayout('Vehicle Stock', series, emptyMessage), { responsive: true });'''),
    ('''            document.getElementById('stockCaveat').textContent = caveat;
        }

        // LSOA Search functionality''',
     '''            document.getElementById('stockCaveat').textContent = caveat + evSmmtCaveat(series);
        }

        // Quarter ends inside the displayed SMMT nowcast (complete quarters only)
        function evSmmtQuarterEnds(series) {
            if (!series || !series.smmt || !series.smmt.show) return [];
            return series.smmt.points.slice(1).map(p => p.period)
                .filter(p => parseInt(p.slice(5, 7), 10) % 3 === 0);
        }

        function evSmmtCaveat(series, quarterly) {
            if (!series || !series.smmt) {
                const st = smmtLevelState[{ Network: 'dno', LA: 'la', LAEP: 'laep' }[currentGeographyLevel] || 'dno'];
                if (st && st.state === 'stale') return ` The published SMMT provisional nowcast (baseline ${st.manifest.baseline_period}) is not shown because the latest actual is ${st.latest}; it must be regenerated.`;
                if (st && st.state === 'invalid') return ` The SMMT provisional nowcast is not shown: ${st.reason}.`;
                return '';
            }
            if (!series.smmt.show) return ' The SMMT provisional nowcast is hidden.';
            const months = `${series.latestActualPeriod} to ${series.smmt.end}`;
            let text = ` SMMT provisional nowcast (green, dotted): monthly UK new registrations allocated to this area, starting from the latest actual (${series.latestActualPeriod}) so that point is not counted twice; ${months}. It is a provisional estimate, not an observation; DFES and UKPN lines are shown to the same month for comparison.`;
            if (quarterly) {
                text += evSmmtQuarterEnds(series).length
                    ? ' Its bars cover complete quarters inside the nowcast only.'
                    : ' The nowcast does not yet cover a complete quarter, so it has no quarterly bar.';
            }
            return text;
        }

        // LSOA Search functionality'''),
]


def dashboard_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def patched_dashboard(source_text: str) -> str:
    """Apply the change to the pinned dashboard.html text. Raises if the source is not the pinned version."""
    if dashboard_sha256(source_text) != PINNED_DASHBOARD_SHA256:
        raise ValueError("dashboard.html is not the pinned version; the SMMT preview patch is not applied")
    out = source_text
    eol = "\r\n" if "\r\n" in source_text else "\n"          # apply in the file's own line-ending style
    for old, new in REPLACEMENTS:
        old, new = old.replace("\n", eol), new.replace("\n", eol)
        if out.count(old) != 1:
            raise ValueError(f"patch anchor not found exactly once: {old.strip()[:80]!r}")
        out = out.replace(old, new)
    return out


def original_dashboard(patched_text: str) -> str:
    """Exact inverse of patched_dashboard: the pinned page, byte for byte (used to roll the dashboard back)."""
    if dashboard_sha256(patched_text) != PATCHED_DASHBOARD_SHA256:
        raise ValueError("dashboard.html is not the approved patched version; nothing to revert")
    out = patched_text
    eol = "\r\n" if "\r\n" in patched_text else "\n"
    for old, new in reversed(REPLACEMENTS):
        old, new = old.replace("\n", eol), new.replace("\n", eol)
        if out.count(new) != 1:
            raise ValueError(f"revert anchor not found exactly once: {new.strip()[:80]!r}")
        out = out.replace(new, old)
    if dashboard_sha256(out) != PINNED_DASHBOARD_SHA256:
        raise ValueError("revert did not reproduce the pinned dashboard.html")
    return out


def patched_dashboard_from_repo(project_root: str) -> str:
    with open(os.path.join(project_root, "dashboard.html"), encoding="utf-8", newline="") as f:
        return patched_dashboard(f.read())


def write_dashboard(project_root: str, action: str) -> dict:
    """apply | revert the approved change in <root>/dashboard.html (atomic replace). The C1 pin must then be updated
    with experiments/ev_redistribution_c1/release/repin_dashboard.py; the live repository is refused unless the
    live-release switch is on."""
    import sys
    import uuid
    root = os.path.abspath(project_root)
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if os.path.normcase(os.path.realpath(root)) == os.path.normcase(os.path.realpath(repo)):
        sys.path.insert(0, os.path.join(repo, "experiments", "ev_redistribution_c1", "release"))
        import release_config as cfg   # noqa: E402
        if not cfg.LIVE_RELEASE_ENABLED:
            raise PermissionError("changing the live dashboard.html is part of an approved release (LIVE_RELEASE_ENABLED=False)")
    path = os.path.join(root, "dashboard.html")
    with open(path, encoding="utf-8", newline="") as f:
        text = f.read()
    before = dashboard_sha256(text)
    new = patched_dashboard(text) if action == "apply" else original_dashboard(text) if action == "revert" else None
    if new is None:
        raise ValueError("action must be apply or revert")
    tmp = f"{path}.{uuid.uuid4().hex[:6]}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        f.write(new)
    os.replace(tmp, path)
    return {"action": action, "old_sha256": before, "new_sha256": dashboard_sha256(new),
            "next": f"python experiments/ev_redistribution_c1/release/repin_dashboard.py --root \"{root}\" (plan), then --approve"}


if __name__ == "__main__":
    import argparse
    import json
    ap = argparse.ArgumentParser(description="Apply or revert the approved SMMT dashboard change in <root>/dashboard.html")
    ap.add_argument("action", choices=["apply", "revert"])
    ap.add_argument("--root", required=True)
    a = ap.parse_args()
    print(json.dumps(write_dashboard(a.root, a.action), indent=2))
