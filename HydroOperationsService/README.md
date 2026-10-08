# Hydro durable orchestration: local validation milestone

**Not the production agent backend.** The Fabric app still uses its browser
orchestrator. This isolated Python service exercises Microsoft Agent Framework
1.19.0 workflows, typed handoffs, checkpoint recovery and human approval.
No Azure resources, cloud authentication changes or production SQL writes are made.

The default HTTP service has **no configured source/agent adapters**, returns HTTP 503 for
readiness/run submission, and reports `live_fabric_connected: false`. It does not
substitute bundled data for Fabric. Synthetic adapters exist only in tests.
The four framework executors are application steps, not evidence that four Foundry
agents have executed. No production UI is pointed at this service.
A separate read-only source adapter and diagnostic command now verify live Fabric
identities and exercise source access. They are not a complete Foundry provider or
a switch to production orchestration.

## Implemented boundary

```text
Authenticated local request + idempotency key
  -> durable run journal
  -> evidence adapter
  -> investigation adapter
  -> proposal adapter
  -> persisted human approval request
  -> reject OR atomic local validation record
```

- Explicit workflow edges replace model-directed routing for this one bounded
  inspection-review flow. Free-form chat routing and the remaining compound flows
  are not implemented here.
- Typed immutable handoffs preserve tenant/workspace/ontology/equipment identity,
  numeric generation 2, units, quality, timestamps and actual evidence references.
  No narrative-table parsing or native-source substitution is used.
- RCA conclusions are constrained to `cause_undetermined`. Schema/reference checks
  are not a causal model or proof that the agent selected relevant evidence.
- Agent Framework checkpoints save completed stages. SQLite records requests,
  approval decisions, outcomes and cursor-addressable execution events.
- Work starts independently of the HTTP request. Closing the connection does not
  cancel a run. Restart recovers queued/running work; waiting approvals remain
  waiting. Completed source reads are not repeated on checkpoint recovery.
- Model/source steps have deadlines. Failures are logged and recorded explicitly,
  not converted to empty evidence. Explicit retry is limited to two attempts;
  automatic source retry/circuit-breaker policy is not implemented.
- Approval is bound to the exact proposal digest and expiry. Decisions cannot be
  changed after recording. Concurrent duplicate approvals and replay after a
  committed operation produce one **local validation record**, never a Fabric WO.
- The terminal outcome and any local validation write commit in one transaction.
  Recovery uses that authoritative receipt if a process stops after the framework's
  final checkpoint but before the run journal is marked complete; it does not
  re-execute the operation or infer success from agent prose.

### Checkpoint failures are fatal

The pinned SDK logs checkpoint-save errors and continues by default. Hydro's storage
adapter retains the failure and blocks subsequent steps and successful completion.
It also refuses to silently skip a corrupted checkpoint and select an older one.
An already committed terminal business outcome can be recovered from its atomic
SQLite receipt without replaying the workflow; this recovery is explicitly journalled.
The checkpoint decoder allows only the explicit Hydro contract classes.
Checkpoint files must remain in a trusted, access-controlled local directory.

### Deliberately local

One worker process owns the state directory; a file lock rejects a second owner.
At most four workflows execute concurrently. This is not distributed hosting,
tenant isolation, Entra authentication, a managed durability SLA or a load-tested
enterprise service. The CLI binds only to `127.0.0.1`; there is no public CORS setup.
Local checkpoints contain synthetic validation evidence, not production analytical
data. Production evidence retention/access controls require a separate design.

## Live source adapter: verified access and blocking prerequisites

The Python reader uses delegated `AzureCliCredential` for local diagnostics.
A bounded Node subprocess reuses the installed Rayfin SDK, its supported
`signInWithEntraToken` helper, and the app's telemetry query builder. Tokens travel
only through process stdin; they are not command-line arguments, files, checkpoints
or log output. Rayfin sessions remain in memory and are destroyed after each read.
This is not a production OBO or managed-identity implementation.

Before reading, the adapter verifies live numeric ontology generation 2, exact
workspace/ontology/KQL identities, the KQL database's Eventhouse parent, and the
AppBackend endpoint against the workspace's current capacity and saved deployment.
It does not reconstruct or change deployment state. A configuration fingerprint
binds a request to its sources; a changed configuration fails before source reads.
The configured graph binding supplies the selected ontology ID only: this reader
does not query, fabricate or certify native graph topology.

STID resolves one exact equipment ID and its instrument IDs/units. The KQL query
returns the latest raw sample for each mapped signal within 24 hours, retains BAD
quality samples, and labels missing samples and readings older than 30 minutes.
It is not the fleet's 30-minute "running hot" query and does not invent a physical
fault or approved temperature limit. KQL completion-status errors/warnings and
truncated/ambiguous envelopes are rejected, not converted to partial success.
Work-order reads paginate up to 500 records and reject incomplete coverage,
duplicate identities or invalid cursors. Only Completed/Cancelled are excluded.
No SQL mutation method exists in this bridge.

