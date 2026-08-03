# Workspace reset — automated Git sync (alternative to the manual portal steps)

Populate (or wipe-and-repopulate) a Microsoft Fabric workspace from this GitHub repo
**without clicking through the Fabric portal**. These tools drive the same Fabric
Git-integration APIs the portal uses, so the result is identical to a manual sync —
just scripted, repeatable, and safe to run from CI or a fresh machine.

Two ways to run everything:

- **CLI** — scripts for Git sync, pipeline execution, Rayfin app deployment, and deletion.
- **Local web UI** — `webapp/server.py` serves a zero-build page that runs the
  workflows and streams live progress.

## Easiest start — just launch it (no commands to type)

One-time setup on the machine: install **[Python](https://www.python.org/downloads/)**
(on Windows, tick *"Add python.exe to PATH"*) and the
**[Azure CLI](https://aka.ms/installazurecli)**.

Then launch the web app:

- **Windows** — double-click **`Start Fabric Demo.cmd`**.
- **macOS** — double-click **`start-fabric-demo.sh`** (first time you may need to make
  it runnable: in Terminal run `chmod +x start-fabric-demo.sh`), or run
  `bash start-fabric-demo.sh`.
- **Linux** — run `bash start-fabric-demo.sh` (or `chmod +x start-fabric-demo.sh`
  once, then `./start-fabric-demo.sh`).
- **Any OS** — the shims above are thin wrappers around one cross-platform launcher,
  so you can equally run `python launch.py` (Windows) / `python3 launch.py` (macOS/Linux).

The launcher installs any missing dependencies, signs you in to Azure if needed
(`az login` opens your browser), starts the app, and **opens your browser at
`http://127.0.0.1:5000`** automatically. Keep the window open while you use it; close
it (or press Ctrl+C) to stop.

## What manual sync this replaces

In the Fabric portal the manual flow is:

1. **Workspace → Settings → Git integration** → connect to GitHub, pick the repo,
   branch, and directory, providing a Git credential.
2. **Source control → Update all** to import every item into the workspace.
3. (Optionally) **Disconnect** so the workspace is populated but no longer Git-linked.

`sync_workspace_from_git.py` performs exactly that cycle
(`connect → initializeConnection (PreferRemote) → updateFromGit → disconnect`) over
the REST API. Because Git integration mirrors folders, items land in same-named
workspace folders (`Notebooks/`, `Orchestrator_Pipelines/`, …) and **reuses any empty
folders that already exist** — which also clears the "empty folder that won't delete"
situation.

## Prerequisites

- **`az login`** first (optionally `az login --tenant <tenant>`). Both scripts use
  your current Azure CLI sign-in to get a Fabric token — whoever you are signed in as
  is the identity that performs the changes.
- **Workspace Admin** on the target workspace (required to connect / sync / delete).
- **Python deps:** `python -m pip install -r requirements.txt`
  (`azure-identity`, `requests`, `flask`).
- **Node.js/npm available on PATH** for app deployment. The deploy action automatically
  runs the Hydro Operations app under the repository's required Node 24 wrapper.
- Permission to create an Entra app registration when the target tenant does not already
  contain `Hydro Operations Fabric Client`. The deploy action reuses the existing SPA
  registration when exactly one is present.
- **GitHub PAT** for the sync (unless you reuse an existing connection) with **`repo`**
  scope (classic) or fine-grained **Contents: Read** on the repo. The PAT is never a
  CLI flag and never logged — it comes from an env var or a hidden prompt.

## Configuration (CLI flag > env var > interactive prompt)

Every value can come from a flag, an environment variable, or an interactive prompt —
in that order of precedence. Run either script with no arguments to be prompted for
everything required.

| Env var | Used by | Meaning |
| --- | --- | --- |
| `FABRIC_TENANT` | both | Tenant id (GUID) or domain, e.g. `contoso.onmicrosoft.com` |
| `FABRIC_WORKSPACE` | both | Workspace GUID or display name |
| `FABRIC_REPOSITORY` | sync | GitHub repo as `owner/repo` (or a bare repo name) |
| `FABRIC_OWNER` | sync | GitHub owner/org (optional if `FABRIC_REPOSITORY` includes it) |
| `FABRIC_BRANCH` | sync | Branch to sync from (default `main`) |
| `FABRIC_DIRECTORY` | sync | Repo directory mapped to the workspace root (default `/`) |
| `FABRIC_CONNECTION_ID` | sync | Reuse an existing Fabric GitHub connection instead of creating one |
| `FABRIC_GIT_PAT` / `GITHUB_PAT` | sync | GitHub PAT (only if not reusing a connection) |

## Sync a workspace from Git

Fully interactive — prompts for everything:

```powershell
az login
python sync_workspace_from_git.py
```

Non-interactive with flags (PAT still comes from `FABRIC_GIT_PAT`/`GITHUB_PAT` or a
hidden prompt — never a flag):

```powershell
python sync_workspace_from_git.py `
  --tenant <tenant-guid> `
  --workspace <workspace-guid-or-name> `
  --owner dibakardharchoudhury --repository FabricOntologyHydro `
  --branch main --directory / --yes
```

Fully env-driven (keeps the PAT out of shell history):

```powershell
$env:FABRIC_TENANT     = "<tenant-guid>"
$env:FABRIC_WORKSPACE  = "<workspace-guid-or-name>"
$env:FABRIC_REPOSITORY = "dibakardharchoudhury/FabricOntologyHydro"
$env:FABRIC_GIT_PAT    = (Read-Host -AsSecureString | ConvertFrom-SecureString -AsPlainText)
python sync_workspace_from_git.py --yes
```

By default the script creates a fresh connection named `FabricOntologyDemo_<UTC timestamp>`
from the PAT and **deletes it again on exit**. Pass `--connection-id` (or
`FABRIC_CONNECTION_ID`) to reuse an existing connection; then no PAT is requested.
Pass `--keep-connected` to leave the workspace Git-linked after the sync.

Flags: `--tenant --workspace --connection-id --owner --repository --branch --directory --yes --keep-connected`.

## Delete every item in a workspace (start clean)

Destructive and effectively irreversible — by default it lists what it will delete
and asks you to confirm. `--tenant` and `--workspace` are required (flag or env var).

```powershell
az login
$env:FABRIC_TENANT = "<tenant-guid>"; $env:FABRIC_WORKSPACE = "<workspace>"
python delete_workspace_items.py --dry-run    # preview only
python delete_workspace_items.py --yes        # actually delete
```

Flags: `--tenant --workspace --yes --dry-run`.

## Deploy the Hydro Operations Fabric app

The **Deploy app** tab runs the complete Rayfin application deployment against the
tenant and workspace selected in the sidebar:

1. Validate the active Azure CLI tenant and resolve the exact workspace GUID/name.
2. Reuse the tenant's `Hydro Operations Fabric Client` SPA, create it when absent,
   or use the optional client ID entered in the form.
3. Reuse matching active Rayfin state for idempotent redeploys; otherwise back up and
  reset stale state, then generate a fresh ignored `rayfin/.env`.
4. Sign Rayfin into the target tenant, provision the AppBackend and SQL schema, build
   and deploy static hosting, and apply the generated hosting origin to backend auth.
5. Run `npm run setup-live-auth` for SPA redirects, delegated ADX/Fabric permissions,
  and consent, then verify the live app returns HTML over HTTP 200. The final check
  also reads the AppBackend from Fabric and verifies the SPA redirect, every delegated
  scope, and its consent grant from Microsoft Graph. Per-user consent is reported as
  a degraded fallback; enterprise rollout requires tenant-wide `AllPrincipals` consent.
6. When **Commit generated hosting origin** is selected, fetch Fabric commit-back,
   commit only `HydroOperationsApp/rayfin/rayfin.yml`, and push `main`. This option
   requires a clean checkout and refuses divergent/unpushed local commits.

Rayfin may open a browser account picker during the job. The setup pipeline and app
deployment remain separate actions so the pipeline's Key Vault and Teams parameters
can be reviewed before execution.

Deployment is exclusive: while it runs, the local server rejects sync, delete,
pipeline, login, and additional deploy jobs with HTTP 409. The server accepts only
loopback clients and validates local Host/Origin headers to prevent remote use,
cross-origin invocation, and DNS-rebinding through a non-local host name.

The same workflow is available from the CLI:

```powershell
python deploy_fabric_app.py `
  --tenant <tenant-guid> `
  --workspace <workspace-guid-or-name> `
  --push-config
```

Add `--client-id <spa-app-guid>` when more than one matching SPA registration exists.

## Web UI (zero build)

```powershell
python -m pip install -r requirements.txt
az login
python webapp\server.py     # open http://127.0.0.1:5000
```

The page has actions for Git sync, setup-pipeline execution, Hydro Operations app
deployment, and workspace deletion, each with a live progress bar, phase checklist,
and streaming log. The PAT is typed into a password field and passed only to the child
process' environment — it is never sent to a CLI flag, logged, or returned. The server
binds to `127.0.0.1` only. The delete form requires re-typing the workspace name to
confirm and defaults to dry-run.

## Security notes

- The PAT is never a command-line argument, never printed, and never returned by the
  web API — only via env var, hidden prompt, or the UI password field.
- Auto-created connections are deleted on exit unless you pass `--connection-id` or
  `--keep-connected`.
- Delete is guarded: confirmation prompt (CLI) / type-to-confirm + dry-run default (UI).
