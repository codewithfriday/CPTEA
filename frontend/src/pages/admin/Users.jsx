import React, { useState } from "react";
import { api, dt } from "../../api";
import { Badge, Modal, Panel, Table, useAsync, useAuth, useToast } from "../../components/ui";

const ROLES = [["admin", "Administrator"], ["analyst", "Analyst"], ["reviewer", "Reviewer"], ["viewer", "Viewer"]];

export default function Users() {
  const { data, reload } = useAsync(() => api.get("/users"), []);
  const { user: me } = useAuth();
  const toast = useToast();
  const [edit, setEdit] = useState(null);
  const save = async () => {
    try {
      if (edit.id) await api.patch(`/users/${edit.id}`, { full_name: edit.full_name, role: edit.role, active: edit.active, password: edit.password || null });
      else await api.post("/users", edit);
      toast(edit.id ? `Saved ${edit.username}` : `Created ${edit.username}; they must change the password at first sign-in`);
      setEdit(null);
      reload();
    } catch (e) { toast(e.message, "error"); }
  };
  return (
    <div className="page">
      <h1 style={{ marginBottom: 14 }}>Users</h1>
      <Panel title="Users" sub="Administrators manage accounts. New accounts and password resets force a password change at the next sign-in."
        actions={<button className="btn" onClick={() => setEdit({ username: "", full_name: "", role: "analyst", password: "", active: true })}>Add user</button>}>
        <Table rows={data || []} onRow={(u) => setEdit({ ...u, password: "" })} columns={[
          { key: "username", label: "Username" }, { key: "full_name", label: "Name" },
          { label: "Role", render: (u) => ROLES.find((r) => r[0] === u.role)?.[1] || u.role },
          { label: "Status", render: (u) => (!u.active ? <Badge kind="bad">inactive</Badge> : u.locked_until && new Date(u.locked_until + "Z") > new Date() ? <Badge kind="warn">locked</Badge> : <Badge kind="good">active</Badge>) },
          { label: "Last sign-in", render: (u) => dt(u.last_login) }, { label: "Created", render: (u) => dt(u.created_at) }]} />
      </Panel>
      <Panel title="What each role can do">
        <Table rows={[
          { r: "Administrator", d: "Everything an Analyst can do, plus users, the system default parameters, aerodrome-level and admin-only parameters, airspace imports, NavEarth refresh and the audit log" },
          { r: "Analyst", d: "Create projects, load data, edit parameters, run analyses, edit insights and generate reports" },
          { r: "Reviewer", d: "Read everything shared with them and release reports that are in review" },
          { r: "Viewer", d: "Read results and download released reports in shared projects" }]} columns={[{ key: "r", label: "Role" }, { key: "d", label: "Permissions" }]} />
      </Panel>
      {edit && (
        <Modal title={edit.id ? `Edit ${edit.username}` : "Add user"} onClose={() => setEdit(null)}>
          <div className="stack">
            {!edit.id && <label className="f">Username<input value={edit.username} onChange={(e) => setEdit({ ...edit, username: e.target.value })} /></label>}
            <label className="f">Full name<input value={edit.full_name} onChange={(e) => setEdit({ ...edit, full_name: e.target.value })} /></label>
            <label className="f">Role<select value={edit.role} onChange={(e) => setEdit({ ...edit, role: e.target.value })}>{ROLES.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
            <label className="f">{edit.id ? "Reset password (leave empty to keep; also unlocks the account)" : "Initial password"}
              <input type="password" autoComplete="new-password" value={edit.password} onChange={(e) => setEdit({ ...edit, password: e.target.value })} /></label>
            <p className="small muted">At least 10 characters with letters and digits.</p>
            {edit.id && edit.id !== me.id && <label className="row small"><input type="checkbox" checked={edit.active} onChange={(e) => setEdit({ ...edit, active: e.target.checked })} /> Account active</label>}
            <div className="row end"><button className="btn ghost" onClick={() => setEdit(null)}>Cancel</button><button className="btn" onClick={save}>{edit.id ? "Save user" : "Create user"}</button></div>
          </div>
        </Modal>
      )}
    </div>
  );
}
