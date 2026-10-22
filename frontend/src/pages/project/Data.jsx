import React, { useEffect, useMemo, useState } from "react";
import { api, dt, fmt, pct } from "../../api";
import MapView from "../../components/MapView";
import { can, Empty, Modal, Panel, StatusBadge, Table, Tabs, useAsync, useAuth, useToast } from "../../components/ui";
import { useProject } from "../ProjectShell";

export default function Data() {
  const { project: p } = useProject();
  const { user } = useAuth();
  const { data: sets, reload } = useAsync(() => api.get(`/projects/${p.id}/datasets`), [p.id]);
  const [sel, setSel] = useState(null);
  const [upload, setUpload] = useState(false);
  useEffect(() => {
    if (sets && sets.length && !sel) setSel(sets[0].id);
    if (sets && sets.some((d) => d.status === "loading")) {
      const t = setTimeout(reload, 2500);
      return () => clearTimeout(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sets]);
  return (
    <div>
      <Panel title="Track datasets" sub="Each upload is kept as an immutable version" actions={can(user, "analyst") && <button className="btn" onClick={() => setUpload(true)}>Load tracks</button>}>
        <Table rows={sets || []} onRow={(d) => setSel(d.id)} empty="No tracks loaded yet. Load FR24-style CSV files, a ZIP or a server folder."
          columns={[{ key: "name", label: "Dataset", render: (d) => <b style={{ color: d.id === sel ? "var(--teal-2)" : undefined }}>{d.name} (v{d.version})</b> },
            { label: "Status", render: (d) => <StatusBadge s={d.status} /> }, { key: "message", label: "Message" },
            { key: "n_files", label: "Files", num: true }, { key: "n_flights", label: "Flights", num: true },
            { label: "Loaded", render: (d) => dt(d.created_at) }]} />
      </Panel>
      {sel && <DatasetDetail did={sel} key={sel} onDeleted={() => { setSel(null); reload(); }} />}
      <FlightPlans datasetId={sel} />
      {upload && <Upload onClose={() => setUpload(false)} onDone={(d) => { setUpload(false); setSel(d.id); reload(); }} />}
    </div>
  );
}

function Upload({ onClose, onDone }) {
  const { project: p } = useProject();
  const toast = useToast();
  const [name, setName] = useState("");
  const [files, setFiles] = useState([]);
  const [path, setPath] = useState("");
  const [busy, setBusy] = useState(false);
  const [src, setSrc] = useState("files");
  const [period, setPeriod] = useState({ from: "", to: "" });
  const [nt, setNt] = useState(null);
  const [cfg, setCfg] = useState(null);
  useEffect(() => { api.get("/config").then(setCfg).catch(() => setCfg({})); }, []);
  useEffect(() => {
    if (src !== "navtrace") return;
    setNt(null);
    const t = setTimeout(() => api.get(`/projects/${p.id}/navtrace?date_from=${period.from}&date_to=${period.to}`).then(setNt)
      .catch((e) => setNt({ ok: false, message: e.message })), 400);
    return () => clearTimeout(t);
  }, [src, period.from, period.to, p.id]);
  const go = async () => {
    const fd = new FormData();
    fd.append("name", name);
    if (src === "navtrace") {
      fd.append("source", "navtrace");
      fd.append("date_from", period.from);
      fd.append("date_to", period.to);
    } else {
      fd.append("server_path", path);
      [...files].forEach((f) => fd.append("files", f, f.webkitRelativePath || f.name));
    }
    setBusy(true);
    try { const d = await api.post(`/projects/${p.id}/datasets`, fd); toast("Loading started"); onDone(d); } catch (e) { toast(e.message, "error"); } finally { setBusy(false); }
  };
  return (
    <Modal title="Load surveillance tracks" onClose={onClose}>
      <div className="stack">
        {cfg?.navtrace && (
          <div className="chips">
            <button className={`chip ${src === "files" ? "on" : ""}`} onClick={() => setSrc("files")}>FR24 files</button>
            <button className={`chip ${src === "navtrace" ? "on" : ""}`} onClick={() => setSrc("navtrace")}>NavTrace database</button>
          </div>
        )}
        <label className="f">Dataset name<input value={name} onChange={(e) => setName(e.target.value)}
          placeholder={src === "navtrace" ? "e.g. NavTrace Aug – Oct 2026" : "e.g. FR24 Jun 2025 – Jun 2026"} /></label>
        {src === "files" ? (<>
          <label className="f">CSV files or a ZIP (one CSV per flight, FR24 columns)
            <input type="file" multiple accept=".csv,.zip" onChange={(e) => setFiles(e.target.files)} /></label>
          <label className="f">or a whole folder<input type="file" webkitdirectory="" directory="" multiple onChange={(e) => setFiles(e.target.files)} /></label>
          <label className="f">or a folder path on the server<input value={path} onChange={(e) => setPath(e.target.value)} placeholder="/data/Flights" /></label>
          <p className="muted small">Required columns: Timestamp, UTC, Callsign, Position ("lat,lon"), Altitude, Speed, Direction. File names like THA431-2025-07-01.csv set the flight identity.</p>
          <div className="row end"><button className="btn" disabled={busy || (!files.length && !path)} onClick={go}>{busy ? "Uploading…" : `Load ${files.length ? files.length + " files" : "folder"}`}</button></div>
        </>) : (<>
          <p className="small muted">Loads every {p.adep}–{p.ades} flight (both directions) recorded by NavTrace, from the ADS-B database on the AirNav network. A copy is stored with this dataset version, so later changes in NavTrace do not alter its results.</p>
          <div className="row">
            <label className="f">First date of flight<input type="date" value={period.from} onChange={(e) => setPeriod({ ...period, from: e.target.value })} /></label>
            <label className="f">Last date of flight<input type="date" value={period.to} onChange={(e) => setPeriod({ ...period, to: e.target.value })} /></label>
          </div>
          <p className="small">{!nt ? <span className="muted">Checking NavTrace…</span> : nt.ok
            ? <>NavTrace holds <b>{nt.flights}</b> flights for this pair{nt.flights ? <> ({Object.entries(nt.by_direction).map(([k, v]) => `${k.replace("-", " → ")}: ${v}`).join(", ")}; {nt.first} to {nt.last})</> : null}.</>
            : <span className="badge bad" style={{ whiteSpace: "normal" }}>{nt.message}</span>}</p>
          <div className="row end"><button className="btn" disabled={busy || !nt?.ok || !nt?.flights} onClick={go}>{busy ? "Starting…" : `Load ${nt?.flights || ""} flights from NavTrace`}</button></div>
        </>)}
      </div>
    </Modal>
  );
}

function DatasetDetail({ did, onDeleted }) {
  const { user } = useAuth();
  const toast = useToast();
  const { data: d, reload } = useAsync(() => api.get(`/datasets/${did}`), [did]);
  const { data: flights, reload: reloadF } = useAsync(() => api.get(`/datasets/${did}/flights`), [did]);
  const [tab, setTab] = useState("Quality");
  const [q, setQ] = useState("");
  useEffect(() => {
    if (d && d.status === "loading") { const t = setTimeout(() => { reload(); reloadF(); }, 2500); return () => clearTimeout(t); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d]);
  const list = useMemo(() => (flights || []).filter((f) => !q || f.fid.includes(q.toUpperCase())), [flights, q]);
  if (!d) return null;
  const qy = d.quality || {};
  const toggle = async (f) => {
    let reason = "";
    if (f.included) { reason = window.prompt(`Reason for excluding ${f.fid}`); if (!reason) return; }
    try { await api.patch(`/flights/${f.id}`, { included: !f.included, reason }); reloadF(); } catch (e) { toast(e.message, "error"); }
  };
  const del = async () => {
    if (!window.confirm("Delete this dataset?")) return;
    try { await api.del(`/datasets/${did}`); onDeleted(); } catch (e) { toast(e.message, "error"); }
  };
  return (
    <Panel title={`${d.name} (v${d.version})`} sub={`${d.n_flights} flights · ${d.included} included · plan matched for ${d.fpl_match?.matched ?? 0} · hash ${(d.content_hash || "").slice(0, 12)}`}
      actions={can(user, "analyst") && <button className="btn danger sm" onClick={del}>Delete dataset</button>}>
      <Tabs tabs={["Quality", "Flights", "Gaps map", "Load log"]} value={tab} onChange={setTab} />
      {d.status !== "ready" && <p className="muted">{d.message}</p>}
      {tab === "Quality" && d.status === "ready" && (
        <div className="stack">
          <div className="kpis">
            <div className="kpi"><div className="v">{qy.n_flights}</div><div className="l">flights</div></div>
            <div className="kpi"><div className="v">{pct(qy.gate_to_gate_share, 1)}</div><div className="l">gate-to-gate</div></div>
            <div className="kpi"><div className="v">{qy.gaps?.flights ?? "–"}</div><div className="l">flights with gaps over {qy.gaps?.threshold_s} s</div></div>
            <div className="kpi"><div className="v">{qy.gaps?.flights_major ?? "–"}</div><div className="l">flights with gaps over {qy.gaps?.major_threshold_s} s</div></div>
          </div>
          <h3>Coverage: flights per callsign per month</h3>
          {qy.coverage && (
            <div className="tablewrap"><table className="t"><thead><tr><th>Callsign</th>{qy.coverage.months.map((m) => <th key={m} className="num">{m.slice(2)}</th>)}</tr></thead>
              <tbody>{qy.coverage.callsigns.map((c, i) => (
                <tr key={c}><td><b>{c}</b></td>{qy.coverage.counts[i].map((n, j) => {
                  const mx = Math.max(...qy.coverage.counts[i]);
                  return <td key={j} className="num" style={{ background: n === 0 ? "var(--magenta-tint)" : n < mx * 0.5 ? "var(--amber-tint)" : undefined }}>{n}</td>;
                })}</tr>))}</tbody></table></div>
          )}
          <h3>Sampling interval by altitude</h3>
          <Table rows={qy.sampling || []} columns={[{ key: "band", label: "Altitude band" }, { label: "Median (s)", num: true, render: (r) => fmt(r.median_s, 0) },
            { label: "P90 (s)", num: true, render: (r) => fmt(r.p90_s, 0) }]} />
          <h3>Direction check</h3>
          <Table rows={qy.directions || []} columns={[{ key: "callsign", label: "Callsign" }, { key: "adep", label: "From" }, { key: "ades", label: "To" }, { key: "n", label: "Flights", num: true }]} />
        </div>
      )}
      {tab === "Flights" && (
        <div className="stack">
          <input placeholder="Filter by flight, e.g. THA431-2025-07" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 320 }} />
          <Table maxHeight={480} rows={list} columns={[{ key: "fid", label: "Flight" }, { label: "Route", render: (f) => `${f.adep}–${f.ades}` },
            { key: "n_points", label: "Points", num: true }, { key: "duration_min", label: "Minutes", num: true },
            { label: "Gate-to-gate", render: (f) => (f.gate_to_gate ? "yes" : <span className="badge warn">no</span>) },
            { label: "Flags", render: (f) => (f.flags || []).join(", ") },
            { label: "Included", render: (f) => (f.included ? <span className="badge good">yes</span> : <span className="badge bad" title={f.exclude_reason}>no: {f.exclude_reason}</span>) },
            ...(can(user, "analyst") ? [{ label: "", render: (f) => <button className="btn ghost sm" onClick={() => toggle(f)}>{f.included ? "Exclude" : "Include"}</button> }] : [])]} />
        </div>
      )}
      {tab === "Gaps map" && (
        <MapView deps={[d.id]} draw={(L, map, g) => {
          const pts = qy.gaps?.points || [];
          pts.forEach((x) => L.circleMarker([x.mlat, x.mlon], { radius: 4, color: x.dt > (qy.gaps.major_threshold_s || 600) ? "#B0306A" : "#D98E1F", weight: 1, fillOpacity: 0.7 })
            .bindTooltip(`${Math.round(x.dt)} s gap at ${Math.round(x.alt)} ft`).addTo(g));
          return pts.length ? L.latLngBounds(pts.map((x) => [x.mlat, x.mlon])) : null;
        }} />
      )}
      {tab === "Load log" && (
        <Table maxHeight={420} rows={d.load_log || []} columns={[{ key: "file", label: "File" }, { label: "Result", render: (r) => <StatusBadge s={r.result === "loaded" ? "done" : r.result === "error" ? "error" : "draft"} /> }, { key: "detail", label: "Detail" }]} />
      )}
    </Panel>
  );
}

function FlightPlans({ datasetId }) {
  const { project: p } = useProject();
  const { user } = useAuth();
  const toast = useToast();
  const { data: stats, reload } = useAsync(() => api.get(`/projects/${p.id}/fpl/stats`), [p.id]);
  const [api_, setApi] = useState(null);
  const [job, setJob] = useState(null);
  const [lookup, setLookup] = useState({ callsign: "", dof: "" });
  const [msgs, setMsgs] = useState(null);
  const uploadFile = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    try { const r = await api.post(`/projects/${p.id}/fpl/upload`, fd); toast(`${r.added} messages added, ${r.duplicates} duplicates, ${r.errors} unreadable`); reload(); } catch (x) { toast(x.message, "error"); }
    e.target.value = "";
  };
  const check = async () => setApi(await api.get("/fpl/api-status"));
  const fetchAll = async (force) => {
    try { const r = await api.post(`/projects/${p.id}/fpl/fetch`, { dataset_id: datasetId, force }); setJob({ id: r.job, status: "running" }); } catch (x) { toast(x.message, "error"); }
  };
  useEffect(() => {
    if (job && job.status === "running") {
      const t = setTimeout(async () => { const j = await api.get(`/jobs/${job.id}`); setJob({ id: job.id, ...j }); if (j.status !== "running") reload(); }, 2500);
      return () => clearTimeout(t);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job]);
  const find = async () => setMsgs(await api.get(`/projects/${p.id}/fpl?callsign=${lookup.callsign}&dof=${lookup.dof}`));
  return (
    <Panel title="Flight plans" sub="From AirNav FPL archive files, or per flight from the AirNav flight data API (VPN)">
      <div className="grid2">
        <div className="stack">
          <h3>Import a file</h3>
          <p className="muted small">JSON array or Parquet with id, callsign, route, dof, typeOfAircraft and the raw ICAO message in fpl.</p>
          {can(user, "analyst") && <input type="file" accept=".json,.parquet,.gzip" onChange={uploadFile} />}
          <h3>AirNav flight data API</h3>
          <div className="row">
            <button className="btn ghost" onClick={check}>Check connection</button>
            {can(user, "analyst") && <button className="btn" disabled={!datasetId || (job && job.status === "running")} onClick={() => fetchAll(false)}>Fetch plans for this dataset</button>}
            {can(user, "analyst") && <button className="btn ghost" disabled={!datasetId} onClick={() => fetchAll(true)}>Refresh all</button>}
          </div>
          {api_ && <div className={`badge ${api_.ok ? "good" : "bad"}`} style={{ whiteSpace: "normal" }}>{api_.ok ? `Reachable (${api_.base})` : api_.message}</div>}
          {job && (
            <div className="small">
              {job.status === "running" ? "Fetching flight plans in the background…" : job.result && (job.result.ok ?
                `Fetched ${job.result.fetched}, not found ${job.result.not_found}, errors ${job.result.errors}, from cache ${job.result.cached}.` : job.result.message)}
            </div>
          )}
        </div>
        <div className="stack">
          <h3>Loaded</h3>
          <Table rows={Object.entries(stats?.sources || {}).map(([k, v]) => ({ k, ...v }))} empty="No flight plans yet"
            columns={[{ key: "k", label: "Source" }, { key: "messages", label: "Messages", num: true }, { key: "flight_days", label: "Flights", num: true },
              { label: "Aircraft types", render: (r) => Object.entries(r.types).map(([t, n]) => `${t} ${n}`).join(", ") }]} />
          <h3>Look up a flight</h3>
          <div className="row">
            <input placeholder="Callsign" value={lookup.callsign} onChange={(e) => setLookup({ ...lookup, callsign: e.target.value })} style={{ width: 110 }} />
            <input type="date" value={lookup.dof} onChange={(e) => setLookup({ ...lookup, dof: e.target.value })} />
            <button className="btn ghost" onClick={find} disabled={!lookup.callsign}>Show messages</button>
          </div>
          {msgs && (msgs.length ? msgs.map((m) => (
            <div key={m.id}><div className="small"><b>{m.msg_type}</b> {m.source} · filed {m.filed_time || "?"} {m.status && `· ${m.status}`}</div>
              <pre className="raw">{m.raw || JSON.stringify(m.parsed, null, 1)}</pre></div>)) : <Empty title="No messages">Nothing stored for this flight.</Empty>)}
        </div>
      </div>
    </Panel>
  );
}
