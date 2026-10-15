import React, { useMemo, useState } from "react";
import { Tabs } from "./ui";

const fmtVal = (v) => (Array.isArray(v) ? v.join(", ") : typeof v === "object" && v !== null ? JSON.stringify(v) : String(v));

function Field({ d, value, onChange, disabled }) {
  if (d.type === "choice") {
    return <select value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)}>{d.options.map((o) => <option key={o} value={o}>{o}</option>)}</select>;
  }
  if (d.type === "list") {
    return <input value={Array.isArray(value) ? value.join(", ") : value} disabled={disabled}
      onChange={(e) => onChange(e.target.value)} onBlur={(e) => onChange(e.target.value.split(/[,;]/).map((x) => x.trim()).filter(Boolean).map(Number))} />;
  }
  if (d.type === "dict") {
    return (
      <div className="formgrid" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(110px, 1fr))" }}>
        {Object.entries(value || {}).map(([k, v]) => (
          <label className="f" key={k}>{k}<input type="number" step="any" value={v} disabled={disabled} onChange={(e) => onChange({ ...value, [k]: Number(e.target.value) })} /></label>
        ))}
      </div>
    );
  }
  if (d.type === "number" || d.type === "int") {
    return <input type="number" step={d.type === "int" ? 1 : "any"} min={d.min ?? undefined} max={d.max ?? undefined} value={value} disabled={disabled}
      onChange={(e) => onChange(e.target.value === "" ? "" : Number(e.target.value))} />;
  }
  return <input value={value} disabled={disabled} onChange={(e) => onChange(e.target.value)} />;
}

function invalid(d, v) {
  if ((d.type === "number" || d.type === "int") && (v === "" || Number.isNaN(Number(v)))) return "Enter a number";
  if ((d.type === "number" || d.type === "int") && ((d.min !== null && v < d.min) || (d.max !== null && v > d.max))) return `Allowed ${d.min}–${d.max}`;
  if (d.type === "list" && Array.isArray(v) && v.some((x) => Number.isNaN(x))) return "Comma-separated numbers";
  return "";
}

/** FR-SET-01..04, 06: tabs, defaults, ranges, sources, reset, affected steps. */
export default function ParamEditor({ defs, values, base, onChange, readOnly, isAdmin }) {
  const [tab, setTab] = useState(defs.tabs[0]);
  const byTab = useMemo(() => defs.params.filter((d) => d.tab === tab), [defs, tab]);
  const changedVsBase = (k) => JSON.stringify(values[k]) !== JSON.stringify(base[k]);
  const changedVsDefault = (k) => JSON.stringify(values[k]) !== JSON.stringify(defs.defaults[k]);
  const set = (k, v) => onChange({ ...values, [k]: v });
  const resetTab = () => onChange({ ...values, ...Object.fromEntries(byTab.map((d) => [d.id, defs.defaults[d.id]])) });
  const resetAll = () => onChange({ ...defs.defaults, "FPL-04": values["FPL-04"] });
  const changed = defs.params.filter((d) => changedVsBase(d.id));
  const steps = [...new Set(changed.flatMap((d) => d.steps))];
  const errors = defs.params.map((d) => [d.id, invalid(d, values[d.id])]).filter(([, e]) => e);
  return (
    <div>
      <Tabs tabs={defs.tabs.map((t) => ({ id: t, label: `${t}${defs.params.some((d) => d.tab === t && changedVsBase(d.id)) ? " •" : ""}` }))} value={tab} onChange={setTab} />
      {!readOnly && (
        <div className="row" style={{ marginBottom: 6 }}>
          <button className="btn ghost sm" onClick={resetTab}>Reset this tab to defaults</button>
          <button className="btn ghost sm" onClick={resetAll}>Reset all to defaults</button>
          {changed.length > 0 && <span className="small muted">{changed.length} unsaved change{changed.length > 1 ? "s" : ""}; re-runs: {steps.join(", ")}</span>}
          {errors.length > 0 && <span className="badge bad">{errors.length} invalid</span>}
        </div>
      )}
      {byTab.map((d) => {
        const err = invalid(d, values[d.id]);
        const lock = readOnly || (d.admin_only && !isAdmin);
        return (
          <div key={d.id} className={`param ${changedVsDefault(d.id) ? "changed" : ""}`}>
            <div>
              <div><b>{d.label}</b> <span className="id">{d.id}</span></div>
              <div className="help">{d.help}</div>
              <div className="help">Default {fmtVal(d.default)} {d.unit} · {d.min !== null && d.max !== null ? `range ${d.min}–${d.max} · ` : ""}source {d.source}{d.admin_only ? " · administrators only" : ""}</div>
            </div>
            <div>
              <Field d={d} value={values[d.id]} disabled={lock} onChange={(v) => set(d.id, v)} />
              {d.unit && <div className="help">{d.unit}</div>}
              {err && <div className="badge bad">{err}</div>}
            </div>
            <div>{!lock && changedVsDefault(d.id) && <button className="btn ghost sm" onClick={() => set(d.id, defs.defaults[d.id])}>Reset</button>}</div>
          </div>
        );
      })}
    </div>
  );
}

export const hasErrors = (defs, values) => defs.params.some((d) => invalid(d, values[d.id]));
