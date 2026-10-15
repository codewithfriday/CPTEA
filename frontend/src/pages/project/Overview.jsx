import React, { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api } from "../../api";
import { can, Modal, Panel, Table, useAsync, useAuth, useToast } from "../../components/ui";
import { useProject } from "../ProjectShell";

export default function Overview() {
  const { project: p, reload, runs } = useProject();
  const { user } = useAuth();
  const toast = useToast();
  const nav = useNavigate();
  const { data: ds } = useAsync(() => api.get(`/projects/${p.id}/datasets`), [p.id]);
  const [edit, setEdit] = useState(false);
  const [share, setShare] = useState(false);
  const last = runs.find((r) => r.status === "done");
  const steps = [
    { done: (ds || []).some((d) => d.status === "ready"), label: "Load surveillance tracks", to: "data" },
    { done: (ds || []).length > 0, label: "Import or fetch flight plans", to: "data" },
    { done: true, label: "Review the parameters", to: "settings" },
    { done: runs.some((r) => r.status === "done"), label: "Run the analysis", to: "runs" },
    { done: false, label: "Review insights and generate the report", to: "insights" },
  ];
  const del = async () => {
    if (!window.confirm(`Delete project ${p.name} with all its data, runs and reports?`)) return;
    try { await api.del(`/projects/${p.id}`); toast("Project deleted"); nav("/"); } catch (e) { toast(e.message, "error"); }
  };
  return (
    <div>
      <div className="grid2">
        <Panel title="Workflow" sub="Each step opens its page">
          <ol style={{ margin: 0, paddingLeft: 20 }}>
            {steps.map((s) => (
              <li key={s.label} style={{ marginBottom: 6 }}>
                <Link to={`/projects/${p.id}/${s.to}`}>{s.label}</Link> {s.done && <span className="badge good">done</span>}
              </li>
            ))}
          </ol>
        </Panel>
        <Panel title="Project" actions={can(user, "analyst") && <>
          <button className="btn ghost sm" onClick={() => setEdit(true)}>Edit</button>
          <button className="btn ghost sm" onClick={() => setShare(true)}>Share</button>
          <button className="btn danger sm" onClick={del}>Delete</button></>}>
          <Table columns={[{ key: "k", label: "Field" }, { key: "v", label: "Value" }]} rows={[
            { k: "Aerodromes", v: `${p.adep} (${p.adep_lat.toFixed(4)}, ${p.adep_lon.toFixed(4)}) – ${p.ades} (${p.ades_lat.toFixed(4)}, ${p.ades_lon.toFixed(4)})` },
            { k: "Callsigns", v: Object.entries(p.callsigns || {}).map(([c, d]) => `${c} ${d}`).join(", ") || "–" },
            { k: "Period", v: p.period_start ? `${p.period_start} to ${p.period_end}` : "–" },
            { k: "Owner", v: p.owner },
            { k: "Shared with", v: p.members.map((m) => `${m.username} (${m.level})`).join(", ") || "nobody" },
          ]} />
        </Panel>
      </div>
      {last && last.summary && (
        <Panel title={`Latest results: run #${last.id}`} sub={last.name} actions={<Link className="btn ghost sm" to={`/projects/${p.id}/results`}>Open results</Link>}>
          <div className="kpis">
            {(last.summary.kpis || []).map((k) => (
              <div key={k.id} className="kpi"><div className="v">{k.value}</div><div className="u">{k.unit}</div><div className="l">{k.label}</div></div>
            ))}
          </div>
        </Panel>
      )}
      {edit && <EditProject p={p} onClose={() => setEdit(false)} onDone={() => { setEdit(false); reload(); }} />}
      {share && <Share p={p} onClose={() => setShare(false)} onDone={reload} />}
    </div>
  );
}

function EditProject({ p, onClose, onDone }) {
  const toast = useToast();
  const [f, setF] = useState({ ...p, callsigns_text: Object.entries(p.callsigns || {}).map(([c, d]) => `${c}=${d}`).join("\n") });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const save = async () => {
    const callsigns = {};
    f.callsigns_text.split(/\n|,/).map((x) => x.trim()).filter(Boolean).forEach((l) => { const [c, d] = l.split("="); if (c && d) callsigns[c.trim()] = d.trim(); });
    try {
      await api.patch(`/projects/${p.id}`, { name: f.name, description: f.description, period_start: f.period_start, period_end: f.period_end,
        adep_lat: Number(f.adep_lat), adep_lon: Number(f.adep_lon), ades_lat: Number(f.ades_lat), ades_lon: Number(f.ades_lon), callsigns });
      toast("Project saved");
      onDone();
    } catch (e) { toast(e.message, "error"); }
  };
  return (
    <Modal title="Edit project" onClose={onClose}>
      <div className="stack">
        <div className="formgrid">
          <label className="f">Name<input value={f.name} onChange={set("name")} /></label>
          <label className="f">Description<input value={f.description} onChange={set("description")} /></label>
          <label className="f">Period start<input type="date" value={f.period_start} onChange={set("period_start")} /></label>
          <label className="f">Period end<input type="date" value={f.period_end} onChange={set("period_end")} /></label>
          <label className="f">{p.adep} latitude<input value={f.adep_lat} onChange={set("adep_lat")} /></label>
          <label className="f">{p.adep} longitude<input value={f.adep_lon} onChange={set("adep_lon")} /></label>
          <label className="f">{p.ades} latitude<input value={f.ades_lat} onChange={set("ades_lat")} /></label>
          <label className="f">{p.ades} longitude<input value={f.ades_lon} onChange={set("ades_lon")} /></label>
        </div>
        <label className="f">Callsigns, one per line as CALLSIGN=ADEP-ADES<textarea value={f.callsigns_text} onChange={set("callsigns_text")} /></label>
        <div className="row end"><button className="btn" onClick={save}>Save project</button></div>
      </div>
    </Modal>
  );
}

function Share({ p, onClose, onDone }) {
  const toast = useToast();
  const { data: users } = useAsync(() => api.get("/users/directory"), []);
  const [uid, setUid] = useState("");
  const [level, setLevel] = useState("viewer");
  const add = async () => {
    try { await api.post(`/projects/${p.id}/members`, { user_id: Number(uid), level }); toast("Project shared"); onDone(); } catch (e) { toast(e.message, "error"); }
  };
  const rm = async (u) => { await api.del(`/projects/${p.id}/members/${u}`); onDone(); };
  return (
    <Modal title="Share project" onClose={onClose}>
      <div className="row">
        <select value={uid} onChange={(e) => setUid(e.target.value)}>
          <option value="">Choose a user</option>
          {(users || []).map((u) => <option key={u.id} value={u.id}>{u.full_name || u.username} ({u.role})</option>)}
        </select>
        <select value={level} onChange={(e) => setLevel(e.target.value)}>
          <option value="viewer">Viewer</option><option value="reviewer">Reviewer</option><option value="analyst">Analyst</option>
        </select>
        <button className="btn" disabled={!uid} onClick={add}>Share</button>
      </div>
      <div style={{ marginTop: 12 }}>
        <Table columns={[{ key: "username", label: "User" }, { key: "level", label: "Access" },
          { label: "", render: (m) => <button className="btn ghost sm" onClick={() => rm(m.user_id)}>Remove</button> }]} rows={p.members} empty="Not shared yet" />
      </div>
    </Modal>
  );
}
