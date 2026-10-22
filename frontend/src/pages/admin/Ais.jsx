import React, { useState } from "react";
import { api, dt } from "../../api";
import MapView, { resetAis } from "../../components/MapView";
import { Badge, can, Panel, Table, useAsync, useAuth, useToast } from "../../components/ui";

export default function Ais() {
  const { user } = useAuth();
  const toast = useToast();
  const [q, setQ] = useState("");
  const { data: fixes, reload } = useAsync(() => api.get(`/ais/fixes?q=${encodeURIComponent(q)}&limit=300`), [q]);
  const { data: airways, reload: reloadAw } = useAsync(() => api.get("/ais/airways"), []);
  const { data: apiStat, reload: reloadApi } = useAsync(() => api.get("/fpl/api-status"), []);
  const [nf, setNf] = useState({ name: "", lat: "", lon: "", kind: "waypoint" });
  const [ver, setVer] = useState(0);
  const analyst = can(user, "analyst");
  const admin = user.role === "admin";
  const add = async () => {
    try { await api.post("/ais/fixes", nf); toast(`Saved ${nf.name.toUpperCase()}`); setNf({ name: "", lat: "", lon: "", kind: "waypoint" }); reload(); setVer(ver + 1); } catch (e) { toast(e.message, "error"); }
  };
  const imp = (kind) => async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("kind", kind);
    fd.append("file", f);
    try { const r = await api.post("/ais/import", fd); toast(`Imported ${r.imported} ${kind}`); resetAis(); reload(); reloadAw(); setVer(ver + 1); } catch (x) { toast(x.message, "error"); }
    e.target.value = "";
  };
  return (
    <div className="page">
      <h1 style={{ marginBottom: 14 }}>Aeronautical data</h1>
      <NavEarth admin={admin} onLoaded={() => { resetAis(); reload(); reloadAw(); setVer((v) => v + 1); }} />
      <div className="grid2">
        <Panel title="Other sources" sub="Flight plans and file imports">
          <div className="stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <div><b>AirNav flight data API</b><div className="small muted">{apiStat ? apiStat.base : "checking…"} · reachable only on the AirNav VPN</div></div>
              <div className="row">{apiStat && (apiStat.ok ? <Badge kind="good">reachable</Badge> : <Badge kind="bad" >not reachable</Badge>)}<button className="btn ghost sm" onClick={reloadApi}>Check</button></div>
            </div>
            {apiStat && !apiStat.ok && <p className="small muted">{apiStat.message}</p>}
            {analyst && (
              <div className="row">
                <label className="btn ghost sm" style={{ cursor: "pointer" }}>Import fixes CSV<input type="file" accept=".csv" hidden onChange={imp("fixes")} /></label>
                <label className="btn ghost sm" style={{ cursor: "pointer" }}>Import airways CSV<input type="file" accept=".csv" hidden onChange={imp("airways")} /></label>
                {admin && <label className="btn ghost sm" style={{ cursor: "pointer" }}>Import FIR / airspace GeoJSON<input type="file" accept=".json,.geojson" hidden onChange={imp("airspace")} /></label>}
              </div>
            )}
            <p className="small muted">Fixes CSV columns: name, lat, lon[, kind, airac]. Airways CSV: airway, seq, fix (one row per fix in order). Airspace GeoJSON: polygons with a <code>code</code> property (e.g. WIIF, VTBB).</p>
          </div>
        </Panel>
        <Panel title="Add or correct a fix" sub="Use this for fixes reported as unresolved in a dataset's plan check">
          {analyst ? (
            <div className="stack">
              <div className="row">
                <label className="f">Name<input value={nf.name} onChange={(e) => setNf({ ...nf, name: e.target.value })} placeholder="TUMGA" /></label>
                <label className="f">Latitude (°)<input value={nf.lat} onChange={(e) => setNf({ ...nf, lat: e.target.value })} placeholder="7.5" /></label>
                <label className="f">Longitude (°)<input value={nf.lon} onChange={(e) => setNf({ ...nf, lon: e.target.value })} placeholder="98.9" /></label>
                <label className="f">Kind<select value={nf.kind} onChange={(e) => setNf({ ...nf, kind: e.target.value })}><option>waypoint</option><option>navaid</option></select></label>
              </div>
              <div className="row end"><button className="btn" disabled={!nf.name || nf.lat === "" || nf.lon === "" || isNaN(+nf.lat) || isNaN(+nf.lon)} onClick={add}>Save fix</button></div>
            </div>
          ) : <p className="muted">Analysts and administrators can add fixes.</p>}
        </Panel>
      </div>
      <Panel title="Map">
        <MapView key={ver} show={["Airways", "Waypoints", "FIR boundaries", "TMA boundaries"]} draw={() => null} />
      </Panel>
      <div className="grid2">
        <Panel title="Fixes" actions={<input placeholder="Search name" value={q} onChange={(e) => setQ(e.target.value)} />}>
          <Table maxHeight={420} rows={fixes || []} columns={[{ key: "name", label: "Name" }, { label: "Lat", num: true, render: (f) => f.lat.toFixed(4) },
            { label: "Lon", num: true, render: (f) => f.lon.toFixed(4) }, { key: "kind", label: "Kind" }, { key: "source", label: "Source" }, { key: "airac", label: "AIRAC" }]} />
        </Panel>
        <Panel title="Airways">
          <Table maxHeight={420} rows={airways || []} columns={[{ key: "name", label: "Airway" }, { key: "source", label: "Source" },
            { label: "Fixes", render: (a) => <span className="small">{(a.chains || [a.fixes]).map((c) => c.join(" ")).join("  |  ")}</span> }]} />
        </Panel>
      </div>
    </div>
  );
}

