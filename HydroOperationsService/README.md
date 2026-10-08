# Hydro durable orchestration: local validation milestone

**Not the production agent backend.** The Fabric app still uses its browser
orchestrator. This isolated Python service exercises Microsoft Agent Framework
1.19.0 workflows, typed handoffs, checkpoint recovery and human approval.
No Azure resources, cloud authentication changes or production SQL writes are made.

The default service has **no live source/agent adapters**, returns HTTP 503 for
readiness/run submission, and reports `live_fabric_connected: false`. It does not
substitute bundled data for Fabric. Synthetic adapters exist only in tests.
The four framework executors are application steps, not evidence that four Foundry
agents have executed. No production UI is pointed at this service.

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

## Run the checks

From this directory, with Python 3.12+:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -c requirements.lock -e ".[test]"
.\.venv\Scripts\python.exe -m pyright --project .
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use the actual Agent Framework engine, file checkpoints, SQLite transactions
and an ephemeral loopback HTTP listener. Providers are synthetic: these tests do not
certify Foundry, native Fabric endpoint availability or live WO creation.

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
3. Implement live adapters against verified v2 identities, retaining source failures,
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
