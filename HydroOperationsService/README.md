# Hydro durable orchestration: local validation milestone

For the full October 7-8 implementation history, agent roles, hosted/local
distinction and production gates, see the
[Foundry and multi-agent implementation record](../docs/foundry-multi-agent-implementation.md).

**Not the production agent backend.** The Fabric app still uses its browser
orchestrator. This isolated Python service exercises Microsoft Agent Framework
1.19.0 workflows, typed handoffs, checkpoint recovery and human approval.
No Azure resources, cloud authentication changes or production SQL writes are made.

The default HTTP service has **no configured source/agent adapters**, returns HTTP 503 for
readiness/run submission, and reports `live_fabric_connected: false`. It does not
substitute bundled data for Fabric. Synthetic adapters exist only in tests.
The four framework executors are application steps, not evidence that four Foundry
agents have executed. No production UI is pointed at this service.
Separate read-only source and Foundry RCA adapters now verify live Fabric identities,
exercise source access and invoke the existing persistent Sleuth agent. They are not
a complete multi-agent provider or a switch to production orchestration.

## Implemented boundary

```text
Authenticated local request + idempotency key
  -> durable run journal
  -> evidence adapter
  -> investigation adapter
  -> proposal adapter
  -> durable no-draft/clarification outcome OR persisted human approval request
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
- A proposal requires an explicit completed work-order read, including when zero
  rows were returned. Its expiry cannot exceed 15 minutes from that read. A resumed
  workflow cannot turn old coverage into a new 15-minute approval window.
  The reader records the clock when SQL pagination completes, not when a slower
  parallel telemetry request eventually finishes. Source latency cannot extend
  the work-coverage approval window.
- The terminal outcome and any local validation write commit in one transaction.
  Recovery uses that authoritative receipt if a process stops after the framework's
  final checkpoint but before the run journal is marked complete; it does not
  re-execute the operation or infer success from agent prose.
- Typed `no_draft` and `needs_clarification` outcomes bind the immutable assessment
  and any specialist receipt, bypass approval, and cannot create a validation work
  record. A no-draft decision requires completed work-source coverage. Identical
  replay returns the persisted decision; changed outcomes fail. This is a tested
  local adapter contract, not an implemented live Fixer adapter.

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
session and installed app dependencies (Node 24):

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
An explicit Azure CLI scope request returned `AADSTS65002`: that first-party client
is not preauthorized for this scope. This is not repaired by repeatedly signing in
or granting customer-tenant admin consent. The documented `RayfinAuth` silent-token
alternative was also tested and had no usable cached login. A supported interactive
Rayfin identity or a properly authorized server-side delegated/OBO path is still
required; no browser token extraction, app-only substitution or auth bypass was used.
Approve and implement the supported delegated authentication path through the
canonical deployer before claiming end-to-end SQL access.

The workflow regression verifies that this failure produces a failed run with
no investigation, proposal or approvable card. It cannot become "no open work."
The complete read adapter is ready for composition only after its live prerequisites
pass. The independent telemetry-only investigation below does not substitute for
the failed work-order read. The proposal adapter and SPA integration remain pending.

## Live scientific RCA adapter

The RCA adapter reads back the existing `hydro-rca-agent`, pins its returned version,
and checks its completion-tool schema against the app's shared `RCA_REPORT_TOOL`.
Foundry rejects a request-level `tools` override when an agent reference is supplied;
the adapter uses the verified persisted tool and a forced completion choice instead.
It makes one model invocation, with no automatic retry or model-directed query loop.
HTTP errors, deadlines, incomplete output, narrative answers and unexpected/duplicate
calls fail explicitly.

The returned report passes the same `parseRcaAssessment` implementation used by the
SPA. References must point into actual supplied source receipts; invented paths,
free-text diagnoses and duplicate hypotheses are rejected. The typed assessment
retains supporting/contradictory references, missing evidence, a source-input digest,
the requested agent version and the service response/request IDs. Checkpoint recovery
preserves this report without repeating the completed invocation. Reference validity
does not prove causal relevance: the conclusion remains `cause_undetermined`.

```powershell
$env:HYDRO_FOUNDRY_PROJECT_ENDPOINT = '<existing project endpoint>'
.\.venv\Scripts\python.exe -m hydro_orchestrator --probe-live-rca EQUIP_RTI_T005
```

This explicit **telemetry-only** diagnostic never reads SQL, proposes work or sends
notifications. It marks work orders/inspections as not requested rather than claiming
none exist. The proposal contract independently rejects this incomplete coverage.

**Observed live result, October 8:** Sleuth v13 processed six verified T005 samples
in one invocation lasting **14.38 seconds**, using 2,392 input and 1,728 output
tokens. Response ID: `resp_0a5e2d14804d33bb016ac7b88f67f081939ff231b4f22fe555`;
request ID: `2fea9529-d38a-448d-8091-57ee2de118c4`. Its three competing hypotheses
remained untested; stale telemetry, missing maintenance/inspection evidence,
approved limits and matched-baseline gaps were retained. This timing is for the
model invocation, not end-to-end source discovery or an interactive performance SLA.
It is one actual specialist call, not a completed multi-agent workflow.

Older local validation proposals without an explicit work-read clock are no longer
approvable under the strengthened contract. Preserve their state for audit and start
a new run/state directory; do not rewrite receipts or delete deployment state.

## Run the checks

From this directory, with Python 3.12+:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements.lock -e ".[test]"
.\.venv\Scripts\python.exe -m pyright --project .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --test ..\HydroOperationsApp\scripts\local-fabric-sources.test.mjs
```

The 34 Python tests use the actual Agent Framework engine, file checkpoints, SQLite transactions
and an ephemeral loopback HTTP listener. Providers are synthetic: these tests do not
certify Foundry, native Fabric endpoint availability or live WO creation.
Eleven Node tests additionally cover live-reader contracts with mocked transports:
generation/capacity checks, KQL completion/partial failures, exact equipment mapping,
units, quality, freshness, source errors, work-order pagination and its independent
completion clock. Pages larger than the requested 100 records are rejected.
The RCA tests run the real shared JavaScript parser against mocked Foundry responses,
including schema drift, invalid references, output failures and typed-report recovery.
The live diagnostic above is separate evidence, not a mocked test result.
After the 34-test full Python run, an additional expiry regression was added;
the affected RCA suite now passes 11 tests, including the case where telemetry
finishes two minutes after SQL and only 13 approval minutes remain.

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