const LAYER_HELP = {
  points: "Reporting points (waypoints) used to expand filed routes",
  navaids: "En-route navaids (VOR, NDB, DME)",
  routes: "ATS routes; separate several layers with commas",
  tma: "TMA polygons, drawn on the maps (not used for the FIR metric)",
  aerodromes: "Aerodrome reference points; new projects take their ADEP/ADES coordinates from these",
  fir: "FIR boundaries, used for efficiency by FIR (metric 6)",
};

function NavEarth({ admin, onLoaded }) {
  const toast = useToast();
  const { data: st, setData } = useAsync(() => api.get("/settings/navearth"), []);
  const [form, setForm] = useState(null);
  const [test, setTest] = useState(null);
  const [busy, setBusy] = useState("");
  const [last, setLast] = useState(null);
  if (!st) return null;
  const f = form || { url: st.url, layers: st.layers };
  const dirty = form && (form.url !== st.url || JSON.stringify(form.layers) !== JSON.stringify(st.layers));
  const isDefault = f.url === st.default.url && JSON.stringify(f.layers) === JSON.stringify(st.default.layers);
  const run = async (what, fn) => { setBusy(what); try { await fn(); } catch (e) { toast(e.message, "error"); } setBusy(""); };
  const save = () => run("save", async () => { const r = await api.put("/settings/navearth", f); setData(r); setForm(null); toast("NavEarth address saved"); });
  const reset = () => run("save", async () => { const r = await api.put("/settings/navearth", { reset: true }); setData(r); setForm(null); setTest(null); toast("NavEarth address restored to the default"); });
  const check = () => run("test", async () => { setTest(null); setTest((await api.post("/ais/navearth-test", f)).results); });
  const refresh = () => run("refresh", async () => {
    const r = await api.post("/ais/navearth-refresh");
    setLast(r);
    toast(`Loaded from NavEarth: ${r.fixes} points, ${r.navaids} navaids, ${r.airways} airways, ${r.aerodromes ?? 0} aerodromes, ${r.firs ?? 0} FIRs, ${r.tma} TMA polygons`);
    onLoaded();
  });
  return (
    <Panel title="NavEarth (AirNav AIS)" sub="CPTEA loads reporting points, navaids, ATS routes, aerodrome reference points, FIR boundaries and TMA polygons from this WFS address. NavEarth data takes precedence over imported files."
      actions={admin && <button className="btn" disabled={!!busy || dirty} title={dirty ? "Save the address first" : ""} onClick={refresh}>{busy === "refresh" ? "Loading from NavEarth…" : "Refresh from NavEarth"}</button>}>
      <div className="stack">
        <label className="f">WFS address
          <input value={f.url} readOnly={!admin} onChange={(e) => { setForm({ ...f, url: e.target.value }); setTest(null); }} spellCheck={false} style={{ fontFamily: "DejaVu Sans Mono, monospace", fontSize: 12.5 }} />
        </label>
        <p className="small muted" style={{ marginTop: 4 }}>
          Paste the GeoServer endpoint (…/ows) or a full GetFeature link. CPTEA keeps its version and output format and asks for each layer below in turn.
          {" "}{st.saved ? <>Saved by {st.updated_by} on {dt(st.updated_at)}.</> : <>Using the default address.</>}
        </p>
        {admin && (
          <details>
            <summary className="small" style={{ cursor: "pointer" }}>Layer names</summary>
            <div className="formgrid" style={{ marginTop: 8 }}>
              {Object.keys(st.default.layers).map((k) => (
                <label key={k} className="f">{st.labels[k]}
                  <input value={f.layers[k] || ""} placeholder="(not loaded)" spellCheck={false} onChange={(e) => { setForm({ ...f, layers: { ...f.layers, [k]: e.target.value } }); setTest(null); }} />
                  <span className="small muted">{LAYER_HELP[k]}</span>
                </label>
              ))}
            </div>
          </details>
        )}
        {admin && (
          <div className="row">
            <button className="btn ghost" disabled={!!busy} onClick={check}>{busy === "test" ? "Testing…" : "Test connection"}</button>
            <button className="btn" disabled={!!busy || !dirty} onClick={save}>Save address</button>
            {dirty && <button className="btn ghost" onClick={() => { setForm(null); setTest(null); }}>Discard changes</button>}
            {!isDefault && <button className="btn ghost" disabled={!!busy} onClick={reset}>Restore default</button>}
          </div>
        )}
        {test && (
          <Table rows={test} columns={[{ label: "Layer", render: (r) => <code>{r.layer}</code> },
            { label: "Result", render: (r) => (r.ok ? <Badge kind="good">{r.message}</Badge> : <Badge kind="bad">failed</Badge>) },
            { label: "Detail", render: (r) => <span className="small">{r.ok ? (r.properties || []).join(", ") : r.message}</span> }]} />
        )}
        {test && test.some((r) => !r.ok) && test.every((r) => !r.ok) && (
          <p className="small muted">The CPTEA server could not reach this address. Check that the server can open it (network, VPN or proxy), then test again.</p>
        )}
        {last && last.errors.length > 0 && <p className="small"><Badge kind="warn">some layers failed</Badge> {last.errors.join("; ")}</p>}
      </div>
    </Panel>
  );
}
