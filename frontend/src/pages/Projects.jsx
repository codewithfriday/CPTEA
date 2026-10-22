import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, dt } from "../api";
import { can, Empty, Modal, useAsync, useAuth, useToast } from "../components/ui";

export default function Projects() {
  const { user } = useAuth();
  const { data, reload } = useAsync(() => api.get("/projects"), []);
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  return (
    <div>
      <div className="row" style={{ justifyContent: "space-between", marginBottom: 16 }}>
        <h1>Projects</h1>
        {can(user, "analyst") && <button className="btn" onClick={() => setOpen(true)}>New project</button>}
      </div>
      {data && data.length === 0 && (
        <div className="panel">
          <Empty title="No projects yet">
            {can(user, "analyst") ? "Create a project for a city pair, then load its tracks and flight plans."
              : "Ask an analyst to share a project with you."}
          </Empty>
        </div>
      )}
      <div className="grid2">
        {(data || []).map((p) => (
          <button key={p.id} className="panel" style={{ textAlign: "left", cursor: "pointer", font: "inherit" }} onClick={() => nav(`/projects/${p.id}`)}>
            <div className="row" style={{ justifyContent: "space-between" }}>
              <span style={{ fontFamily: "var(--font-cond)", fontSize: 26, fontWeight: 700, letterSpacing: ".04em" }}>{p.adep} – {p.ades}</span>
              <span className="muted small">{p.n_datasets} datasets · {p.n_runs} runs</span>
            </div>
            <div style={{ fontWeight: 600 }}>{p.name}</div>
            <div className="muted small">{p.description || "No description"}</div>
            <div className="muted small" style={{ marginTop: 6 }}>Owner {p.owner} · updated {dt(p.updated_at)}</div>
          </button>
        ))}
      </div>
      {open && <NewProject onClose={() => setOpen(false)} onDone={(p) => { setOpen(false); reload(); nav(`/projects/${p.id}/data`); }} />}
    </div>
  );
}

function NewProject({ onClose, onDone }) {
  const toast = useToast();
  const { data: sets } = useAsync(() => api.get("/paramsets/system"), []);
  const [f, setF] = useState({ name: "", description: "", adep: "", ades: "", adep_lat: "", adep_lon: "", ades_lat: "", ades_lon: "",
    period_start: "", period_end: "" });
  const [cs, setCs] = useState([{ callsign: "", dir: "out" }]);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const vtbs = () => {
    setF({ name: "VTBS–WADD", description: "Bangkok Suvarnabhumi – Bali", adep: "VTBS", ades: "WADD", adep_lat: 13.685833, adep_lon: 100.748889,
      ades_lat: -8.7475, ades_lon: 115.169167, period_start: "2025-06-01", period_end: "2026-06-30" });
    setCs([{ callsign: "THA431", dir: "out" }, { callsign: "THA439", dir: "out" }, { callsign: "THA432", dir: "back" }, { callsign: "THA440", dir: "back" }]);
  };
  const save = async () => {
    const num = (v) => (v === "" ? null : Number(v));
    const callsigns = {};
    cs.filter((c) => c.callsign).forEach((c) => {
      callsigns[c.callsign.toUpperCase()] = c.dir === "out" ? `${f.adep.toUpperCase()}-${f.ades.toUpperCase()}` : `${f.ades.toUpperCase()}-${f.adep.toUpperCase()}`;
    });
    try {
      const p = await api.post("/projects", { ...f, adep_lat: num(f.adep_lat), adep_lon: num(f.adep_lon), ades_lat: num(f.ades_lat),
        ades_lon: num(f.ades_lon), callsigns, copy_paramset_id: sets && sets.length ? sets[sets.length - 1].id : null });
      toast("Project created");
      onDone(p);
    } catch (e) { toast(e.message, "error"); }
  };
  return (
    <Modal title="New project" onClose={onClose}>
      <div className="stack">
        <div className="row"><button className="btn ghost sm" onClick={vtbs}>Fill in the VTBS–WADD example</button></div>
        <div className="formgrid">
          <label className="f">Name<input value={f.name} onChange={set("name")} /></label>
          <label className="f">Description<input value={f.description} onChange={set("description")} /></label>
          <label className="f">ADEP (ICAO)<input value={f.adep} maxLength={4} onChange={set("adep")} /></label>
          <label className="f">ADES (ICAO)<input value={f.ades} maxLength={4} onChange={set("ades")} /></label>
          <label className="f">ADEP latitude<input value={f.adep_lat} onChange={set("adep_lat")} placeholder="from AIS if blank" /></label>
          <label className="f">ADEP longitude<input value={f.adep_lon} onChange={set("adep_lon")} /></label>
          <label className="f">ADES latitude<input value={f.ades_lat} onChange={set("ades_lat")} placeholder="from AIS if blank" /></label>
          <label className="f">ADES longitude<input value={f.ades_lon} onChange={set("ades_lon")} /></label>
          <label className="f">Period start<input type="date" value={f.period_start} onChange={set("period_start")} /></label>
          <label className="f">Period end<input type="date" value={f.period_end} onChange={set("period_end")} /></label>
        </div>
        <h3>Callsigns per direction</h3>
        {cs.map((c, i) => (
          <div className="row" key={i}>
            <input value={c.callsign} placeholder="Callsign" onChange={(e) => setCs(cs.map((x, j) => (j === i ? { ...x, callsign: e.target.value } : x)))} />
            <select value={c.dir} onChange={(e) => setCs(cs.map((x, j) => (j === i ? { ...x, dir: e.target.value } : x)))}>
              <option value="out">{f.adep || "ADEP"} to {f.ades || "ADES"}</option>
              <option value="back">{f.ades || "ADES"} to {f.adep || "ADEP"}</option>
            </select>
            <button className="btn ghost sm" onClick={() => setCs(cs.filter((_, j) => j !== i))}>Remove</button>
          </div>
        ))}
        <div className="row"><button className="btn ghost sm" onClick={() => setCs([...cs, { callsign: "", dir: "out" }])}>Add callsign</button></div>
        <p className="muted small">The project starts with a copy of the system default parameter set (VTBS–WADD values). You can change it under Settings.</p>
        <div className="row end"><button className="btn" onClick={save} disabled={!f.name || f.adep.length !== 4 || f.ades.length !== 4}>Create project</button></div>
      </div>
    </Modal>
  );
}
