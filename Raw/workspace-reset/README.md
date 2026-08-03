# Workspace reset — automated Git sync (alternative to the manual portal steps)

Populate (or wipe-and-repopulate) a Microsoft Fabric workspace from this GitHub repo
**without clicking through the Fabric portal**. These tools drive the same Fabric
Git-integration APIs the portal uses, so the result is identical to a manual sync —
just scripted, repeatable, and safe to run from CI or a fresh machine.

Two ways to run everything:

- **CLI** — `sync_workspace_from_git.py` and `delete_workspace_items.py`.
- **Local web UI** — `webapp/server.py` serves a zero-build page that runs both
  scripts and streams live progress. No `npm`, no build step.

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

## Web UI (zero build)

```powershell
python -m pip install -r requirements.txt
az login
python webapp\server.py     # open http://127.0.0.1:5000
```

The page has forms for both scripts with a live progress bar, phase checklist, and
streaming log. The PAT is typed into a password field and passed only to the child
process' environment — it is never sent to a CLI flag, logged, or returned. The server
binds to `127.0.0.1` only. The delete form requires re-typing the workspace name to
confirm and defaults to dry-run.

## Security notes

- The PAT is never a command-line argument, never printed, and never returned by the
  web API — only via env var, hidden prompt, or the UI password field.
- Auto-created connections are deleted on exit unless you pass `--connection-id` or
  `--keep-connected`.
- Delete is guarded: confirmation prompt (CLI) / type-to-confirm + dry-run default (UI).
