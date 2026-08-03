import { useState } from "react";
import { useJob } from "./useJob";
import { ProgressPanel } from "./ProgressPanel";

export default function App() {
  // Shared identity
  const [tenant, setTenant] = useState("");
  const [workspace, setWorkspace] = useState("");

  // Sync form
  const [repository, setRepository] = useState("");
  const [branch, setBranch] = useState("main");
  const [directory, setDirectory] = useState("/");
  const [connectionId, setConnectionId] = useState("");
  const [keepConnected, setKeepConnected] = useState(false);
  const [pat, setPat] = useState("");
  const syncJob = useJob();
  const syncBusy = syncJob.state.status === "running";

  // Delete form
  const [dryRun, setDryRun] = useState(true);
  const [confirmText, setConfirmText] = useState("");
  const deleteJob = useJob();
  const deleteBusy = deleteJob.state.status === "running";

  const syncValid =
    tenant.trim() && workspace.trim() && repository.includes("/") &&
    (connectionId.trim() || pat.trim());

  // Deleting (not dry-run) requires re-typing the workspace to confirm.
  const deleteValid =
    tenant.trim() && workspace.trim() &&
    (dryRun || confirmText.trim().toLowerCase() === workspace.trim().toLowerCase());

  async function runSync() {
    await syncJob.start("/api/sync", {
      tenant, workspace, repository, branch, directory,
      connectionId, keepConnected, pat,
    });
    setPat(""); // never keep the secret in memory longer than needed
  }

  async function runDelete() {
    await deleteJob.start("/api/delete", { tenant, workspace, dryRun });
    setConfirmText("");
  }

  return (
    <div className="page">
      <header>
        <h1>Fabric Workspace Reset</h1>
        <p className="sub">
          Local UI for the sync &amp; delete scripts. Auth uses your{" "}
          <code>az login</code> session. Runs on localhost only.
        </p>
      </header>

      <section className="card">
        <h2>Identity</h2>
        <div className="grid">
          <label>
            Tenant id or domain
            <input value={tenant} onChange={(e) => setTenant(e.target.value)}
              placeholder="contoso.onmicrosoft.com or GUID" />
          </label>
          <label>
            Workspace (GUID or name)
            <input value={workspace} onChange={(e) => setWorkspace(e.target.value)}
              placeholder="ws-… or a display name" />
          </label>
        </div>
      </section>

      <section className="card">
        <h2>Sync workspace from GitHub</h2>
        <div className="grid">
          <label>
            Repository (owner/repo)
            <input value={repository} onChange={(e) => setRepository(e.target.value)}
              placeholder="owner/repo" />
          </label>
          <label>
            Branch
            <input value={branch} onChange={(e) => setBranch(e.target.value)} />
          </label>
          <label>
            Directory (workspace root)
            <input value={directory} onChange={(e) => setDirectory(e.target.value)} />
          </label>
          <label>
            GitHub PAT {connectionId.trim() ? "(not needed — reusing connection)" : ""}
            <input type="password" value={pat} autoComplete="off"
              disabled={!!connectionId.trim()}
              onChange={(e) => setPat(e.target.value)}
              placeholder="ghp_… (kept out of history & disk)" />
          </label>
          <label>
            Reuse connection id (optional)
            <input value={connectionId} onChange={(e) => setConnectionId(e.target.value)}
              placeholder="existing Fabric GitHub connection GUID" />
          </label>
          <label className="check">
            <input type="checkbox" checked={keepConnected}
              onChange={(e) => setKeepConnected(e.target.checked)} />
            Keep connected after sync
          </label>
        </div>
        <button className="primary" disabled={!syncValid || syncBusy} onClick={runSync}>
          {syncBusy ? "Syncing…" : "Run sync"}
        </button>
        <ProgressPanel job={syncJob.state} />
      </section>

      <section className="card danger">
        <h2>Delete all workspace items</h2>
        <label className="check">
          <input type="checkbox" checked={dryRun}
            onChange={(e) => setDryRun(e.target.checked)} />
          Dry run (list only, delete nothing)
        </label>
        {!dryRun && (
          <label>
            Type the workspace to confirm deletion
            <input value={confirmText} onChange={(e) => setConfirmText(e.target.value)}
              placeholder="re-enter the workspace GUID or name" />
          </label>
        )}
        <button className={dryRun ? "primary" : "destructive"}
          disabled={!deleteValid || deleteBusy} onClick={runDelete}>
          {deleteBusy ? "Working…" : dryRun ? "List items (dry run)" : "Delete everything"}
        </button>
        <ProgressPanel job={deleteJob.state} />
      </section>
    </div>
  );
}
