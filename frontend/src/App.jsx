import React, { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { api, getToken, onSessionEnd, setToken } from "./api";
import { AuthProvider, can, useToast } from "./components/ui";
import Login from "./pages/Login";
import Projects from "./pages/Projects";
import ProjectShell from "./pages/ProjectShell";
import Account from "./pages/Account";
import Users from "./pages/admin/Users";
import Audit from "./pages/admin/Audit";
import Ais from "./pages/admin/Ais";
import Defaults from "./pages/admin/Defaults";

export default function App() {
  const [user, setUser] = useState(null);
  const [ready, setReady] = useState(false);
  const toast = useToast();
  const nav = useNavigate();
  useEffect(() => {
    onSessionEnd((msg) => { setUser(null); toast(msg, "error"); nav("/login"); });
    if (getToken()) {
      api.get("/auth/me").then(setUser).catch(() => setToken("")).finally(() => setReady(true));
    } else setReady(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  if (!ready) return null;
  if (!user) {
    return (
      <Routes>
        <Route path="*" element={<Login onLogin={(u) => setUser(u)} />} />
      </Routes>
    );
  }
  const logout = () => { setToken(""); setUser(null); nav("/login"); };
  return (
    <AuthProvider value={{ user, setUser, logout }}>
      {user.must_change_password ? <ForcedPassword /> : <Shell user={user} logout={logout} />}
    </AuthProvider>
  );
}

function ForcedPassword() {
  return (
    <div className="main" style={{ maxWidth: 520, margin: "6vh auto" }}>
      <h1 style={{ marginBottom: 8 }}>Set a new password</h1>
      <p className="muted">Your password was set by an administrator. Choose your own before you continue.</p>
      <Account forced />
    </div>
  );
}

function Shell({ user, logout }) {
  const loc = useLocation();
  const inProject = loc.pathname.match(/^\/projects\/(\d+)/);
  return (
    <div className="shell">
      <header className="topbar">
        <NavLink to="/" className="brand" style={{ textDecoration: "none" }}>
          <svg width="26" height="26" viewBox="0 0 32 32" aria-hidden="true"><circle cx="8" cy="22" r="3" fill="#5FB3BE" /><circle cx="24" cy="10" r="3" fill="#D98E1F" /><path d="M8 22 Q14 12 24 10" stroke="#fff" strokeWidth="2" fill="none" /></svg>
          CPTEA <small>City-Pair Trajectory Efficiency Analyser</small>
        </NavLink>
        <div className="spacer" />
        <NavLink to="/account" className="user">{user.full_name || user.username} · {user.role}</NavLink>
        <button className="link" onClick={logout}>Log out</button>
      </header>
      <nav className="side" aria-label="Main">
        <div className="group">
          <div className="group-title">Work</div>
          <NavLink end to="/" className="nav">Projects</NavLink>
          <NavLink to="/ais" className="nav">Aeronautical data</NavLink>
        </div>
        {inProject && <ProjectNav pid={inProject[1]} />}
        {can(user, "admin") && (
          <div className="group">
            <div className="group-title">Administration</div>
            <NavLink to="/admin/users" className="nav">Users</NavLink>
            <NavLink to="/admin/defaults" className="nav">Default parameters</NavLink>
            <NavLink to="/admin/audit" className="nav">Audit log</NavLink>
          </div>
        )}
        <VersionNote />
      </nav>
      <main className="main">
        <Routes>
          <Route path="/" element={<Projects />} />
          <Route path="/projects/:pid/*" element={<ProjectShell />} />
          <Route path="/account" element={<Account />} />
          <Route path="/ais" element={<Ais />} />
          <Route path="/admin/users" element={can(user, "admin") ? <Users /> : <Navigate to="/" />} />
          <Route path="/admin/audit" element={can(user, "admin") ? <Audit /> : <Navigate to="/" />} />
          <Route path="/admin/defaults" element={can(user, "admin") ? <Defaults /> : <Navigate to="/" />} />
          <Route path="*" element={<Navigate to="/" />} />
        </Routes>
      </main>
    </div>
  );
}

function ProjectNav({ pid }) {
  const items = [["", "Overview"], ["data", "Data"], ["settings", "Settings"], ["runs", "Runs"], ["results", "Results"],
    ["flight", "Flight view"], ["insights", "Insights"], ["reports", "Reports"], ["compare", "Compare runs"]];
  return (
    <div className="group">
      <div className="group-title">This project</div>
      {items.map(([p, l]) => <NavLink key={p} end={p === ""} to={`/projects/${pid}/${p}`} className="nav">{l}</NavLink>)}
    </div>
  );
}

/** Shows the version, and warns when the web interface was built from older code than the server runs. */
function VersionNote() {
  const [server, setServer] = useState(null);
  useEffect(() => { api.get("/config").then((c) => setServer(c.version)).catch(() => {}); }, []);
  const ui = typeof __APP_VERSION__ === "undefined" ? "?" : __APP_VERSION__;
  const stale = server && server !== ui;
  return (
    <div className="small muted" style={{ marginTop: "auto", padding: "16px 10px 6px" }}>
      {stale ? (
        <div className="badge warn" style={{ whiteSpace: "normal", lineHeight: 1.4 }}>
          The web interface (v{ui}) is older than the server (v{server}). Rebuild it: run <code>npm run build</code> in <code>frontend</code>,
          or <code>docker compose up -d --build</code>, then reload this page.
        </div>
      ) : <>CPTEA v{ui}</>}
    </div>
  );
}
