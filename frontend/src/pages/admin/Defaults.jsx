import React, { useEffect, useState } from "react";
import { api, dt } from "../../api";
import ParamEditor, { hasErrors } from "../../components/ParamEditor";
import { Panel, Table, useAsync, useToast } from "../../components/ui";

export default function Defaults() {
  const toast = useToast();
  const { data: defs } = useAsync(() => api.get("/params/definitions"), []);
  const { data: sets, reload } = useAsync(() => api.get("/paramsets/system"), []);
  const [values, setValues] = useState(null);
  const [note, setNote] = useState("");
  const cur = sets && sets[sets.length - 1];
  useEffect(() => { if (cur) setValues(cur.values); }, [cur?.id]); // eslint-disable-line
  if (!defs || !cur || !values) return null;
  const save = async () => {
    try { const ps = await api.post("/paramsets/system", { values, note }); toast(`Saved system defaults version ${ps.version}`); setNote(""); reload(); } catch (e) { toast(e.message, "error"); }
  };
  return (
    <div className="page">
      <h1 style={{ marginBottom: 14 }}>Default parameters</h1>
      <Panel title="System default parameters" sub="New projects start from the latest version. Existing projects keep their own sets. The shipped values reproduce the VTBS–WADD study.">
        <Table rows={[...sets].reverse()} maxHeight={200} columns={[{ label: "Version", render: (s) => `${s.name} v${s.version}` }, { key: "author", label: "Author" },
          { label: "Saved", render: (s) => dt(s.created_at) }, { key: "note", label: "Note" }, { label: "Differs from shipped default", render: (s) => Object.keys(s.diff).join(", ") || "none" }]} />
      </Panel>
      <Panel title={`Edit ${cur.name} v${cur.version}`}>
        <ParamEditor defs={defs} values={values} base={cur.values} onChange={setValues} isAdmin />
        <div className="row" style={{ marginTop: 14 }}>
          <button className="btn ghost" onClick={() => setValues(defs.defaults)}>Restore shipped VTBS–WADD values</button>
          <input placeholder="Change note (what and why)" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1, minWidth: 240 }} />
          <button className="btn" disabled={hasErrors(defs, values) || JSON.stringify(values) === JSON.stringify(cur.values)} onClick={save}>Save new version</button>
        </div>
      </Panel>
    </div>
  );
}
