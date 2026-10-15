import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../../api";
import { can, Empty, Panel, StatusBadge, useAsync, useAuth, useToast } from "../../components/ui";
import { RunPicker, useProject } from "../ProjectShell";

export default function Insights() {
  const { project: p, runId } = useProject();
  const { user } = useAuth();
  const { data, reload, setData } = useAsync(() => (runId ? api.get(`/runs/${runId}/insights`) : Promise.resolve([])), [runId]);
  const [add, setAdd] = useState({ title: "", text: "", kind: "insight" });
  const toast = useToast();
  const edit = can(user, "analyst");
  const update = async (i, body) => {
    try { const r = await api.patch(`/insights/${i.id}`, body); setData((d) => d.map((x) => (x.id === r.id ? r : x))); } catch (e) { toast(e.message, "error"); }
  };
  const create = async () => {
    try { await api.post(`/runs/${runId}/insights`, add); setAdd({ title: "", text: "", kind: "insight" }); reload(); toast("Added"); } catch (e) { toast(e.message, "error"); }
  };
  const ins = (data || []).filter((i) => i.kind === "insight");
  const recs = (data || []).filter((i) => i.kind === "recommendation");
  return (
    <div>
      <div className="row" style={{ marginBottom: 12 }}><RunPicker /></div>
      <Panel title="Insights" sub="Drafted by rules from the run's numbers. Accept, edit or reject each one; only accepted and edited insights go into reports. Editing the text never changes the numbers.">
        {data && !data.length && <Empty title="No insights">No rule fired for this run.</Empty>}
        {ins.map((i) => <Item key={i.id} i={i} edit={edit} update={update} pid={p.id} />)}
      </Panel>
      <Panel title="Recommendations" sub="Free text written by analysts">
        {recs.map((i) => <Item key={i.id} i={i} edit={edit} update={update} pid={p.id} onDelete={async () => { await api.del(`/insights/${i.id}`); reload(); }} />)}
        {edit && (
          <div className="stack" style={{ marginTop: 10 }}>
            <div className="row">
              <select value={add.kind} onChange={(e) => setAdd({ ...add, kind: e.target.value })}><option value="insight">Insight</option><option value="recommendation">Recommendation</option></select>
              <input placeholder="Title" value={add.title} onChange={(e) => setAdd({ ...add, title: e.target.value })} style={{ flex: 1 }} />
            </div>
            <textarea placeholder="Text" value={add.text} onChange={(e) => setAdd({ ...add, text: e.target.value })} />
            <div className="row end"><button className="btn" disabled={!add.text} onClick={create}>Add {add.kind}</button></div>
          </div>
        )}
      </Panel>
    </div>
  );
}

function Item({ i, edit, update, pid, onDelete }) {
  const [text, setText] = useState(i.text);
  const [editing, setEditing] = useState(false);
  const nav = useNavigate();
  return (
    <div className={`insight ${i.status === "rejected" ? "rejected" : ""}`}>
      <div className="row" style={{ justifyContent: "space-between" }}>
        <div className="t">{i.title || "(untitled)"}</div>
        <div className="row"><span className="small muted">{i.rule}</span><StatusBadge s={i.status} /></div>
      </div>
      {editing ? <textarea value={text} onChange={(e) => setText(e.target.value)} style={{ width: "100%" }} /> : <p>{i.text}</p>}
      {i.edited && i.drafted && !editing && <details className="small muted"><summary>Original draft</summary>{i.drafted}</details>}
      <div className="row">
        {i.chart && <button className="btn ghost sm" onClick={() => nav(`/projects/${pid}/results#${i.chart}`)}>Open chart</button>}
        {edit && !editing && <>
          {i.status !== "accepted" && i.status !== "edited" && <button className="btn ghost sm" onClick={() => update(i, { status: "accepted" })}>Accept</button>}
          <button className="btn ghost sm" onClick={() => setEditing(true)}>Edit</button>
          {i.status !== "rejected" && <button className="btn ghost sm" onClick={() => update(i, { status: "rejected" })}>Reject</button>}
          {onDelete && <button className="btn danger sm" onClick={onDelete}>Delete</button>}
        </>}
        {editing && <>
          <button className="btn sm" onClick={() => { update(i, { edited: text, status: "edited" }); setEditing(false); }}>Save text</button>
          <button className="btn ghost sm" onClick={() => { setText(i.text); setEditing(false); }}>Cancel</button></>}
        {i.editor && <span className="small muted">last changed by {i.editor}</span>}
      </div>
    </div>
  );
}
