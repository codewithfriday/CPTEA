import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, setToken } from "../api";

export default function Login({ onLogin }) {
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const nav = useNavigate();
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setErr("");
    try {
      const r = await api.post("/auth/login", { username: u, password: p });
      setToken(r.token);
      onLogin(r.user);
      nav("/");
    } catch (x) {
      setErr(x.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="login">
      <div className="art">
        <div className="brand" style={{ fontSize: 15 }}>AirNav Indonesia · APAC TBO Pathfinder WG3</div>
        <div>
          <svg viewBox="0 0 420 120" width="100%" style={{ maxWidth: 460 }} aria-hidden="true">
            <circle cx="40" cy="90" r="30" fill="none" stroke="#5FB3BE" strokeDasharray="4 5" />
            <circle cx="380" cy="30" r="30" fill="none" stroke="#5FB3BE" strokeDasharray="4 5" />
            <path d="M40 90 C140 70 250 50 380 30" stroke="#D98E1F" strokeWidth="2.5" fill="none" />
            <path d="M40 90 C150 86 260 44 380 30" stroke="#ffffff" strokeOpacity=".35" fill="none" />
            <path d="M40 90 C130 60 270 60 380 30" stroke="#ffffff" strokeOpacity=".35" fill="none" />
            <circle cx="40" cy="90" r="4" fill="#fff" /><circle cx="380" cy="30" r="4" fill="#fff" />
          </svg>
          <h1>City-pair trajectory<br />efficiency, flight by flight</h1>
          <p>Load surveillance tracks and flight plans, measure every metric of the End-to-End method, and report what the data says.</p>
        </div>
        <div className="small" style={{ color: "#8fa6b5" }}>CPTEA 1.0</div>
      </div>
      <div className="form">
        <form className="box stack" onSubmit={submit}>
          <h2>Log in</h2>
          <label className="f">Username<input autoFocus value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" /></label>
          <label className="f">Password<input type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" /></label>
          {err && <div className="badge bad" style={{ whiteSpace: "normal" }}>{err}</div>}
          <button className="btn" disabled={busy || !u || !p}>{busy ? "Logging in…" : "Log in"}</button>
          <p className="muted small">Accounts are created by your administrator. After 5 failed attempts the account is locked for 15 minutes.</p>
        </form>
      </div>
    </div>
  );
}
