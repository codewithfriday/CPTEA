import React, { useEffect, useState } from "react";
import { api, download, dt } from "../../api";
import MapView from "../../components/MapView";
import ParamEditor, { hasErrors } from "../../components/ParamEditor";
import { can, Panel, Table, useAsync, useAuth, useToast } from "../../components/ui";
import { useProject } from "../ProjectShell";

export default function Settings() {
  const { project: p, runId } = useProject();
  const { user } = useAuth();
  const toast = useToast();
  const { data: defs } = useAsync(() => api.get("/params/definitions"), []);
  const { data: sets, reload } = useAsync(() => api.get(`/projects/${p.id}/paramsets`), [p.id]);
  const [cur, setCur] = useState(null);
  const [values, setValues] = useState(null);
  const [note, setNote] = useState("");
  useEffect(() => {
    if (sets && sets.length && (!cur || !sets.find((s) => s.id === cur.id))) { setCur(sets[0]); setValues(sets[0].values); }
  }, [sets, cur]);
  if (!defs || !sets || !values) return null;
  const isLatest = cur && sets[0].id === cur.id;
  const canEdit = can(user, "analyst");
  const save = async () => {
    try {
      const ps = await api.post(`/projects/${p.id}/paramsets`, { name: cur.name, values, note, parent_id: cur.id });
      toast(`Saved ${ps.name} version ${ps.version}`);
      setNote("");
      setCur(ps);
      reload();
    } catch (e) { toast(e.message, "error"); }
  };
  const imp = async (e) => {
    const f = e.target.files[0];
    if (!f) return;
    const fd = new FormData();
    fd.append("file", f);
    try { const ps = await api.post(`/projects/${p.id}/paramsets/import`, fd); toast(`Imported as ${ps.name}`); setCur(ps); setValues(ps.values); reload(); } catch (x) { toast(x.message, "error"); }
  };
  return (
    <div>
      <Panel title="Parameter sets" sub="Saving creates a new version; earlier versions stay read-only and keep the runs that used them reproducible"
        actions={<>
          <button className="btn ghost sm" onClick={() => download(`/paramsets/${cur.id}/export`)}>Export JSON</button>
          {canEdit && <label className="btn ghost sm" style={{ cursor: "pointer" }}>Import JSON<input type="file" accept=".json" hidden onChange={imp} /></label>}
        </>}>
        <Table rows={sets} onRow={(s) => { setCur(s); setValues(s.values); }} maxHeight={220}
          columns={[{ label: "Set", render: (s) => <b style={{ color: s.id === cur.id ? "var(--teal-2)" : undefined }}>{s.name} v{s.version}</b> },
            { key: "author", label: "Author" }, { label: "Saved", render: (s) => dt(s.created_at) }, { key: "note", label: "Note" },
            { label: "Differs from default", render: (s) => Object.keys(s.diff).join(", ") || "none" }]} />
      </Panel>
      <Panel title={`${cur.name} v${cur.version}`} sub={isLatest ? "Edit values below, then save as a new version" : "Older version: editing starts a new version from these values"}>
        <ParamEditor defs={defs} values={values} base={cur.values} onChange={setValues} readOnly={!canEdit} isAdmin={user.role === "admin"} />
        {canEdit && (
          <div className="row" style={{ marginTop: 14 }}>
            <input placeholder="Change note (what and why)" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1, minWidth: 240 }} />
            <button className="btn" onClick={save} disabled={hasErrors(defs, values) || JSON.stringify(values) === JSON.stringify(cur.values)}>Save new version</button>
          </div>
        )}
      </Panel>
      <CutLines values={values} setValues={canEdit ? setValues : null} runId={runId} />
    </div>
  );
}

function CutLines({ values, setValues, runId }) {
  const [tracks, setTracks] = useState(null);
  useEffect(() => {
    if (runId) api.get(`/runs/${runId}/tracks?max_points=40`).then(setTracks).catch(() => setTracks(null));
  }, [runId]);
  const lat = values["CLU-02b"] !== "longitude";
  const cuts = (Array.isArray(values["CLU-02"]) ? values["CLU-02"] : []).map(Number).filter((x) => !Number.isNaN(x));
  return (
    <Panel title="Cut lines for route classification" sub={setValues ? `Click the map to add a ${lat ? "latitude" : "meridian"}; click a line to remove it` : "Cut lines of this set"}>
      <MapView deps={[JSON.stringify(cuts), tracks ? tracks.tracks.length : 0, lat]} draw={(L, map, g) => {
        let b = null;
        (tracks?.tracks || []).forEach((t) => { const pl = L.polyline(t.coords, { color: "#0F7482", weight: 0.6, opacity: 0.35 }).addTo(g); b = b ? b.extend(pl.getBounds()) : pl.getBounds(); });
        cuts.forEach((c) => {
          const line = lat ? [[c, -180], [c, 180]] : [[-80, c], [80, c]];
          const pl = L.polyline(line, { color: "#B0306A", weight: 2, dashArray: "6 6" }).bindTooltip(`${c}°`, { permanent: true, direction: "right" }).addTo(g);
          if (setValues) pl.on("click", (e) => { L.DomEvent.stopPropagation(e); setValues({ ...values, "CLU-02": cuts.filter((x) => x !== c) }); });
        });
        map.off("click");
        if (setValues) map.on("click", (e) => {
          const v = Math.round((lat ? e.latlng.lat : e.latlng.lng) * 10) / 10;
          setValues({ ...values, "CLU-02": [...cuts, v].sort((a, b2) => b2 - a) });
        });
        return b;
      }} />
      <p className="muted small" style={{ marginTop: 6 }}>Keep every cut line outside the classification clip radius ({values["CLU-01"]} NM) around both aerodromes, otherwise tracks will not cross it.</p>
    </Panel>
  );
}
