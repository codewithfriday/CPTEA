import React from "react";

function Chips({ options, value, onChange }) {
  const v = value || [];
  return (
    <div className="chips">
      {options.map((o) => (
        <button key={o} type="button" className={`chip ${v.includes(o) ? "on" : ""}`} aria-pressed={v.includes(o)}
          onClick={() => onChange(v.includes(o) ? v.filter((x) => x !== o) : [...v, o])}>{o}</button>
      ))}
    </div>
  );
}

/** One filter bar for every view (FR-VIS-01). */
export default function FilterBar({ options, value, onChange }) {
  if (!options) return null;
  const set = (k) => (v) => onChange({ ...value, [k]: v });
  return (
    <div className="filters" role="region" aria-label="Filters">
      <label className="f">Direction<Chips options={options.dirn} value={value.dirn} onChange={set("dirn")} /></label>
      <label className="f">Callsign<Chips options={options.callsign} value={value.callsign} onChange={set("callsign")} /></label>
      <label className="f">Aircraft type<Chips options={options.type} value={value.type} onChange={set("type")} /></label>
      {options.cluster && options.cluster.length > 1 && (
        <label className="f">Cluster<select value={value.cluster || ""} onChange={(e) => set("cluster")(e.target.value)}>
          <option value="">All</option>{options.cluster.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
      )}
      <label className="f">From<select value={value.month_from || ""} onChange={(e) => set("month_from")(e.target.value)}>
        <option value="">First month</option>{options.months.map((m) => <option key={m}>{m}</option>)}</select></label>
      <label className="f">To<select value={value.month_to || ""} onChange={(e) => set("month_to")(e.target.value)}>
        <option value="">Last month</option>{options.months.map((m) => <option key={m}>{m}</option>)}</select></label>
      <label className="f">Season<select value={value.season || ""} onChange={(e) => set("season")(e.target.value)}>
        <option value="">All</option><option value="DJF">Dec–Feb</option><option value="MAM">Mar–May</option><option value="JJA">Jun–Aug</option><option value="SON">Sep–Nov</option></select></label>
      <button className="btn ghost sm" type="button" onClick={() => onChange({})}>Clear filters</button>
    </div>
  );
}
