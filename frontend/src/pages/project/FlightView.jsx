import React, { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, fmt } from "../../api";
import Chart, { axis, base, COLORS } from "../../components/Chart";
import MapView, { altColor, ringLatLngs } from "../../components/MapView";
import { Badge, can, Empty, Panel, Table, useAuth, useToast } from "../../components/ui";
import { RunPicker, useProject } from "../ProjectShell";

const LABELS = {
  airborne_min: "Airborne time (min)", flown_total: "Flown distance, total (NM)", toc_min: "Top of climb after take-off (min)",
  toc_dist: "Top of climb from ADEP (NM)", tod_dist: "Top of descent from ADES (NM)", tod_to_end_min: "Top of descent to landing (min)",
  cruise_changes: "Cruise level changes", score_total: "Cruise deviation score", score_above: "Score above filed", score_below: "Score below filed",
  time_above_min: "Cruise minutes above filed", time_below_min: "Cruise minutes below filed", time_at_min: "Cruise minutes at filed",
  net_turn: "Net turn near ADES (°)", xt_median: "Median distance from filed route (NM)", xt_max: "Maximum distance from filed route (NM)",
};
const label = (m) => LABELS[m] || m.replace(/^ext_gc_(\d+)/, "Extension over great circle, $1 NM rings (NM)").replace(/^gcd_(\d+)/, "Great-circle distance between $1 NM rings (NM)")
  .replace(/^dep_dist_(\d+)/, "Distance flown to $1 NM ring after take-off (NM)").replace(/^ext_(\d+)/, "Path extension, $1 NM rings (NM)").replace(/^extp_(\d+)/, "Path extension, $1 NM rings (%)")
  .replace(/^time_(\d+)/, "En-route time, $1 NM rings (min)").replace(/^flown_(\d+)/, "Flown distance, $1 NM rings (NM)").replace(/^ref_(\d+)/, "Reference distance, $1 NM rings (NM)")
  .replace(/^arr_time_(\d+)/, "Time in last $1 NM (min)").replace(/^arr_dist_(\d+)/, "Distance flown in last $1 NM (NM)").replace(/^dep_time_(\d+)/, "Time to $1 NM ring after take-off (min)");

