import React, { useEffect, useState } from "react";
import { api, download, dt } from "../../api";
import { can, Panel, StatusBadge, Table, useAsync, useAuth, useToast } from "../../components/ui";
import { RunPicker, useProject } from "../ProjectShell";

export default function Reports() {
  const { project: p, runId } = useProject();
  const { user } = useAuth();
  const toast = useToast();
  const { data: sections } = useAsync(() => api.get("/report-sections"), []);
  const { data: reps, reload } = useAsync(() => api.get(`/projects/${p.id}/reports`), [p.id]);
  const [f, setF] = useState({ title: `${p.adep}–${p.ades} trajectory efficiency analysis`, authors: "", formats: ["pdf", "docx", "pptx"], include_drafts: false, logo_path: "" });
  const [sel, setSel] = useState(null);
  const [filters, setFilters] = useState(true);
  const [kind, setKind] = useState("standard");
  useEffect(() => { if (sections && !sel) setSel(sections.map((s) => s.id)); }, [sections, sel]);
  useEffect(() => {
    if (reps && reps.some((r) => r.generation === "generating")) { const t = setTimeout(reload, 2500); return () => clearTimeout(t); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reps]);
  const toggle = (arr, v) => (arr.includes(v) ? arr.filter((x) => x !== v) : [...arr, v]);
  const logo = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file);
    const r = await api.post("/report-logo", fd);
    setF({ ...f, logo_path: r.logo_path });
  };
  const gen = async () => {
    let flt = {};
    if (filters) { try { flt = JSON.parse(localStorage.getItem(`cptea_filters_${p.id}`) || "{}"); } catch { flt = {}; } }
    const stdTitle = `${p.adep}–${p.ades} trajectory efficiency analysis`;
    const body = kind === "explained" ? { kind, title: f.title === stdTitle ? "" : f.title, authors: f.authors, logo_path: f.logo_path, filters: flt }
      : { ...f, kind, sections: sel, filters: flt };
    try { await api.post(`/runs/${runId}/reports`, body); toast("Generating the report"); reload(); } catch (e) { toast(e.message, "error"); }
  };
  const status = async (r, s) => { try { await api.patch(`/reports/${r.id}`, { status: s }); reload(); } catch (e) { toast(e.message, "error"); } };
  return (
    <div>
      {can(user, "analyst") && (
        <Panel title="Generate a report" sub="Every number comes from the selected run; charts are drawn from the same data as the Results page">
          <div className="row" style={{ marginBottom: 10 }}><RunPicker /></div>
          <div className="formgrid">
            <label className="f" style={{ gridColumn: "span 2" }}>Title<input value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} /></label>
            <label className="f">Authors<input value={f.authors} onChange={(e) => setF({ ...f, authors: e.target.value })} placeholder="AirNav Indonesia" /></label>
            <label className="f">Logo (optional)<input type="file" accept="image/*" onChange={logo} /></label>
          </div>
          <h3 style={{ marginTop: 12 }}>Report type</h3>
          <div className="chips">
            <button className={`chip ${kind === "standard" ? "on" : ""}`} onClick={() => setKind("standard")}>Analysis report</button>
            <button className={`chip ${kind === "explained" ? "on" : ""}`} onClick={() => setKind("explained")}>Plain-language explained report</button>
          </div>
          {kind === "explained" && <p className="small muted" style={{ marginTop: 6 }}>One PDF that goes through every table and chart of the run and explains, in everyday words, what it shows, how to read it and what it says about these flights. It uses the reviewed insights, and CPTEA's proposed ones where a section has none.</p>}
          {kind === "standard" && <>
          <h3 style={{ marginTop: 12 }}>Sections</h3>
          <div className="chips">{(sections || []).map((s) => <button key={s.id} className={`chip ${sel?.includes(s.id) ? "on" : ""}`} onClick={() => setSel(toggle(sel, s.id))}>{s.title}</button>)}</div>
          <h3 style={{ marginTop: 12 }}>Formats</h3>
          <div className="chips">{[["pdf", "PDF"], ["docx", "Word"], ["pptx", "PowerPoint summary"]].map(([k, l]) => <button key={k} className={`chip ${f.formats.includes(k) ? "on" : ""}`} onClick={() => setF({ ...f, formats: toggle(f.formats, k) })}>{l}</button>)}</div>
          </>}
          <div className="row" style={{ marginTop: 12 }}>
            <label className="row small"><input type="checkbox" checked={filters} onChange={(e) => setFilters(e.target.checked)} /> Use the filters set on the Results page</label>
            {kind === "standard" && <label className="row small"><input type="checkbox" checked={f.include_drafts} onChange={(e) => setF({ ...f, include_drafts: e.target.checked })} /> Include insights not yet accepted</label>}
          </div>
          <div className="row end"><button className="btn" disabled={!runId || (kind === "standard" && (!f.formats.length || !sel?.length))} onClick={gen}>Generate report</button></div>
        </Panel>
      )}
      <Panel title="Reports" sub="Draft, then In review, then Released. Only a Reviewer releases a report, and a released report is locked.">
        <Table rows={reps || []} empty="No reports yet" columns={[
          { label: "#", render: (r) => r.id }, { label: "Title", render: (r) => <>{r.title}{r.kind === "explained" && <> <span className="badge">plain language</span></>}</> }, { label: "Run", render: (r) => `#${r.run_id}` },
          { label: "Status", render: (r) => <StatusBadge s={r.status} /> },
          { label: "Files", render: (r) => (r.generation === "generating" ? <span className="muted">generating…</span> : r.generation.startsWith("error") ? <span className="badge bad" title={r.generation}>failed</span> :
            <div className="row">{r.formats.map((x) => <button key={x} className="btn ghost sm" onClick={() => download(`/reports/${r.id}/download/${x}`)}>{x.toUpperCase()}</button>)}</div>) },
          { key: "created_by", label: "By" }, { label: "Created", render: (r) => dt(r.created_at) }, { key: "released_by", label: "Released by" },
          { label: "", render: (r) => (<div className="row">
            {r.status === "draft" && can(user, "analyst") && <button className="btn ghost sm" onClick={() => status(r, "in_review")}>Send for review</button>}
            {r.status === "in_review" && can(user, "reviewer") && user.role !== "analyst" && <button className="btn sm" onClick={() => status(r, "released")}>Release</button>}
            {r.status === "in_review" && can(user, "reviewer") && <button className="btn ghost sm" onClick={() => status(r, "draft")}>Back to draft</button>}</div>) }]} />
      </Panel>
    </div>
  );
}
