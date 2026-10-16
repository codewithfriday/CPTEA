import React, { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { api } from "../api";

let aisCache = null;
/** Forget the cached AIS layers (after an import or a NavEarth refresh). */
export function resetAis() { aisCache = null; }
async function ais() {
  if (!aisCache) aisCache = api.get("/ais/geojson").catch(() => ({ features: [], fixes: [] }));
  return aisCache;
}
let landCache = null;
/** Offline coastline (Natural Earth 1:50m via world-atlas), drawn under the tiles so maps stay readable without internet. */
async function land() {
  if (!landCache) {
    landCache = Promise.all([import("world-atlas/land-50m.json"), import("topojson-client")])
      .then(([t, tc]) => tc.feature(t.default || t, (t.default || t).objects.land)).catch(() => null);
  }
  return landCache;
}
let tileUrl = null;
async function tiles() {
  if (tileUrl === null) {
    try { tileUrl = (await api.get("/config")).tile_url || ""; } catch { tileUrl = ""; }
  }
  return tileUrl;
}

export function ringLatLngs(lat, lon, nm) {
  const pts = [];
  for (let a = 0; a <= 360; a += 6) {
    const r = (a * Math.PI) / 180;
    pts.push([lat + (nm / 60) * Math.cos(r), lon + ((nm / 60) * Math.sin(r)) / Math.cos((lat * Math.PI) / 180)]);
  }
  return pts;
}

export function altColor(ft) {
  // light teal low, deep ink high
  const t = Math.max(0, Math.min(1, (ft || 0) / 41000));
  const a = [143, 208, 214], b = [19, 40, 58];
  return `rgb(${a.map((x, i) => Math.round(x + (b[i] - x) * t)).join(",")})`;
}

/** Imperative Leaflet map. `draw(L, map, layer)` adds content to a fresh layer group whenever `deps` change. */
export default function MapView({ draw, deps = [], tall, layers = true, height, show = ["FIR boundaries"] }) {
  const el = useRef(null);
  const map = useRef(null);
  const group = useRef(null);
  const base = useRef({});
  useEffect(() => {
    const m = L.map(el.current, { preferCanvas: true, zoomSnap: 0.25, worldCopyJump: true }).setView([3, 108], 4);
    map.current = m;
    m.createPane("land").style.zIndex = 150;
    land().then((g) => { if (g && map.current === m) L.geoJSON(g, { pane: "land", interactive: false, style: { color: "#9fb2bd", weight: 0.8, fillColor: "#e9eee8", fillOpacity: 1 } }).addTo(m); });
    tiles().then((u) => {
      if (u) L.tileLayer(u, { maxZoom: 12, attribution: "© OpenStreetMap contributors" }).addTo(m);
    });
    group.current = L.layerGroup().addTo(m);
    if (layers) {
      ais().then((g) => {
        const airways = L.geoJSON({ type: "FeatureCollection", features: g.features.filter((f) => f.properties.layer === "airway") },
          { style: { color: "#8aa0ae", weight: 1, opacity: 0.6, dashArray: "4 4" } });
        const firs = L.geoJSON({ type: "FeatureCollection", features: g.features.filter((f) => f.properties.layer === "airspace") },
          { style: { color: "#b0306a", weight: 1, fill: false, opacity: 0.6 },
            onEachFeature: (f, l) => l.bindTooltip(`${f.properties.name} ${f.properties.ansp || ""}`) });
        const fx = L.layerGroup((g.fixes || []).map((f) => L.circleMarker([f.lat, f.lon], { radius: 2, color: "#3d5163", weight: 1 })
          .bindTooltip(f.name)));
        const tmas = L.geoJSON({ type: "FeatureCollection", features: g.features.filter((f) => f.properties.layer === "tma") },
          { style: { color: "#6250d6", weight: 1, fill: true, fillOpacity: 0.04, opacity: 0.7 },
            onEachFeature: (f, l) => { const p = f.properties; l.bindTooltip(`<b>${p.name}</b>${p.class ? ` · class ${p.class}` : ""}${p.lower ? `<br>${p.lower} – ${p.upper || ""}` : ""}${p.ansp ? `<br>${p.ansp}` : ""}`); } });
        base.current = { "Airways": airways, "FIR boundaries": firs, "TMA boundaries": tmas, "Waypoints": fx };
        Object.entries(base.current).forEach(([k, l]) => show.includes(k) && l.addTo(m));
        L.control.layers(null, base.current, { collapsed: true }).addTo(m);
      });
    }
    return () => m.remove();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (!map.current) return;
    group.current.clearLayers();
    const bounds = draw && draw(L, map.current, group.current);
    if (bounds && bounds.isValid && bounds.isValid()) map.current.fitBounds(bounds, { padding: [20, 20] });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return <div ref={el} className={`mapbox ${tall ? "tall" : ""}`} style={height ? { height } : undefined} />;
}
