import React, { useState } from "react";
import { api, download, dt } from "../../api";
import { Panel, Table, useAsync } from "../../components/ui";

export default function Audit() {
  const [f, setF] = useState({ action: "", username: "" });
  const { data, reload } = useAsync(() => api.get(`/audit?limit=1000&action=${encodeURIComponent(f.action)}&username=${encodeURIComponent(f.username)}`), [f.action, f.username]);
  return (
    <div className="page">
      <h1 style={{ marginBottom: 14 }}>Audit log</h1>
      <Panel title="Audit log" sub="Sign-ins, data loads, parameter changes, runs, exclusions, insight edits and report releases. Entries cannot be edited."
        actions={<button className="btn ghost sm" onClick={() => download("/audit.csv", "cptea_audit.csv")}>Download CSV</button>}>
        <div className="row" style={{ marginBottom: 10 }}>
          <input placeholder="Action contains…" value={f.action} onChange={(e) => setF({ ...f, action: e.target.value })} />
          <input placeholder="Username" value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} />
          <button className="btn ghost sm" onClick={reload}>Refresh</button>
        </div>
        <Table maxHeight={640} rows={data || []} columns={[
          { label: "Time", render: (r) => dt(r.ts) }, { key: "username", label: "User" }, { key: "action", label: "Action" },
          { label: "Object", render: (r) => `${r.obj_type || ""} ${r.obj_id || ""}` },
          { label: "Detail", render: (r) => <code className="small" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word" }}>{r.detail ? JSON.stringify(r.detail).slice(0, 400) : ""}</code> }]} />
      </Panel>
    </div>
  );
}
