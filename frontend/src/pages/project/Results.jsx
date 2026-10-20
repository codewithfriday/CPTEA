import React, { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, fmt, pct, qs } from "../../api";
import Chart, { axis, base, COLORS } from "../../components/Chart";
import FilterBar from "../../components/FilterBar";
import MapView, { ringLatLngs } from "../../components/MapView";
import { Empty, Panel, Table, Tabs, useToast } from "../../components/ui";
import { RunPicker, useProject } from "../ProjectShell";

const FKEY = (pid) => `cptea_filters_${pid}`;

export default function Results() {
  const { project: p, runId, runs } = useProject();
  const toast = useToast();
  const nav = useNavigate();
  const [filters, setFilters] = useState(() => { try { return JSON.parse(localStorage.getItem(FKEY(p.id)) || "{}"); } catch { return {}; } });
  const [S, setS] = useState(null);
  const [loading, setLoading] = useState(false);
  useEffect(() => { try { localStorage.setItem(FKEY(p.id), JSON.stringify(filters)); } catch { /* */ } }, [filters, p.id]);
  useEffect(() => {
    if (!runId) return;
    setLoading(true);
    api.get(`/runs/${runId}/summary${qs(filters)}`).then(setS).catch((e) => toast(e.message, "error")).finally(() => setLoading(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runId, JSON.stringify(filters)]);
  const openFlight = (fid) => nav(`/projects/${p.id}/flight/${fid}`);
  if (!runs.some((r) => r.status === "done")) return <div className="panel"><Empty title="No results yet">Start a run on the Runs page; results appear here when it finishes.</Empty></div>;
  return (
    <div>
      <div className="row" style={{ marginBottom: 10 }}><RunPicker />{loading && <span className="muted small">Updating…</span>}</div>
      {S && <FilterBar options={S.options} value={filters} onChange={setFilters} />}
      {S && (
        <>
          <div className="kpis">
            {S.kpis.map((k) => (
              <button key={k.id} className="kpi" onClick={() => document.getElementById(k.chart)?.scrollIntoView({ behavior: "smooth" })}>
                <div className="v">{k.value}</div><div className="u">{k.unit}</div><div className="l">{k.label}</div>
              </button>
            ))}
          </div>
          <Coverage S={S} />
          <TrackMap runId={runId} filters={filters} p={p} onFlight={openFlight} />
          <Clusters S={S} />
          <Horizontal S={S} />
          <Terminal S={S} onFlight={openFlight} />
          <Cruise S={S} />
          <LevelOffs S={S} />
          <Flights runId={runId} filters={filters} onFlight={openFlight} />
        </>
      )}
    </div>
  );
}

function Coverage({ S }) {
  const c = S.coverage;
  return (
    <Panel id="coverage" title="Data coverage" sub={`${S.n.flights} flights in the current filter · ${S.n.with_plan} with a flight plan · ${S.n.with_ref} with a reference route · ${S.n.outliers} outliers`}>
      {c && c.months ? (
        <div className="tablewrap"><table className="t"><thead><tr><th>Callsign</th>{c.months.map((m) => <th key={m} className="num">{m.slice(2)}</th>)}</tr></thead>
          <tbody>{c.callsigns.map((cs, i) => <tr key={cs}><td><b>{cs}</b></td>{c.counts[i].map((n, j) => <td key={j} className="num" style={{ background: n === 0 ? "var(--magenta-tint)" : undefined }}>{n}</td>)}</tr>)}</tbody></table></div>
      ) : <span className="muted">No coverage data.</span>}
      <div className="small muted" style={{ marginTop: 6 }}>Aircraft types: {S.types.map((t) => `${t.callsign} ${t.type} (${t.n})`).join(", ")}</div>
    </Panel>
  );
}

function TrackMap({ runId, filters, p, onFlight }) {
  const [data, setData] = useState(null);
  const [colorBy, setColorBy] = useState("dirn");
  useEffect(() => { api.get(`/runs/${runId}/tracks${qs({ ...filters, max_points: 60 })}`).then(setData).catch(() => {}); }, [runId, JSON.stringify(filters)]);  // eslint-disable-line
  const keys = useMemo(() => [...new Set((data?.tracks || []).map((t) => (colorBy === "dirn" ? t.dirn : colorBy === "type" ? t.type : `cluster ${t.cluster}`)))], [data, colorBy]);
  return (
    <Panel id="map" title="Track density and nominal routes" sub="Click a track to open it in the Flight view" actions={
      <select value={colorBy} onChange={(e) => setColorBy(e.target.value)}><option value="dirn">Colour by direction</option><option value="cluster">Colour by cluster</option><option value="type">Colour by aircraft type</option></select>}>
      <MapView tall deps={[data, colorBy]} draw={(L, map, g) => {
        if (!data) return null;
        let b = null;
        data.tracks.forEach((t) => {
          const k = colorBy === "dirn" ? t.dirn : colorBy === "type" ? t.type : `cluster ${t.cluster}`;
          const pl = L.polyline(t.coords, { color: t.outlier ? "#B0306A" : COLORS[keys.indexOf(k) % COLORS.length], weight: t.outlier ? 1.4 : 0.8, opacity: t.outlier ? 0.9 : 0.35 })
            .bindTooltip(`${t.fid} · ${t.type}${t.outlier ? " · outlier" : ""}`).on("click", () => onFlight(t.fid)).addTo(g);
          b = b ? b.extend(pl.getBounds()) : pl.getBounds();
        });
        Object.entries(data.airports || {}).forEach(([ap, [la, lo]]) => {
          L.circleMarker([la, lo], { radius: 5, color: "#13283A", fillOpacity: 1 }).bindTooltip(ap, { permanent: true }).addTo(g);
          [40, 100].forEach((r) => L.polyline(ringLatLngs(la, lo, r), { color: "#5FB3BE", weight: 1, dashArray: "4 4" }).addTo(g));
        });
        return b;
      }} />
      <div className="legend">{keys.map((k, i) => <span key={k}><i style={{ background: COLORS[i % COLORS.length] }} />{k}</span>)}<span><i style={{ background: "#B0306A" }} />outlier</span><span><i style={{ background: "#5FB3BE" }} />40 / 100 NM rings</span></div>
    </Panel>
  );
}

function Clusters({ S }) {
  const cl = S.clusters;
  if (!cl) return null;
  const months = [...new Set(cl.monthly.map((m) => m.month))].sort();
  const series = [];
  [...new Set(cl.monthly.map((m) => `${m.dirn} · cluster ${m.cluster}`))].forEach((k) => {
    series.push({ name: k, type: "bar", stack: k.split(" · ")[0], data: months.map((m) => { const r = cl.monthly.find((x) => `${x.dirn} · cluster ${x.cluster}` === k && x.month === m); return r ? r.n : 0; }) });
  });
  return (
    <Panel id="clusters" title="Route classification and usage (metric 7)" sub={`Cut lines ${cl.cuts.join(", ")}° ${cl.orientation}`}>
      <div className="grid2">
        <Table rows={cl.summary} columns={[{ key: "dirn", label: "Direction" }, { key: "cluster", label: "Cluster", num: true }, { key: "n", label: "Flights", num: true },
          { label: "Share", num: true, render: (r) => pct(r.share) }, { key: "outliers", label: "Outliers", num: true }, { label: "Contingency", render: (r) => (r.contingency ? <span className="badge warn">yes</span> : "no") }]} />
        <Chart name="route_usage" height={260} option={{ ...base(), xAxis: axis({ type: "category", data: months.map((m) => m.slice(2)) }), yAxis: axis({ type: "value", name: "flights" }), series }} />
      </div>
    </Panel>
  );
}

function Horizontal({ S }) {
  const [tab, setTab] = useState("rings");
  const rings = [...new Set(S.extension.map((e) => e.ring))];
  const dirs = S.dirs;
  const dotOpt = useMemo(() => ({
    ...base({ grid: { left: 90, right: 30, top: 48, bottom: 40 } }),
    tooltip: { trigger: "item", formatter: (pp) => `${pp.seriesName}, ${pp.value[1]} NM rings<br/>median ${fmt(pp.value[0])} NM<br/>P15 ${fmt(pp.value[2])} · P85 ${fmt(pp.value[3])}` },
    xAxis: axis({ type: "value", name: "flown minus filed (NM)", nameLocation: "middle", nameGap: 26 }),
    yAxis: axis({ type: "category", data: rings.map(String), name: "ring (NM)" }),
    series: dirs.map((d, j) => ({
      name: d, type: "custom",
      renderItem: (params, api2) => {
        const y = api2.coord([0, api2.value(1)])[1] + (j - (dirs.length - 1) / 2) * 14;
        const x0 = api2.coord([api2.value(2), 0])[0], x1 = api2.coord([api2.value(3), 0])[0], xm = api2.coord([api2.value(0), 0])[0];
        return { type: "group", children: [
          { type: "line", shape: { x1: x0, y1: y, x2: x1, y2: y }, style: { stroke: COLORS[j], lineWidth: 3, lineCap: "round" } },
          { type: "circle", shape: { cx: xm, cy: y, r: 6 }, style: { fill: COLORS[j], stroke: "#fff", lineWidth: 1.5 } }] };
      },
      encode: { x: [0, 2, 3], y: 1 },
      data: S.extension.filter((e) => e.dirn === d).map((e) => [e.median, String(e.ring), e.p15, e.p85]),
    })),
  }), [S]);  // eslint-disable-line
  const box = (by) => {
    const rows = S.extension_box.filter((b) => b.by === by);
    return { ...base({ grid: { left: 60, right: 20, top: 20, bottom: by.includes("month") ? 70 : 40 } }), tooltip: { trigger: "item" },
      xAxis: axis({ type: "category", data: rows.map((r) => r.key), axisLabel: { rotate: by.includes("month") ? 60 : 0, color: "#3d5163", fontSize: 10 } }),
      yAxis: axis({ type: "value", name: "NM" }),
      series: [{ name: "path extension", type: "boxplot", itemStyle: { color: "#e3f1f2", borderColor: COLORS[0] }, data: rows.map((r) => [r.min, r.p25, r.median, r.p75, r.max]) }] };
  };
  const months = [...new Set(S.enroute_monthly.map((m) => m.month))].sort();
  const enr = { ...base(), xAxis: axis({ type: "category", data: months.map((m) => m.slice(2)) }), yAxis: axis({ type: "value", name: "min", scale: true }),
    series: dirs.flatMap((d, j) => {
      const rows = months.map((m) => S.enroute_monthly.find((x) => x.dirn === d && x.month === m));
      return [{ name: `${d} P15`, type: "line", stack: `b${j}`, symbol: "none", lineStyle: { opacity: 0 }, data: rows.map((r) => r?.p15 ?? null), tooltip: { show: false } },
        { name: `${d} P15–P85`, type: "line", stack: `b${j}`, symbol: "none", lineStyle: { opacity: 0 }, areaStyle: { color: COLORS[j], opacity: 0.15 }, data: rows.map((r) => (r ? r.p85 - r.p15 : null)), tooltip: { show: false } },
        { name: d, type: "line", data: rows.map((r) => r?.median ?? null), color: COLORS[j], lineStyle: { width: 2 } }];
    }), legend: { top: 0, data: dirs } };
  const lat = { ...base(), tooltip: { trigger: "axis" }, xAxis: axis({ type: "value", name: "latitude (°)", nameLocation: "middle", nameGap: 26 }),
    yAxis: axis({ type: "value", name: "NM from filed route" }),
    series: dirs.flatMap((d, j) => {
      const rows = S.lateral.filter((r) => r.dirn === d).sort((a, b) => a.lat - b.lat);
      return [{ name: `${d} median`, type: "line", data: rows.map((r) => [r.lat, r.median]), color: COLORS[j], lineStyle: { width: 2 }, symbol: "none" },
        { name: `${d} P90`, type: "line", data: rows.map((r) => [r.lat, r.p90]), color: COLORS[j], lineStyle: { type: "dashed" }, symbol: "none" }];
    }) };
  return (
    <Panel id="extension" title="Horizontal efficiency (metrics 1–6)" sub={`Against the filed route; en-route boundary ${S.main_ring} NM; distances on the ${S.earth_model} earth model`}>
      <Tabs value={tab} onChange={setTab} tabs={[{ id: "rings", label: "Boundary sensitivity" }, { id: "type", label: "By aircraft type" }, { id: "month", label: "By month" },
        { id: "time", label: "En-route time" }, { id: "lateral", label: "Lateral deviation" }, { id: "fir", label: "By FIR" }, { id: "table", label: "Table" }]} />
      <RefRouteNotice S={S} />
      {tab === "rings" && <Chart name="extension_rings" height={90 + rings.length * 70} option={dotOpt} />}
      {tab === "type" && <Chart name="extension_by_type" option={box("dirn+type")} />}
      {tab === "month" && <Chart name="extension_by_month" option={box("dirn+month")} />}
      {tab === "time" && (<>
        <Chart name="enroute_time" option={enr} />
        <Table rows={S.variance.filter((v) => v.by !== "dirn+cluster" || S.options.cluster.length > 1)} maxHeight={260}
          columns={[{ key: "by", label: "Grouping" }, { key: "key", label: "Group" }, { key: "n", label: "Flights", num: true },
            { label: "Median (min)", num: true, render: (r) => fmt(r.median) }, { label: "Variance ±(min)", num: true, render: (r) => fmt(r.var) }]} />
      </>)}
      {tab === "lateral" && <Chart name="lateral_deviation" option={lat} />}
      {tab === "fir" && (S.fir.length ? <Table rows={S.fir} columns={[{ key: "dirn", label: "Direction" }, { key: "fir", label: "FIR" }, { key: "ansp", label: "ANSP" },
        { key: "n", label: "Flights", num: true }, { label: "Flown (NM)", num: true, render: (r) => fmt(r.flown_median) }, { label: "Reference (NM)", num: true, render: (r) => fmt(r.ref_median) },
        { label: "Extension (NM)", num: true, render: (r) => fmt(r.ext_median) }, { label: "Time (min)", num: true, render: (r) => fmt(r.time_median) }]} />
        : <Empty title="No FIR boundaries loaded">An administrator can import FIR polygons (GeoJSON) under Aeronautical data; the next run then splits every flight by FIR.</Empty>)}
      {tab === "table" && <Table rows={S.extension} columns={[{ key: "ring", label: "Ring", num: true }, { key: "dirn", label: "Direction" }, { key: "n", label: "n", num: true },
        { label: "Reference", num: true, render: (r) => fmt(r.ref_median) }, { label: "Flown", num: true, render: (r) => fmt(r.flown_median) }, { label: "Extension", num: true, render: (r) => fmt(r.median) },
        { label: "P15 / P85", num: true, render: (r) => `${fmt(r.p15)} / ${fmt(r.p85)}` }, { label: "%", num: true, render: (r) => fmt(r.median_pct, 2) },
        { label: "Shorter than filed", num: true, render: (r) => pct(r.shorter_share) }, { label: "vs great circle %", num: true, render: (r) => fmt(r.hfe_gc_pct, 2) },
        { label: "Time (min)", num: true, render: (r) => fmt(r.time_median) }, { label: "Variance", num: true, render: (r) => fmt(r.time_var) }]} />}
    </Panel>
  );
}

function Terminal({ S, onFlight }) {
  const T = S.terminal;
  const aps = [...new Set(T.monthly.map((m) => m.ades))];
  const months = [...new Set(T.monthly.map((m) => m.month))].sort();
  const bar = (key, name, scale = 1) => ({ ...base(), xAxis: axis({ type: "category", data: months.map((m) => m.slice(2)) }), yAxis: axis({ type: "value", name }),
    series: aps.map((a) => ({ name: `into ${a}`, type: "bar", data: months.map((m) => { const r = T.monthly.find((x) => x.ades === a && x.month === m); return r ? +(r[key] * scale).toFixed(2) : 0; }) })) });
  const scat = { ...base(), tooltip: { trigger: "item", formatter: (pp) => `${pp.data[2]}<br/>en-route ${fmt(pp.data[0])} min · arrival ${fmt(pp.data[1])} min` },
    xAxis: axis({ type: "value", name: "en-route time (min)", scale: true, nameLocation: "middle", nameGap: 26 }), yAxis: axis({ type: "value", name: `last ${T.big_ring} NM (min)`, scale: true }),
    series: aps.map((a) => ({ name: `into ${a} (r = ${fmt(T.corr[a], 2)})`, type: "scatter", symbolSize: 5, data: T.scatter.filter((x) => x.ades === a).map((x) => [x.enroute, x.arrival, x.fid]) })) };
  return (
    <Panel id="terminal" title="Terminal arrival" sub={`Extra time = time inside the ring minus the unimpeded reference; holding = net turn of at least the set threshold near the destination`}>
      <Table rows={T.by_ades} columns={[{ key: "ades", label: "Destination" }, { key: "ring", label: "Ring (NM)", num: true }, { key: "n", label: "n", num: true },
        { label: "Median (min)", num: true, render: (r) => fmt(r.median) }, { label: "P85", num: true, render: (r) => fmt(r.p85) }, { label: "Variance ±", num: true, render: (r) => fmt(r.var) },
        { label: "Unimpeded", num: true, render: (r) => fmt(r.unimpeded) }, { label: "Extra (mean)", num: true, render: (r) => fmt(r.add_mean) },
        { label: "Distance median / P85 (NM)", num: true, render: (r) => `${fmt(r.dist_median, 0)} / ${fmt(r.dist_p85, 0)}` }, { label: "Holding-type", num: true, render: (r) => pct(r.hold_share, 1) }]} />
      <div className="grid2" style={{ marginTop: 12 }}>
        <Chart name="terminal_extra_time" height={260} option={bar("add_mean", "min")} />
        <Chart name="terminal_holding" height={260} option={bar("hold_share", "%", 100)} />
      </div>
      <div id="terminal_scatter"><Chart name="enroute_vs_arrival" height={300} option={scat} onClick={(e) => e.data && onFlight(e.data[2])} /></div>
    </Panel>
  );
}

function Cruise({ S }) {
  const C = S.cruise;
  const cats = ["below_2000", "below_1000", "at", "above_1000", "above_2000"];
  const labels = ["≥2,000 ft below", "1,000 ft below", "At filed level", "1,000 ft above", "≥2,000 ft above"];
  const cols = ["#B0306A", "#E7A3C2", "#CFD6DA", "#8CB8EC", "#1F5FAD"];
  const rows = C.classes.filter((c) => c.n_flights >= 3);
  const opt = { ...base({ grid: { left: 160, right: 20, top: 48, bottom: 30 } }), tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (v) => `${v}%` },
    xAxis: axis({ type: "value", max: 100, name: "% of cruise waypoints" }), yAxis: axis({ type: "category", data: rows.map((r) => `${r.dirn} · ${r.type} (${r.n_flights})`), inverse: true }),
    series: cats.map((c, i) => ({ name: labels[i], type: "bar", stack: "a", color: cols[i], data: rows.map((r) => +(100 * r[c]).toFixed(1)),
      label: { show: true, formatter: (pp) => (pp.value >= 8 ? `${Math.round(pp.value)}%` : ""), color: i === 0 || i === 4 ? "#fff" : "#13283A", fontSize: 10 } })) };
  const ch = C.changes || {};
  return (
    <Panel id="cruise" title="Cruise vertical efficiency (metric 8)" sub="Leg-based score against the filed profile; flight above the filed level is reported separately because it is often a benefit">
      <Chart name="cruise_vs_filed" height={80 + rows.length * 30} option={opt} />
      <div className="grid2" style={{ marginTop: 12 }}>
        <div>
          <h3>Score by direction and type</h3>
          <Table rows={C.score} columns={[{ key: "dirn", label: "Direction" }, { key: "type", label: "Type" }, { key: "n", label: "n", num: true },
            { label: "Score median", num: true, render: (r) => fmt(r.median) }, { label: "P85", num: true, render: (r) => fmt(r.p85) },
            { label: "Min above", num: true, render: (r) => fmt(r.time_above_mean) }, { label: "Min below", num: true, render: (r) => fmt(r.time_below_mean) }, { label: "Min at", num: true, render: (r) => fmt(r.time_at_mean) }]} />
        </div>
        <div>
          <h3>Capped climbs</h3>
          <Table rows={C.capped} columns={[{ key: "dirn", label: "Direction" }, { key: "type", label: "Type" }, { key: "n", label: "n", num: true }, { label: "Share", num: true, render: (r) => pct(r.share) },
            { label: "Below filed (ft)", num: true, render: (r) => fmt(r.median_below_ft, 0) }, { label: "From ADEP (NM)", num: true, render: (r) => fmt(r.median_dist, 0) }, { label: "Duration (min)", num: true, render: (r) => fmt(r.median_min) }]} />
        </div>
      </div>
      <div id="cruise_changes" style={{ marginTop: 12 }}>
        <h3>Planned against flown level changes</h3>
        {ch.n ? (<>
          <p className="small">{ch.n} flown changes ({pct(ch.climb_share)} climbs, median transition {fmt(ch.transition_median_min)} min). Of {ch.n_reach_planned} that reach a filed level, {pct(ch.matched_share)} happen within the match distance of the filed waypoint (median {fmt(ch.median_dist_planned, 0)} NM away). {pct(ch.on_unplanned_share)} are on flights that filed no change.</p>
          <MapView height={360} deps={[ch]} draw={(L, map, g) => {
            const pts = ch.points || [];
            pts.forEach((x) => L.circleMarker([x.lat, x.lon], { radius: 4, weight: 1, color: x.matched ? "#1D7A46" : x.reaches_planned ? "#D98E1F" : "#3D5163", fillOpacity: 0.7 })
              .bindTooltip(`${x.kind}${x.matched ? ", at filed waypoint" : x.reaches_planned ? ", away from filed waypoint" : ", not in the plan"}`).addTo(g));
            return pts.length ? L.latLngBounds(pts.map((x) => [x.lat, x.lon])) : null;
          }} />
          <div className="legend"><span><i style={{ background: "#1D7A46" }} />at the filed waypoint</span><span><i style={{ background: "#D98E1F" }} />filed level, elsewhere</span><span><i style={{ background: "#3D5163" }} />not in the plan</span></div>
        </>) : <span className="muted">No cruise level changes in this filter.</span>}
      </div>
    </Panel>
  );
}

function LevelOffs({ S }) {
  const LO = S.leveloffs;
  const keys = [...new Set(LO.sensitivity.map((s) => `${s.phase}|${s.airport}`))];
  const nm = (k) => { const [ph, ap] = k.split("|"); return `${ph === "descent" ? "Descent into" : "Climb out of"} ${ap}`; };
  const durs = [...new Set(LO.sensitivity.map((s) => s.dur))].sort((a, b) => a - b);
  const sens = { ...base(), tooltip: { trigger: "axis", valueFormatter: (v) => `${v}%` }, xAxis: axis({ type: "category", data: durs.map((d) => `${d} s`), name: "minimum duration" }),
    yAxis: axis({ type: "value", name: "% of flights" }),
    series: keys.map((k, i) => ({ name: nm(k), type: "line", data: durs.map((d) => +(100 * (LO.sensitivity.find((s) => `${s.phase}|${s.airport}` === k && s.dur === d)?.share || 0)).toFixed(1)),
      lineStyle: { type: k.startsWith("climb") ? "dashed" : "solid", width: 2 }, color: COLORS[i] })) };
  const [h, setH] = useState(0);
  const hist = LO.hist[h];
  const histOpt = hist && { ...base(), tooltip: { trigger: "axis" }, xAxis: axis({ type: "category", data: hist.edges.map((e) => `${e / 1000}`), name: "thousand ft" }),
    yAxis: axis({ type: "value", name: "segments ≥ 60 s" }), series: [{ name: "level segments", type: "bar", data: hist.counts, color: COLORS[0] }] };
  return (
    <Panel id="leveloffs" title="Climb and descent level-offs (metrics 9–10)">
      <Table rows={LO.primary} columns={[{ label: "Phase", render: (r) => nm(`${r.phase}|${r.airport}`) }, { label: "Counted between", render: (r) => `${(r.bounds[0] || 0).toLocaleString()} ft – ${r.bounds[1] ? r.bounds[1].toLocaleString() + " ft" : r.phase === "climb" ? "TOC" : "TOD"}` },
        { key: "flights", label: "Flights", num: true }, { label: "With level-off", num: true, render: (r) => pct(r.share, 1) }, { label: "Per flight", num: true, render: (r) => fmt(r.mean_per_flight, 2) },
        { label: "Median duration (s)", num: true, render: (r) => fmt(r.median_dur, 0) }, { label: "Minutes level per flight", num: true, render: (r) => fmt(r.time_per_flight_min, 2) }]} />
      <div className="grid2" style={{ marginTop: 12 }}>
        <div id="leveloff_sensitivity"><h3>Share of flights by duration threshold</h3><Chart name="leveloff_sensitivity" option={sens} /></div>
        <div id="leveloff_hist">
          <div className="row" style={{ justifyContent: "space-between" }}><h3>Where level segments happen</h3>
            <select value={h} onChange={(e) => setH(Number(e.target.value))}>{LO.hist.map((x, i) => <option key={i} value={i}>{nm(`${x.phase}|${x.airport}`)}</option>)}</select></div>
          {histOpt && <Chart name="leveloff_altitude" option={histOpt} />}
        </div>
      </div>
      <h3>By altitude band (segments ≥ 60 s)</h3>
      <Table rows={LO.bands.filter((b) => b.flights)} columns={[{ label: "Phase", render: (r) => nm(`${r.phase}|${r.airport}`) }, { key: "band", label: "Band" }, { key: "flights", label: "Flights", num: true },
        { label: "Median duration (s)", num: true, render: (r) => fmt(r.median_dur, 0) }, { label: "Median distance (NM)", num: true, render: (r) => fmt(r.median_dist, 0) }, { label: "Minutes per flight", num: true, render: (r) => fmt(r.min_per_flight, 2) }]} />
    </Panel>
  );
}

function Flights({ runId, filters, onFlight }) {
  const [d, setD] = useState(null);
  const [q, setQ] = useState("");
  useEffect(() => { api.get(`/runs/${runId}/flights${qs(filters)}`).then(setD).catch(() => {}); }, [runId, JSON.stringify(filters)]);  // eslint-disable-line
  if (!d) return null;
  const rows = d.rows.filter((r) => !q || r.fid.includes(q.toUpperCase()));
  const label = { fid: "Flight", callsign: "Callsign", dof: "Date", dirn: "Direction", type: "Type", rfl: "RFL", cluster: "Cluster", outlier: "Outlier", has_plan: "Plan", has_ref: "Ref. route",
    holding: "Holding", toc_min: "TOC (min)", cruise_changes: "Level changes", capped_climb: "Capped climb", score_total: "Cruise score", time_above_min: "Min above", time_below_min: "Min below", arr_time_100: "Last 100 NM (min)" };
  return (
    <Panel title="Flights" sub={`${rows.length} flights · click a row to open the Flight view`} actions={<input placeholder="Search flight" value={q} onChange={(e) => setQ(e.target.value)} />}>
      <Table maxHeight={480} rows={rows} onRow={(r) => onFlight(r.fid)} columns={d.columns.map((c) => ({
        key: c, label: label[c] || c.replace("ext_", "Extension ").replace("time_", "En-route min ").replace(/_/g, " "), num: typeof d.rows[0]?.[c] === "number",
        render: (r) => (typeof r[c] === "boolean" ? (r[c] ? "yes" : "") : typeof r[c] === "number" ? fmt(r[c], Number.isInteger(r[c]) ? 0 : 1) : r[c] ?? "–") }))} />
    </Panel>
  );
}

/** Explains an empty or thin horizontal-efficiency section: plans whose filed route could not be expanded. */
function RefRouteNotice({ S }) {
  const withPlan = S.n.with_plan || 0;
  const withRef = S.n.with_ref || 0;
  if (!withPlan || withRef >= 0.8 * withPlan) return null;
  const blocking = Object.entries(S.meta?.unresolved_fixes || {}).filter(([k]) => !k.endsWith("(route end)")).sort((a, b) => b[1] - a[1]).slice(0, 6);
  return (
    <div className="notice" role="status">
      <b>Only {withRef} of {withPlan} flight plans have a usable reference route</b>, so the path extension figures {withRef ? "cover few flights" : "are empty"}.
      {blocking.length > 0 && <> The filed routes could not be expanded at: {blocking.map(([k, n]) => `${k} (${n} plans)`).join(", ")}.</>}
      {" "}An entry like <code>N875(ENREP-PNK)</code> means that airway in the aeronautical data does not contain both fixes. Check it under
      Aeronautical data, add the missing fixes or airway, then run the analysis again.
    </div>
  );
}
