import React, { useState } from "react";
import { api } from "../api";
import { Panel, useAuth, useToast } from "../components/ui";

export default function Account({ forced }) {
  const { user, setUser } = useAuth();
  const toast = useToast();
  const [o, setO] = useState("");
  const [n, setN] = useState("");
  const [n2, setN2] = useState("");
  const save = async (e) => {
    e.preventDefault();
    if (n !== n2) return toast("The new passwords do not match", "error");
    try {
      await api.post("/auth/change-password", { old_password: o, new_password: n });
      toast("Password changed");
      setUser({ ...user, must_change_password: false });
      setO(""); setN(""); setN2("");
    } catch (x) { toast(x.message, "error"); }
  };
  return (
    <div style={{ maxWidth: 520 }}>
      {!forced && <h1 style={{ marginBottom: 14 }}>Your account</h1>}
      {!forced && (
        <Panel title={user.full_name || user.username} sub={`Role: ${user.role}`}>
          <div className="small muted">Username {user.username}</div>
        </Panel>
      )}
      <Panel title="Change password" sub="At least 10 characters">
        <form className="stack" onSubmit={save}>
          <label className="f">Current password<input type="password" value={o} onChange={(e) => setO(e.target.value)} autoComplete="current-password" /></label>
          <label className="f">New password<input type="password" value={n} onChange={(e) => setN(e.target.value)} autoComplete="new-password" /></label>
          <label className="f">Repeat new password<input type="password" value={n2} onChange={(e) => setN2(e.target.value)} autoComplete="new-password" /></label>
          <button className="btn" disabled={!o || n.length < 10}>Change password</button>
        </form>
      </Panel>
    </div>
  );
}
