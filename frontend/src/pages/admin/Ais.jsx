import React, { useState } from "react";
import { api } from "../../api";
import MapView from "../../components/MapView";
import { Badge, can, Panel, Table, useAsync, useAuth, useToast } from "../../components/ui";

export default function Ais() {
  const { user } = useAuth();
  const toast = useToast();
  const [q, setQ] = useState("");
  const { data: fixes, reload } = useAsync(() => api.get(`/ais/fixes?q=${encodeURIComponent(q)}&limit=300`), [q]);
  const { data: airways, reload: reloadAw } = useAsync(() => api.get("/ais/airways"), []);
  const { data: apiStat, reload: reloadApi } = useAsync(() => api.get("/fpl/api-status"), []);
  const [nf, setNf] = useState({ name: "", lat: "", lon: "", kind: "waypoint" });
  const [busy, setBusy] = useState(false);
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
    try { const r = await api.post("/ais/import", fd); toast(`Imported ${r.imported} ${kind}`); reload(); reloadAw(); setVer(ver + 1); } catch (x) { toast(x.message, "error"); }
    e.target.value = "";
  };
  const navearth = async () => {
    setBusy(true);
    try { const r = await api.post("/ais/navearth-refresh"); toast(`NavEarth: ${r.points ?? r.fixes ?? 0} points, ${r.navaids ?? 0} navaids, ${r.airways} airways`); reload(); reloadAw(); setVer(ver + 1); } catch (e) { toast(e.message, "error"); }
    setBusy(false);
  };
  return (
    <div className="page">
      <h1 style={{ marginBottom: 14 }}>Aeronautical data</h1>
      <div className="grid2">
        <Panel title="Sources" sub="Where route expansion and the map layers get their data">
          <div className="stack">
            <div className="row" style={{ justifyContent: "space-between" }}>
              <div><b>NavEarth (AirNav AIS)</b><div className="small muted">Reporting points, navaids and ATS routes from the NavEarth WFS. Takes precedence over imports.</div></div>
              {admin && <button className="btn sm" disabled={busy} onClick={navearth}>{busy ? "Refreshing…" : "Refresh from NavEarth"}</button>}
            </div>
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
        <MapView deps={[ver]} show={["Airways", "Waypoints", "FIR boundaries"]} draw={() => null} />
      </Panel>
      <div className="grid2">
        <Panel title="Fixes" actions={<input placeholder="Search name" value={q} onChange={(e) => setQ(e.target.value)} />}>
          <Table maxHeight={420} rows={fixes || []} columns={[{ key: "name", label: "Name" }, { label: "Lat", num: true, render: (f) => f.lat.toFixed(4) },
            { label: "Lon", num: true, render: (f) => f.lon.toFixed(4) }, { key: "kind", label: "Kind" }, { key: "source", label: "Source" }, { key: "airac", label: "AIRAC" }]} />
        </Panel>
        <Panel title="Airways">
          <Table maxHeight={420} rows={airways || []} columns={[{ key: "name", label: "Airway" }, { key: "source", label: "Source" },
            { label: "Fixes", render: (a) => <span className="small">{a.fixes.join(" ")}</span> }]} />
        </Panel>
      </div>
    </div>
  );
}
