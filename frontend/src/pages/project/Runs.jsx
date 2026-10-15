import React, { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, download, dt } from "../../api";
import { can, Panel, Progress, StatusBadge, Table, useAsync, useAuth, useToast } from "../../components/ui";
import { useProject } from "../ProjectShell";

export default function Runs() {
  const { project: p, runs, loadRuns, setRunId } = useProject();
  const { user } = useAuth();
  const toast = useToast();
  const nav = useNavigate();
  const { data: sets } = useAsync(() => api.get(`/projects/${p.id}/datasets`), [p.id]);
  const { data: params } = useAsync(() => api.get(`/projects/${p.id}/paramsets`), [p.id]);
  const [ds, setDs] = useState("");
  const [ps, setPs] = useState("");
  const [name, setName] = useState("");
  const [detail, setDetail] = useState(null);
  useEffect(() => {
    if (sets && !ds) { const r = sets.find((d) => d.status === "ready"); if (r) setDs(r.id); }
    if (params && !ps && params.length) setPs(params[0].id);
  }, [sets, params, ds, ps]);
  useEffect(() => {
    if (runs.some((r) => r.status === "running" || r.status === "queued")) { const t = setTimeout(loadRuns, 2000); return () => clearTimeout(t); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runs]);
  const start = async () => {
    try { await api.post(`/projects/${p.id}/runs`, { dataset_id: Number(ds), paramset_id: Number(ps), name }); toast("Run started"); setName(""); loadRuns(); } catch (e) { toast(e.message, "error"); }
  };
  const del = async (r) => {
    if (!window.confirm(`Delete run #${r.id} with its results, insights and reports?`)) return;
    try { await api.del(`/runs/${r.id}`); loadRuns(); } catch (e) { toast(e.message, "error"); }
  };
  const open = async (r) => setDetail(await api.get(`/runs/${r.id}`));
  return (
    <div>
      {can(user, "analyst") && (
        <Panel title="Start an analysis run" sub="A run records the dataset version, parameter set version and software version it used">
          <div className="formgrid">
            <label className="f">Dataset<select value={ds} onChange={(e) => setDs(e.target.value)}>
              {(sets || []).filter((d) => d.status === "ready").map((d) => <option key={d.id} value={d.id}>{d.name} (v{d.version}, {d.n_flights} flights)</option>)}</select></label>
            <label className="f">Parameter set<select value={ps} onChange={(e) => setPs(e.target.value)}>
              {(params || []).map((s) => <option key={s.id} value={s.id}>{s.name} v{s.version}</option>)}</select></label>
            <label className="f">Run name (optional)<input value={name} onChange={(e) => setName(e.target.value)} /></label>
          </div>
          <div className="row end" style={{ marginTop: 10 }}><button className="btn" disabled={!ds || !ps} onClick={start}>Start run</button></div>
        </Panel>
      )}
      <Panel title="Runs">
        <Table rows={runs} empty="No runs yet" onRow={open} columns={[
          { label: "#", render: (r) => r.id }, { key: "name", label: "Name" }, { label: "Status", render: (r) => <StatusBadge s={r.status} /> },
          { label: "Progress", render: (r) => (r.status === "running" || r.status === "queued" ? <div style={{ minWidth: 140 }}><Progress value={r.progress} /><div className="small muted">{r.message}</div></div> : r.status === "error" ? <span className="small">{(r.message || "").split("\n")[0]}</span> : "") },
          { key: "dataset", label: "Dataset" }, { key: "paramset", label: "Parameters" }, { key: "user", label: "By" }, { label: "Finished", render: (r) => dt(r.finished_at) },
          { label: "", render: (r) => (<div className="row" onClick={(e) => e.stopPropagation()}>
            {r.status === "done" && <button className="btn ghost sm" onClick={() => { setRunId(r.id); nav(`/projects/${p.id}/results`); }}>Results</button>}
            {r.status === "done" && <button className="btn ghost sm" onClick={() => download(`/runs/${r.id}/export?fmt=xlsx`)}>Excel</button>}
            {r.status === "done" && <button className="btn ghost sm" onClick={() => download(`/runs/${r.id}/export?fmt=csv`)}>CSV</button>}
            {can(user, "analyst") && r.status !== "running" && <button className="btn danger sm" onClick={() => del(r)}>Delete</button>}</div>) }]} />
      </Panel>
      {detail && (
        <Panel title={`Run #${detail.id} record`} sub={`CPTEA ${detail.software_version} · started ${dt(detail.started_at)} · finished ${dt(detail.finished_at)}`}
          actions={<button className="btn ghost sm" onClick={() => setDetail(null)}>Close</button>}>
          <div className="grid2">
            <div>
              <h3>Inputs</h3>
              <Table rows={[{ k: "Dataset", v: detail.dataset }, { k: "Parameter set", v: detail.paramset }, { k: "Earth model", v: detail.params?.["GEN-01"] },
                { k: "Flights loaded / included", v: `${detail.meta?.n_loaded ?? "–"} / ${detail.meta?.n_included ?? "–"}` },
                { k: "With plan / with reference route", v: `${detail.meta?.n_with_plan ?? "–"} / ${detail.meta?.n_ref ?? "–"}` }]}
                columns={[{ key: "k", label: "" }, { key: "v", label: "" }]} />
            </div>
            <div>
              <h3>Unresolved flight plan fixes</h3>
              <Table maxHeight={260} rows={Object.entries(detail.meta?.unresolved_fixes || {}).map(([k, v]) => ({ k, v })).sort((a, b) => b.v - a.v)}
                empty="All fixes resolved" columns={[{ key: "k", label: "Fix or route element" }, { key: "v", label: "Flights", num: true }]} />
              <p className="muted small">Add missing fixes under Aeronautical data. "Route end" fixes (for example a STAR entry) do not block the reference route.</p>
            </div>
          </div>
          {detail.meta?.crosstab?.length > 0 && (<>
            <h3 style={{ marginTop: 10 }}>Choosing k: clusters against filed routes</h3>
            <Table rows={detail.meta.crosstab} columns={[{ key: "dirn", label: "Direction" }, { key: "k", label: "k", num: true },
              { label: "Purity", num: true, render: (r) => `${(100 * r.purity).toFixed(0)}%` }, { label: "Cluster sizes", render: (r) => r.sizes.join(", ") }]} />
            <p className="muted small">Purity is the share of flights whose cluster's most common filed route is their own. Choose the smallest k after which purity stops rising.</p>
          </>)}
          {detail.status === "error" && <pre className="raw">{detail.message}</pre>}
        </Panel>
      )}
    </div>
  );
}