export default function FlightView() {
  const { project: p, runId } = useProject();
  const { fid } = useParams();
  const nav = useNavigate();
  const toast = useToast();
  const [d, setD] = useState(null);
  const [list, setList] = useState(null);
  const [q, setQ] = useState("");
  const [cmp, setCmp] = useState([]);
  const [cmpData, setCmpData] = useState([]);
  const [pos, setPos] = useState(null);
  const [labels, setLabels] = useState(false);
  useEffect(() => { if (runId) api.get(`/runs/${runId}/flights`).then(setList).catch(() => {}); }, [runId]);
  useEffect(() => {
    if (!runId || !fid) return;
    setD(null);
    setPos(null);
    api.get(`/runs/${runId}/flights/${fid}`).then(setD).catch((e) => toast(e.message, "error"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, fid]);
  useEffect(() => {
    Promise.all(cmp.map((f) => api.get(`/runs/${runId}/flights/${f}`))).then(setCmpData).catch(() => {});
  }, [cmp, runId]);
  const rows = (list?.rows || []).filter((r) => !q || r.fid.includes(q.toUpperCase()));
  const go = (f) => nav(`/projects/${p.id}/flight/${f}`);
  const idx = list ? list.rows.findIndex((r) => r.fid === fid) : -1;
  return (
    <div>
      <div className="row" style={{ marginBottom: 12, alignItems: "flex-end" }}>
        <RunPicker />
        <label className="f">Flight<input list="fl" value={q || fid || ""} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => e.key === "Enter" && rows[0] && go(rows[0].fid)} placeholder="THA431-2025-07-01" />
          <datalist id="fl">{rows.slice(0, 200).map((r) => <option key={r.fid} value={r.fid} />)}</datalist></label>
        <button className="btn ghost" onClick={() => rows[0] && go(rows.find((r) => r.fid === q)?.fid || rows[0].fid)}>Open</button>
        {idx > 0 && <button className="btn ghost" onClick={() => go(list.rows[idx - 1].fid)}>Previous</button>}
        {idx >= 0 && idx < list.rows.length - 1 && <button className="btn ghost" onClick={() => go(list.rows[idx + 1].fid)}>Next</button>}
        {fid && cmp.length < 5 && !cmp.includes(fid) && <button className="btn ghost" onClick={() => setCmp([...cmp, fid])}>Add to comparison</button>}
      </div>
      {!fid && <div className="panel"><Empty title="Choose a flight">Pick a flight above, or click a track or row on the Results page.</Empty></div>}
      {fid && !d && <div className="muted">Loading {fid}…</div>}
      {d && <Detail d={d} p={p} runId={runId} pos={pos} setPos={setPos} cmpData={cmpData} labels={labels} setLabels={setLabels} />}
      {cmp.length > 0 && (
        <Panel title="Comparison" sub="Up to 5 flights are drawn on the map and profile above" actions={<button className="btn ghost sm" onClick={() => setCmp([])}>Clear</button>}>
          <div className="row">{cmp.map((f, i) => <span key={f} className="badge" style={{ borderLeft: `4px solid ${COLORS[(i + 1) % 8]}` }}>{f} <button className="link" onClick={() => setCmp(cmp.filter((x) => x !== f))}>remove</button></span>)}</div>
        </Panel>
      )}
    </div>
  );
}

/** Level segments inside climb (before TOC) or descent (after TOD); cruise levels are not level-offs. */
function levelOffs(d) {
  const toc = d.toc_t_min ?? -1;
  const tod = d.tod_t_min ?? 1e9;
  const out = [];
  d.level_segments.filter((s) => s.alt > 1000).forEach((s) => {
    const a = s.t_min, b = s.t_min + s.dur_s / 60;
    if (a < toc) out.push([a, Math.min(b, toc)]);
    if (b > tod) out.push([Math.max(a, tod), b]);
  });
  return out.filter(([a, b]) => b - a > 0.05);
}

const printable = (t) => (t || "").replace(/[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]/g, "");

function Detail({ d, p, runId, pos, setPos, cmpData, labels, setLabels }) {
  const { user } = useAuth();
  const toast = useToast();
  const m = d.meta;
  const T = d.track;
  const P = d.profile;
  const refresh = async () => {
    try {
      const r = await api.post(`/projects/${p.id}/fpl/fetch`, { dataset_id: (await api.get(`/runs/${runId}`)).dataset_id, force: true, fid: m.fid });
      toast(`Fetching the plan for ${m.fid} from the AirNav flight data API (job ${r.job}); re-run to use it`);
    } catch (e) { toast(e.message, "error"); }
  };
  const profOpt = useMemo(() => {
    const series = [
      { name: "Flown altitude", type: "line", data: P.t_min.map((t, i) => [t, P.alt[i]]), symbol: "none", color: COLORS[0], lineStyle: { width: 2 },
        markArea: { silent: true, itemStyle: { color: "rgba(217,142,31,0.18)" }, data: levelOffs(d).map(([a, b]) => [{ xAxis: a }, { xAxis: b }]) },
        markLine: { symbol: "none", label: { formatter: "{b}", color: "#3d5163" }, lineStyle: { color: "#B0306A", type: "dashed" },
          data: [d.toc_t_min != null && { name: "TOC", xAxis: d.toc_t_min }, d.tod_t_min != null && { name: "TOD", xAxis: d.tod_t_min }].filter(Boolean) } },
      { name: "Filed level", type: "line", step: "end", data: P.t_min.map((t, i) => [t, P.planned[i]]), symbol: "none", color: COLORS[1], lineStyle: { width: 2, type: "dashed" }, connectNulls: false },
      ...cmpData.map((c, j) => ({ name: c.meta.fid, type: "line", data: c.profile.t_min.map((t, i) => [t, c.profile.alt[i]]), symbol: "none", color: COLORS[(j + 2) % 8], lineStyle: { width: 1.2 } })),
    ];
    return { ...base(), tooltip: { trigger: "axis", valueFormatter: (v) => (v == null ? "–" : `${Math.round(v).toLocaleString()} ft`) },
      xAxis: axis({ type: "value", name: "minutes after take-off", nameLocation: "middle", nameGap: 26 }), yAxis: axis({ type: "value", name: "ft" }), series,
      dataZoom: [{ type: "inside" }, { type: "slider", height: 18, bottom: 4 }] };
  }, [d, cmpData]);  // eslint-disable-line
  const vrOpt = useMemo(() => ({ ...base(), tooltip: { trigger: "axis" }, xAxis: axis({ type: "value", name: "minutes after take-off", nameLocation: "middle", nameGap: 26 }),
    yAxis: [axis({ type: "value", name: "ft/s" }), axis({ type: "value", name: "kt", splitLine: { show: false } })],
    series: [{ name: "Vertical rate", type: "line", data: P.t_min.map((t, i) => [t, P.vr[i]]), symbol: "none", color: COLORS[0], markLine: { symbol: "none", silent: true, label: { show: false }, lineStyle: { color: "#B0306A", type: "dotted" }, data: [{ yAxis: 5 }, { yAxis: -5 }] } },
      { name: "Ground speed", type: "line", yAxisIndex: 1, data: T.t_min.map((t, i) => [t, T.gs[i]]), symbol: "none", color: COLORS[1] }],
    dataZoom: [{ type: "inside" }] }), [d]);  // eslint-disable-line
  const xtOpt = d.xt && { ...base(), legend: { show: false }, tooltip: { trigger: "axis" }, xAxis: axis({ type: "value", name: "latitude (°)", nameLocation: "middle", nameGap: 26 }), yAxis: axis({ type: "value", name: "NM" }),
    series: [{ name: "Distance from filed route", type: "line", data: d.xt.lat.map((la, i) => [la, d.xt.xt[i]]), symbol: "none", color: COLORS[3] }] };
  const wpCol = { at: "#1D7A46", above_1000: "#8CB8EC", above_2000: "#1F5FAD", below_1000: "#E7A3C2", below_2000: "#B0306A" };
  return (
    <>
      <Panel title={`${m.fid} · ${m.dirn} · ${m.type}`} sub={`Filed level ${m.rfl ? "FL" + m.rfl / 100 : "–"} · first cruise ${m.first_cruise ? "FL" + m.first_cruise / 100 : "–"} · highest ${m.max_alt ? Math.round(m.max_alt).toLocaleString() + " ft" : "–"} · TOC rule ${m.toc_rule || "–"}`}
        actions={<div className="row">
          {m.outlier && <Badge kind="bad">outlier</Badge>}{m.holding && <Badge kind="warn">holding-type turns</Badge>}{m.capped_climb && <Badge kind="warn">capped climb</Badge>}
          {!m.has_plan && <Badge kind="bad">no flight plan</Badge>}{m.has_plan && !m.has_ref && <Badge kind="warn">plan not expandable</Badge>}
          {(m.flags || []).map((f) => <Badge key={f}>{f}</Badge>)}
          {can(user, "analyst") && <button className="btn ghost sm" onClick={refresh}>Fetch plan from API</button>}</div>}>
        <MapView tall deps={[d, pos, cmpData.length, labels]} draw={(L, map, g) => {
          let b = null;
          for (let i = 1; i < T.lat.length; i++) {
            L.polyline([[T.lat[i - 1], T.lon[i - 1]], [T.lat[i], T.lon[i]]], { color: altColor(T.alt[i]), weight: 3 }).addTo(g);
          }
          b = L.latLngBounds(T.lat.map((la, i) => [la, T.lon[i]]));
          if (d.route) {
            L.polyline(d.route.coords, { color: "#D98E1F", weight: 2, dashArray: "6 5" }).addTo(g);
            d.route.fix_points.forEach((f) => L.circleMarker([f.lat, f.lon], { radius: 3, color: "#D98E1F", weight: 2 })
              .bindTooltip(`${f.name} FL${f.level ? f.level / 100 : "–"}`, { permanent: labels, direction: "right", className: labels ? "wplabel" : "" }).addTo(g));
          }
          (d.waypoints || []).forEach((w) => L.circleMarker([w.lat, w.lon], { radius: 7, color: wpCol[w.cls] || "#3d5163", weight: 3, fill: false })
            .bindTooltip(`${w.wp}: flown ${Math.round(w.cpa_alt).toLocaleString()} ft, filed ${w.plan.toLocaleString()} ft (${w.cls.replace("_", " ")})`).addTo(g));
          (d.gaps || []).forEach((x) => L.circleMarker([x.lat, x.lon], { radius: 5, color: "#B0306A", weight: 1, fillOpacity: 0.8 }).bindTooltip(`${Math.round(x.dt)} s data gap`).addTo(g));
          (d.changes || []).forEach((c) => L.marker([c.lat, c.lon], { icon: L.divIcon({ className: "", html: `<div style="font-size:16px;color:${c.matched ? "#1D7A46" : "#13283A"}">${c.kind === "climb" ? "▲" : "▼"}</div>` }) })
            .bindTooltip(`${c.kind} FL${Math.round(c.from / 100)} → FL${Math.round(c.to / 100)}${c.planned_fix ? ` (filed at ${c.planned_fix}, ${Math.round(c.dist_planned)} NM away)` : " (not in the plan)"}`).addTo(g));
          (d.levels || []).forEach((l) => L.circleMarker([l.lat, l.lon], { radius: 4, color: "#D98E1F", weight: 2, fillOpacity: 0.2 })
            .bindTooltip(`${l.phase} level-off ${Math.round(l.alt).toLocaleString()} ft, ${Math.round(l.dur)} s`).addTo(g));
          Object.entries(d.airports).forEach(([ap, [la, lo]]) => {
            L.circleMarker([la, lo], { radius: 5, color: "#13283A", fillOpacity: 1 }).bindTooltip(ap, { permanent: true }).addTo(g);
            d.rings.forEach((r) => L.polyline(ringLatLngs(la, lo, r), { color: "#5FB3BE", weight: 1, dashArray: "4 4" }).addTo(g));
          });
          cmpData.forEach((c, j) => L.polyline(c.track.lat.map((la, i) => [la, c.track.lon[i]]), { color: COLORS[(j + 2) % 8], weight: 1.5, opacity: 0.8 }).bindTooltip(c.meta.fid).addTo(g));
          if (pos != null && T.lat[pos] != null) L.circleMarker([T.lat[pos], T.lon[pos]], { radius: 8, color: "#B0306A", weight: 3 }).addTo(g);
          return pos == null ? b : null;
        }} />
        <div className="legend">
          <span><i style={{ background: "linear-gradient(90deg,#8fd0d6,#13283a)" }} />flown track, low to high</span>
          <span><i style={{ background: "#D98E1F" }} />filed route (dashed)</span>
          <span>○ cruise waypoint check: <b style={{ color: "#1D7A46" }}>at</b> / <b style={{ color: "#1F5FAD" }}>above</b> / <b style={{ color: "#B0306A" }}>below</b> filed</span>
          <span>▲▼ flown level change</span><span style={{ color: "#B0306A" }}>● data gap</span>
          <label className="row small" style={{ marginLeft: "auto" }}><input type="checkbox" checked={labels} onChange={(e) => setLabels(e.target.checked)} /> Show waypoint names</label>
        </div>
        <div className="row" style={{ marginTop: 8 }}>
          <span className="small muted">Playback</span>
          <input type="range" min={0} max={T.lat.length - 1} value={pos ?? 0} onChange={(e) => setPos(Number(e.target.value))} style={{ flex: 1 }} aria-label="Playback position" />
          <span className="small">{pos != null ? `${fmt(T.t_min[pos], 0)} min · ${Math.round(T.alt[pos]).toLocaleString()} ft · ${T.gs[pos] ?? "–"} kt` : "drag to replay"}</span>
        </div>
      </Panel>
      <div className="grid2">
        <Panel title="Vertical profile" sub="Shaded: level-offs in climb (before TOC) and descent (after TOD); dashed: filed level by leg">
          <Chart name={`${m.fid}_profile`} height={330} option={profOpt} />
        </Panel>
        <Panel title="Vertical rate and ground speed" sub="Dotted lines: level threshold">
          <Chart name={`${m.fid}_vr_speed`} height={330} option={vrOpt} />
        </Panel>
      </div>
      <div className="grid2">
        <Panel title="Metrics for this flight" sub={`Against the median of ${m.dirn} ${m.type} flights`}>
          <Table maxHeight={420} rows={d.card} columns={[{ label: "Metric", render: (r) => label(r.metric) }, { label: "This flight", num: true, render: (r) => fmt(r.value) },
            { label: "Group median", num: true, render: (r) => fmt(r.group_median) }]} />
        </Panel>
        <Panel title="Lateral deviation" sub="Distance from the filed route along the en-route portion">
          {xtOpt ? <Chart name={`${m.fid}_lateral`} height={300} option={xtOpt} /> : <Empty title="No reference route">This flight has no expandable flight plan.</Empty>}
        </Panel>
      </div>
      <div className="grid2">
        <Panel title="Level segments in climb and descent">
          <Table maxHeight={300} rows={d.levels || []} empty="None" columns={[{ key: "phase", label: "Phase" }, { label: "Altitude (ft)", num: true, render: (r) => Math.round(r.alt).toLocaleString() },
            { label: "Duration (s)", num: true, render: (r) => Math.round(r.dur) }, { label: "From ADEP / ADES (NM)", num: true, render: (r) => `${Math.round(r.d_adep)} / ${Math.round(r.d_ades)}` }]} />
        </Panel>
        <Panel title="Flight plan" sub={d.route ? `${d.route.source} · ${d.route.route}` : "No plan matched"}>
          {d.route && d.route.edge.length > 0 && <p className="small muted">Not in the AIS data (route end): {d.route.edge.join(", ")}</p>}
          {d.route && d.route.blocking.length > 0 && <p className="small"><Badge kind="bad">unresolved</Badge> {d.route.blocking.join(", ")}</p>}
          {(m.plan_messages || []).map((x, i) => (
            <div key={i}><div className="small"><b>{x.msg_type}</b> · {x.source} · filed {x.filed_time || "?"} {x.status && `· ${x.status}`} {x.atd && `· ATD ${x.atd.slice(11, 16)}`} {x.ata && `· ATA ${x.ata.slice(11, 16)}`}</div>
              {(x.flags || []).length > 0 && <div className="small"><Badge kind="warn">API cross-check</Badge> {x.flags.join("; ")}</div>}
              <pre className="raw">{printable(x.raw) || "(structured fields only)"}</pre></div>))}
        </Panel>
      </div>
    </>
  );
}
