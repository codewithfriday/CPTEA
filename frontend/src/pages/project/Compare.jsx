import React, { useState } from "react";
import { api, fmt } from "../../api";
import { Empty, Panel, Table, useToast } from "../../components/ui";
import { useProject } from "../ProjectShell";

export default function Compare() {
  const { runs } = useProject();
  const done = runs.filter((r) => r.status === "done");
  const [a, setA] = useState(done[1]?.id || "");
  const [b, setB] = useState(done[0]?.id || "");
  const [res, setRes] = useState(null);
  const toast = useToast();
  const go = async () => { try { setRes(await api.get(`/runs/compare/${a}/${b}`)); } catch (e) { toast(e.message, "error"); } };
  if (done.length < 2) return <div className="panel"><Empty title="Two finished runs needed">Run the analysis again with another parameter set, then compare the two here.</Empty></div>;
  const opt = (r) => <option key={r.id} value={r.id}>#{r.id} {r.name}</option>;
  return (
    <div>
      <Panel title="Compare two runs" sub="Headline metrics side by side, and the parameters that differ">
        <div className="row">
          <label className="f">Run A<select value={a} onChange={(e) => setA(e.target.value)}>{done.map(opt)}</select></label>
          <label className="f">Run B<select value={b} onChange={(e) => setB(e.target.value)}>{done.map(opt)}</select></label>
          <button className="btn" disabled={!a || !b || a === b} onClick={go} style={{ alignSelf: "flex-end" }}>Compare</button>
        </div>
      </Panel>
      {res && (
        <div className="grid2">
          <Panel title="Metrics">
            <Table rows={res.metrics} columns={[{ key: "metric", label: "Metric" }, { key: "unit", label: "Unit" }, { label: "A", num: true, render: (r) => fmt(r.a) },
              { label: "B", num: true, render: (r) => fmt(r.b) }, { label: "B − A", num: true, render: (r) => (r.diff == null ? "–" : <b style={{ color: r.diff ? "var(--teal-2)" : undefined }}>{fmt(r.diff)}</b>) }]} />
          </Panel>
          <Panel title="Parameters that differ">
            <Table rows={res.parameters} empty="Same parameters" columns={[{ key: "parameter", label: "Parameter" }, { label: "A", render: (r) => JSON.stringify(r.a) }, { label: "B", render: (r) => JSON.stringify(r.b) }]} />
          </Panel>
        </div>
      )}
    </div>
  );
}
