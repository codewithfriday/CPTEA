import React, { createContext, useContext, useEffect, useState } from "react";
import { Route, Routes, useParams } from "react-router-dom";
import { api } from "../api";
import { useAsync } from "../components/ui";
import Overview from "./project/Overview";
import Data from "./project/Data";
import Settings from "./project/Settings";
import Runs from "./project/Runs";
import Results from "./project/Results";
import FlightView from "./project/FlightView";
import Insights from "./project/Insights";
import Reports from "./project/Reports";
import Compare from "./project/Compare";

const Ctx = createContext(null);
export const useProject = () => useContext(Ctx);

export default function ProjectShell() {
  const { pid } = useParams();
  const { data: project, reload, error } = useAsync(() => api.get(`/projects/${pid}`), [pid]);
  const key = `cptea_run_${pid}`;
  const [runId, setRunIdState] = useState(() => {
    try { return Number(localStorage.getItem(key)) || null; } catch { return null; }
  });
  const setRunId = (id) => {
    setRunIdState(id);
    try { localStorage.setItem(key, String(id || "")); } catch { /* per-viewer convenience only */ }
  };
  const [runs, setRuns] = useState([]);
  const loadRuns = async () => {
    const r = await api.get(`/projects/${pid}/runs`);
    setRuns(r);
    if ((!runId || !r.find((x) => x.id === runId)) && r.find((x) => x.status === "done")) setRunId(r.find((x) => x.status === "done").id);
    return r;
  };
  useEffect(() => { loadRuns().catch(() => {}); /* eslint-disable-next-line */ }, [pid]);
  if (error) return <div className="panel">This project could not be opened: {error.message}</div>;
  if (!project) return null;
  return (
    <Ctx.Provider value={{ project, reload, runId, setRunId, runs, loadRuns }}>
      <Ribbon p={project} />
      <Routes>
        <Route index element={<Overview />} />
        <Route path="data" element={<Data />} />
        <Route path="settings" element={<Settings />} />
        <Route path="runs" element={<Runs />} />
        <Route path="results" element={<Results />} />
        <Route path="flight" element={<FlightView />} />
        <Route path="flight/:fid" element={<FlightView />} />
        <Route path="insights" element={<Insights />} />
        <Route path="reports" element={<Reports />} />
        <Route path="compare" element={<Compare />} />
      </Routes>
    </Ctx.Provider>
  );
}

function Ribbon({ p }) {
  // the city pair drawn as a route ribbon: aerodromes, their rings, and the corridor between them
  return (
    <div className="ribbon">
      <span className="ap">{p.adep}</span>
      <svg viewBox="0 0 400 40" preserveAspectRatio="none" aria-hidden="true">
        <circle cx="16" cy="20" r="14" fill="none" stroke="#5FB3BE" strokeDasharray="3 4" />
        <circle cx="384" cy="20" r="14" fill="none" stroke="#5FB3BE" strokeDasharray="3 4" />
        <path d="M16 20 C120 6 280 34 384 20" stroke="#D98E1F" strokeWidth="2.5" fill="none" />
        <circle cx="16" cy="20" r="3.5" fill="#13283A" />
        <circle cx="384" cy="20" r="3.5" fill="#13283A" />
      </svg>
      <span className="ap">{p.ades}</span>
      <div className="meta">{p.name}{p.period_start ? `, ${p.period_start} to ${p.period_end}` : ""}</div>
    </div>
  );
}

export function RunPicker({ onlyDone = true }) {
  const { runs, runId, setRunId } = useProject();
  const list = runs.filter((r) => !onlyDone || r.status === "done");
  if (!list.length) return null;
  return (
    <label className="f">Analysis run
      <select value={runId || ""} onChange={(e) => setRunId(Number(e.target.value))}>
        {list.map((r) => <option key={r.id} value={r.id}>#{r.id} {r.name}</option>)}
      </select>
    </label>
  );
}