From this service directory, using the operator's existing tenant-scoped Azure CLI
session and installed app dependencies (Node 24+):

```powershell
.\.venv\Scripts\python.exe -m hydro_orchestrator --probe-live-sources EQUIP_RTI_T005
```

If the canonical deployer used an isolated Azure CLI cache, select that existing
cache through `AZURE_CONFIG_DIR`; do not copy tokens or create another login cache.
`HYDRO_LOCAL_NODE` may select the installed Node executable.
This command does not start an HTTP server, save source data or invoke agents.
It exits nonzero when any source check fails. A passed read is not evidence of
fresh ingestion; inspect `missing_sources`.

**Observed live result, October 8:** verified ontology generation 2 and six
STID-mapped T005 measurements, explicitly labelled `fresh_telemetry` missing.
Work-order access failed with **`EXCHANGE_NOT_ENABLED`**. The installed Rayfin
guidance identifies `services.auth.fabric.externalEntraExchange: true` as the
setting for delegated exchange, but it was not changed or deployed.
The SDK also requires delegated `Item.Execute.All`, owning-tenant identity and
item Execute permission. The inspected Fabric token from the existing CLI session
lacked that named scope; enabling the setting alone is not a verified solution.
Approve and implement the supported delegated authentication path through the
canonical deployer before claiming end-to-end SQL access.

The workflow regression verifies that this failure produces a failed run with
no investigation, proposal or approvable card. It cannot become "no open work."
The read adapter is ready for composition only after its live prerequisites pass;
Foundry investigation/proposal adapters and SPA integration remain unimplemented.

## Run the checks

From this directory, with Python 3.12+:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements.lock -e ".[test]"
.\.venv\Scripts\python.exe -m pyright --project .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test ..\HydroOperationsApp\scripts\local-fabric-sources.test.mjs
```

The 24 Python tests use the actual Agent Framework engine, file checkpoints, SQLite transactions
and an ephemeral loopback HTTP listener. Providers are synthetic: these tests do not
certify Foundry, native Fabric endpoint availability or live WO creation.
Ten Node tests additionally cover live-reader contracts with mocked transports:
generation/capacity checks, KQL completion/partial failures, exact equipment mapping,
units, quality, freshness, source errors and work-order pagination.

The fault suite covers real process exit/recovery, HTTP disconnect, interrupted
investigation, a failure after a committed local write, checkpoint-write failure,
corrupt checkpoints, duplicate/concurrent approval, rejection, expiry, changed source
identity, malformed evidence, unknown evidence references and unexpected adapter bugs.

To inspect the intentionally unconfigured HTTP service:

```powershell
$env:HYDRO_LOCAL_API_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
.\.venv\Scripts\python.exe -m hydro_orchestrator
```

`GET http://127.0.0.1:8088/healthz` is liveness only. All run/readiness routes require
the local bearer token. `GET /readyz` returns 503 without configured adapters.
Do not put this token in the SPA, source control, a URL or a production configuration.

## Required before production migration

1. Approve cloud hosting/durable storage and its cost; select compatible runtime
   package versions and a managed recovery/availability model.
2. Implement and test server-side Entra authorization and the supported delegated
   access path for each Fabric/Foundry endpoint. Do not assume every native endpoint
   supports managed identity or app-only access.
3. Finish the live Foundry adapters and unblock/test the read adapter's SQL access
   against verified v2 identities, retaining source failures,
   direct-source evidence and native-source limitations. Never migrate by treating
   arbitrary model narrative as an authoritative dataset.
4. Implement production SQL idempotency and server-side approval authorization.
   The local SQLite transaction does not prove exactly-once effects against a
   separate remote database. Add a durable notification outbox and delivery receipts.
5. Wire the SPA to run IDs, progress events, reconnect and approval endpoints.
   Preserve card-first presentation; do not label application executors as actual
   Foundry invocations without their service receipts.
6. Benchmark representative workloads, reduce redundant calls/context, add bounded
   retry/circuit-breaker policies, and repeat the
   [ten compound prompts](../HydroOperationsApp/AGENT-TEST-PROMPTS.md), multi-turn and
   fault-injection suites on the same production candidate.
7. Integrate the new hosting/auth steps into the existing canonical deployer before
   any hosted rollout. There is no alternate deployment command for this service.

References: [workflows](https://learn.microsoft.com/en-us/agent-framework/workflows/),
[checkpointing](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints),
[durable hosting](https://learn.microsoft.com/en-us/agent-framework/hosting/azure-functions).
