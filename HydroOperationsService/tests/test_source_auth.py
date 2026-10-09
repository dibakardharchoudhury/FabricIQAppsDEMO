import asyncio
import json
import os
import socket
import subprocess
import sys
import unittest
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from time import time
from types import SimpleNamespace
from uuid import UUID, uuid4
from unittest.mock import AsyncMock, Mock, patch

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from pydantic import SecretStr, ValidationError

from hydro_orchestrator.contracts import SourceIdentity, WorkOrderDraft, utc_now
from hydro_orchestrator.live_sources import FabricBackendTools, LiveSources, NodeSourceBridge, SourceFailure
from hydro_orchestrator.foundry_supervisor import ChatAnswer, FoundrySupervisor, NativeBinding, RunJournal
from hydro_orchestrator.service import ChatInput, RuntimeConfiguration, create_delegated_app, create_fabric_delegated_app, create_runtime_app, decide_work_order, normalized_chat_input
from hydro_orchestrator.source_auth import (
    DelegatedTokens, SourceAuthPolicy, SourceAuthorizationError, SourceTokenVerifier,
)
from hydro_orchestrator.__main__ import prepare_runtime_user

TENANT = UUID("11111111-1111-4111-8111-111111111111")
SPA = UUID("22222222-2222-4222-8222-222222222222")
USER = UUID("33333333-3333-4333-8333-333333333333")


class RuntimeUserTests(unittest.TestCase):
    def test_local_runtime_without_container_account_is_unchanged(self):
        with patch.dict(os.environ, {"HYDRO_RUNTIME_USER": ""}), patch.object(Path, "mkdir") as mkdir:
            prepare_runtime_user(Path("local-fixture"))
            mkdir.assert_not_called()

    def test_container_initializes_only_dedicated_state_and_drops_all_root_ids(self):
        account = SimpleNamespace(pw_uid=10001, pw_gid=10001)
        manager = Mock()
        with patch.dict(os.environ, {"HYDRO_RUNTIME_USER": "hydro"}), \
                patch("hydro_orchestrator.__main__.sys.platform", "linux"), \
                patch.dict(sys.modules, {"pwd": SimpleNamespace(getpwnam=Mock(return_value=account))}), \
                patch.object(Path, "is_symlink", return_value=False), \
                patch.object(Path, "mkdir") as mkdir, patch.object(Path, "chmod") as chmod, \
                patch("hydro_orchestrator.__main__.os.chown", create=True) as chown, \
                patch("hydro_orchestrator.__main__.os.geteuid", side_effect=[0, 10001], create=True), \
                patch("hydro_orchestrator.__main__.os.getegid", return_value=10001, create=True), \
                patch("hydro_orchestrator.__main__.os.setgroups", create=True) as setgroups, \
                patch("hydro_orchestrator.__main__.os.setgid", create=True) as setgid, \
                patch("hydro_orchestrator.__main__.os.setuid", create=True) as setuid:
            for name, operation in (("mkdir", mkdir), ("chmod", chmod), ("chown", chown),
                                    ("setgroups", setgroups), ("setgid", setgid), ("setuid", setuid)):
                manager.attach_mock(operation, name)
            prepare_runtime_user(Path("/home/session/hydro"))
            self.assertEqual(chown.call_args_list[0].args, (Path("/home/session"), 10001, 10001))
            self.assertEqual(chown.call_args_list[1].args, (Path("/home/session/hydro"), 10001, 10001))
            self.assertEqual([call[0] for call in manager.mock_calls],
                             ["mkdir", "chown", "chmod", "mkdir", "chown", "chmod",
                              "setgroups", "setgid", "setuid"])
            setgroups.assert_called_once_with([])
            setgid.assert_called_once_with(10001)
            setuid.assert_called_once_with(10001)

    def test_container_rejects_arbitrary_or_symlink_state_before_ownership_changes(self):
        account = SimpleNamespace(pw_uid=10001, pw_gid=10001)
        with patch.dict(os.environ, {"HYDRO_RUNTIME_USER": "hydro"}), \
                patch("hydro_orchestrator.__main__.sys.platform", "linux"), \
                patch.dict(sys.modules, {"pwd": SimpleNamespace(getpwnam=Mock(return_value=account))}), \
                patch("hydro_orchestrator.__main__.os.geteuid", return_value=0, create=True), \
                patch("hydro_orchestrator.__main__.os.chown", create=True) as chown:
            with self.assertRaises(PermissionError):
                prepare_runtime_user(Path("/tmp/not-owned-by-the-runtime"))
            with patch.object(Path, "is_symlink", return_value=True), self.assertRaises(PermissionError):
                prepare_runtime_user(Path("/home/session/hydro"))
            chown.assert_not_called()


class SourceAuthTests(unittest.IsolatedAsyncioTestCase):
    async def test_backend_intent_fills_only_omitted_options_and_cannot_change_the_question(self):
        intent = {"charts_requested": True, "native_sources": ["ontology"], "proposal_priority": "High"}
        chat = ChatInput(question="Show a chart.")
        normalized = normalized_chat_input(chat, intent)
        self.assertEqual(normalized.question, chat.question)
        self.assertTrue(normalized.charts_requested)
        self.assertEqual(normalized.native_sources, ("ontology",))
        explicit = ChatInput(question=chat.question, charts_requested=False,
                             native_sources=("data-agent",), proposal_priority="Low")
        self.assertEqual(normalized_chat_input(explicit, intent), explicit)
        for invalid in ({**intent, "question": "Changed"}, {**intent, "charts_requested": "true"}, {}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                normalized_chat_input(chat, invalid)
        result = await NodeSourceBridge().call({"action": "chat_intent", "question": chat.question})
        self.assertTrue(normalized_chat_input(chat, result).charts_requested)

    async def asyncSetUp(self):
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public = jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key(), as_dict=True)
        public.update(kid="test-key", use="sig")
        self.reads = 0

        def respond(request):
            self.reads += 1
            self.assertEqual(str(request.url), f"https://login.microsoftonline.com/{TENANT}/discovery/v2.0/keys")
            self.assertNotIn("Authorization", request.headers)
            return httpx.Response(200, json={"keys": [public]})

        self.client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        self.policy = SourceAuthPolicy(
            tenant_id=TENANT, spa_client_id=SPA,
            audiences={"fabric": ("https://fabric.test",), "foundry": ("https://foundry.test",)},
            scopes={"fabric": "https://fabric.test/.default", "foundry": "https://foundry.test/.default"},
        )
        self.verifier = SourceTokenVerifier(self.policy, self.client)
        self.deadline = utc_now() + timedelta(minutes=3)

    async def asyncTearDown(self):
        await self.client.aclose()

    def token(self, kind, **updates):
        now = int(time())
        claims = {"exp": now + 3600, "iat": now - 30, "nbf": now - 30, "tid": str(TENANT),
                  "oid": str(USER), "appid": str(SPA), "aud": self.policy.audiences[kind][0],
                  "iss": f"https://sts.windows.net/{TENANT}/", "scp": "user_impersonation"}
        claims.update(updates)
        return jwt.encode(claims, self.private, algorithm="RS256", headers={"kid": "test-key"})

    def body(self, **fabric_updates):
        return DelegatedTokens(tokens={
            "fabric": SecretStr(self.token("fabric", **fabric_updates)),
            "foundry": SecretStr(self.token("foundry")),
        })

    async def test_verified_token_leases_are_user_bound_and_never_serialized(self):
        body = self.body()
        credential = await self.verifier.verify(body, self.deadline)
        self.assertEqual(credential.principal_id, USER)
        token = await credential.get_token(self.policy.scopes["fabric"])
        self.assertEqual(token.token, body.tokens["fabric"].get_secret_value())
        self.assertEqual(self.reads, 1)
        self.assertNotIn(token.token, repr(body))
        self.assertNotIn(token.token, body.model_dump_json())
        async with credential as same:
            self.assertIs(same, credential)
            self.assertEqual((await same.get_token(self.policy.scopes["fabric"])).token, token.token)
        with self.assertRaisesRegex(SourceAuthorizationError, "needs a delegated token"):
            await credential.get_token(self.policy.scopes["fabric"])

    async def test_wrong_signature_audience_tenant_client_user_and_app_only_fail(self):
        cases = [
            {"aud": "https://attacker.test"},
            {"tid": "44444444-4444-4444-8444-444444444444"},
            {"iss": "https://attacker.test/"},
            {"appid": "44444444-4444-4444-8444-444444444444"},
            {"oid": "44444444-4444-4444-8444-444444444444"},
            {"idtyp": "app"},
            {"scp": ""},
            {"oid": 17},
            {"tid": 17},
        ]
        for claims in cases:
            with self.subTest(claims=claims):
                with self.assertRaises(SourceAuthorizationError):
                    await self.verifier.verify(self.body(**claims), self.deadline)
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = self.token("fabric")
        claims = jwt.decode(token, options={"verify_signature": False})
        forged = jwt.encode(claims, other, algorithm="RS256", headers={"kid": "test-key"})
        body = self.body()
        body.tokens["fabric"] = SecretStr(forged)
        with self.assertRaises(SourceAuthorizationError):
            await self.verifier.verify(body, self.deadline)

    async def test_expiring_lease_missing_source_and_unconfigured_scope_fail(self):
        with self.assertRaises(SourceAuthorizationError):
            await self.verifier.verify(self.body(exp=int(time()) + 60), self.deadline)
        credential = await self.verifier.verify(self.body(), self.deadline)
        with self.assertRaises(SourceAuthorizationError):
            await credential.get_token("https://attacker.test/.default")
        with self.assertRaises(SourceAuthorizationError):
            await credential.get_token(self.policy.scopes["fabric"], self.policy.scopes["foundry"])
        with self.assertRaises(SourceAuthorizationError):
            await credential.get_token(self.policy.scopes["fabric"], claims="source-challenge")
        with self.assertRaises(SourceAuthorizationError):
            await credential.get_token(self.policy.scopes["fabric"], tenant_id="another-tenant")
        with self.assertRaises(SourceAuthorizationError):
            await self.verifier.verify(DelegatedTokens(tokens={
                "foundry": SecretStr(self.token("foundry")), "graphql": SecretStr("not-used"),
            }), self.deadline)
        await credential.close()

    async def test_parallel_verification_coalesces_tenant_signing_key_reads(self):
        leases = await asyncio.gather(*(self.verifier.verify(self.body(), self.deadline) for _ in range(8)))
        self.assertEqual(self.reads, 1)
        self.assertTrue(all(item.principal_id == USER for item in leases))
        for lease in leases:
            await lease.close()

    async def test_unknown_signing_keys_cannot_force_unbounded_jwks_refreshes(self):
        lease = await self.verifier.verify(self.body(), self.deadline)
        await lease.close()
        for index in range(10):
            with self.assertRaises(SourceAuthorizationError):
                await self.verifier.key(f"untrusted-{index}")
        self.assertEqual(self.reads, 1)

    def invocation(self, user=USER):
        return {"operation": "run", "chat": {"question": "Review actual source evidence."},
                "tokens": {kind: self.token(kind, oid=str(user)) for kind in ("fabric", "foundry")}}

    def ingress(self, root, *, run=None, timeout=300, capacity=4, alter_owner=None,
                draft_factory=None, approval_tools=None):
        source = SourceIdentity(
            tenant_id=TENANT, workspace_id=UUID("44444444-4444-4444-8444-444444444444"),
            ontology_id=UUID("55555555-5555-4555-8555-555555555555"), generation=2,
            configuration_digest="a" * 64,
        )
        leases, namespaces, contexts = [], [], []

        @asynccontextmanager
        async def factory(state, credential):
            leases.append(credential)
            namespaces.append(state)
            event_sink = None

            async def execute(request):
                if event_sink is not None:
                    event_sink({"id": f"{request.run_id}:chief", "role": "supervisor", "status": "running",
                                "label": "Chief", "detail": "Coordinating.", "timestamp": 1})
                if run is not None:
                    return await run(request)
                draft = draft_factory(request) if draft_factory is not None else None
                answer = ChatAnswer(
                    run_id=request.run_id, source=source, requested_at=request.requested_at,
                    source_read_times=(), summary="Transport fixture, not an operational answer.",
                    tables=(), charts=(), limitations=(), cell_sources=(), specialists=(),
                    audit_url=f"/chat/runs/{request.run_id}/evidence",
                    proposals=(draft,) if draft is not None else (),
                    proposal_digests={str(draft.id): draft.digest()} if draft is not None else {},
                )
                journal = RunJournal(state / str(request.run_id) / "receipts")
                journal.save("request", request.model_dump(mode="json"))
                journal.save("answer", answer.model_dump(mode="json"))
                journal.save("evidence", {"request": request.model_dump(mode="json")})
                if event_sink is not None:
                    event_sink({"id": f"{request.run_id}:chief", "role": "supervisor", "status": "completed",
                                "label": "Chief", "detail": "Committed.", "timestamp": 2,
                                "agentName": "hydro-supervisor-agent", "responseId": "resp_test"})
                return answer

            def set_event_sink(sink):
                nonlocal event_sink
                event_sink = sink

            try:
                owner = SimpleNamespace(root=state, source=source, credential=credential,
                                        run=execute, set_event_sink=set_event_sink)
                if alter_owner is not None:
                    alter_owner(owner)
                yield owner
            finally:
                contexts.append("closed")

        async def decide(state, credential, run_id, decision):
            return await decide_work_order(state, source, credential, run_id, decision, approval_tools)

        async def reconcile(state, credential, run_id, decision):
            return await decide_work_order(state, source, credential, run_id, decision, approval_tools,
                                           require_previous=True)

        app = create_delegated_app(root, self.verifier, source, factory,
                                   run_timeout=timeout, max_concurrent_runs=capacity,
                                   decision_handler=decide if draft_factory is not None else None,
                                   reconciliation_handler=reconcile if draft_factory is not None else None)
        return app, leases, namespaces, contexts

    def approval_draft(self, request):
        clock = utc_now()
        return WorkOrderDraft(id=uuid4(), run_id=request.run_id, source=request.source,
                              equipment_id="T1", title="Inspect signal", description="Operator-requested inspection.",
                              priority="Medium", work_read_at=clock, expires_at=clock + timedelta(minutes=15),
                              existing_work=())

    async def test_signed_human_approval_is_idempotent_isolated_and_cannot_be_changed_after_submission(self):
        tools = AsyncMock(spec=FabricBackendTools)
        tools.approve.return_value = {"id": "source-confirmed-record"}
        with TemporaryDirectory() as directory:
            app, leases, namespaces, _ = self.ingress(Path(directory), draft_factory=self.approval_draft,
                                                      approval_tools=tools)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                initial = self.invocation()
                run = await client.post("/invocations", json=initial)
                draft = WorkOrderDraft.model_validate(run.json()["proposals"][0])
                self.assertEqual(run.json()["proposal_digests"][str(draft.id)], draft.digest())
                decision = {"proposal_id": str(draft.id), "proposal_digest": draft.digest(), "approved": True,
                            "edits": {"title": "Reviewed inspection", "description": "Approved human scope.", "priority": "High"}}
                body = {"operation": "decide", "run_id": run.json()["run_id"], "decision": decision,
                        "tokens": initial["tokens"]}
                corrupt = await client.post("/invocations", json={**body, "decision": {**decision, "proposal_digest": "0" * 64}})
                self.assertEqual(corrupt.status_code, 409)
                other = self.invocation(uuid4())
                denied = await client.post("/invocations", json={**body, "tokens": other["tokens"]})
                self.assertEqual(denied.status_code, 404)
                created = await client.post("/invocations", json=body)
                self.assertEqual(created.status_code, 200, created.text)
                self.assertTrue(created.json()["production_write_executed"])
                duplicate = await client.post("/invocations", json=body)
                self.assertEqual(duplicate.json(), created.json())
                audit = await client.post("/invocations", json={
                    "operation": "evidence", "run_id": body["run_id"], "tokens": initial["tokens"],
                })
                self.assertEqual(audit.json()["work_order_decisions"][0]["result"]["status"], "created")
                tools.approve.assert_awaited_once()
                self.assertTrue(tools.approve.call_args.kwargs["allow_create"])
                self.assertEqual(tools.approve.call_args.args[2], USER)
                changed = await client.post("/invocations", json={**body, "decision": {
                    **decision, "edits": {**decision["edits"], "priority": "Critical"},
                }})
                self.assertEqual(changed.status_code, 409)
            persisted = "".join(path.read_text() for path in namespaces[0].rglob("*.json"))
            for token in initial["tokens"].values():
                self.assertNotIn(token, persisted)
            for lease in leases:
                with self.assertRaises(SourceAuthorizationError):
                    await lease.get_token(self.policy.scopes["fabric"])

    async def test_rejection_disabled_writes_and_expired_cards_never_submit_sql(self):
        tools = AsyncMock(spec=FabricBackendTools)
        with TemporaryDirectory() as directory:
            app, _, _, _ = self.ingress(Path(directory), draft_factory=self.approval_draft)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                initial = self.invocation()
                run = await client.post("/invocations", json=initial)
                draft = WorkOrderDraft.model_validate(run.json()["proposals"][0])
                decision = {"proposal_id": str(draft.id), "proposal_digest": draft.digest(), "approved": True,
                            "edits": {"title": draft.title, "description": draft.description, "priority": draft.priority}}
                body = {"operation": "decide", "run_id": run.json()["run_id"], "decision": decision,
                        "tokens": initial["tokens"]}
                disabled = await client.post("/invocations", json=body)
                self.assertEqual(disabled.status_code, 503)
                rejected = await client.post("/invocations", json={**body, "decision": {
                    **decision, "approved": False, "edits": None,
                }})
                self.assertEqual(rejected.status_code, 200)
                self.assertEqual(rejected.json()["status"], "rejected")
                self.assertFalse(rejected.json()["production_write_executed"])
                self.assertEqual((await client.post("/invocations", json=body)).status_code, 409)
            def expired(request):
                draft = self.approval_draft(request)
                return draft.model_copy(update={"work_read_at": utc_now() - timedelta(minutes=20),
                                                 "expires_at": utc_now() - timedelta(minutes=5)})
            app, _, _, _ = self.ingress(Path(directory) / "expired", draft_factory=expired, approval_tools=tools)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                run = await client.post("/invocations", json=initial)
                draft = WorkOrderDraft.model_validate(run.json()["proposals"][0])
                body.update(run_id=run.json()["run_id"], decision={**decision, "proposal_id": str(draft.id),
                                                                 "proposal_digest": draft.digest()})
                self.assertEqual((await client.post("/invocations", json=body)).status_code, 409)
            tools.approve.assert_not_awaited()

    async def test_uncertain_submission_only_reconciles_and_known_prewrite_failure_is_explicit(self):
        tools = AsyncMock(spec=FabricBackendTools)
        tools.approve.side_effect = [SourceFailure("Write response lost"), {"id": "reconciled"}]
        with TemporaryDirectory() as directory:
            app, _, _, _ = self.ingress(Path(directory), draft_factory=self.approval_draft, approval_tools=tools)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                initial = self.invocation()
                async def card():
                    run = await client.post("/invocations", json=initial)
                    draft = WorkOrderDraft.model_validate(run.json()["proposals"][0])
                    return {"operation": "decide", "run_id": run.json()["run_id"], "tokens": initial["tokens"],
                            "decision": {"proposal_id": str(draft.id), "proposal_digest": draft.digest(), "approved": True,
                                         "edits": {"title": draft.title, "description": draft.description, "priority": draft.priority}}}
                body = await card()
                unsubmitted = await client.post("/invocations", json={**body, "operation": "reconcile"})
                self.assertEqual(unsubmitted.status_code, 409)
                self.assertFalse(unsubmitted.json()["detail"]["production_write_executed"])
                tools.approve.assert_not_awaited()
                uncertain = await client.post("/invocations", json=body)
                self.assertEqual(uncertain.status_code, 409)
                self.assertEqual(uncertain.json()["detail"]["status"], "uncertain")
                self.assertEqual((await client.post("/invocations", json={**body, "operation": "reconcile"})).json()["status"], "created")
                calls = tools.approve.await_args_list
                self.assertEqual([call.kwargs["allow_create"] for call in calls], [True, False])
                self.assertEqual(calls[0].args[3], calls[1].args[3])
                tools.approve.reset_mock()
                tools.approve.side_effect = SourceFailure("An open duplicate exists. No SQL write was attempted.",
                                                         write_attempted=False)
                body = await card()
                blocked = await client.post("/invocations", json=body)
                self.assertEqual(blocked.status_code, 409)
                self.assertEqual(blocked.json()["detail"]["status"], "blocked")
                self.assertFalse(blocked.json()["detail"]["production_write_executed"])
                self.assertEqual((await client.post("/invocations", json=body)).status_code, 409)
                tools.approve.assert_awaited_once()

    async def test_followup_context_is_bounded_isolated_historical_and_cleared_on_new_chat(self):
        with TemporaryDirectory() as directory:
            app, _, namespaces, _ = self.ingress(Path(directory))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                first = self.invocation()
                initial = await client.post("/invocations", json=first)
                run_id = initial.json()["run_id"]
                followup = {**first, "chat": {"question": "What work is open on those?", "previous_run_id": run_id}}
                response = await client.post("/invocations", json=followup)
                self.assertEqual(response.status_code, 200, response.text)
                saved = RunJournal(namespaces[-1] / response.json()["run_id"] / "receipts").read("request")
                self.assertEqual(saved["historical_context"]["run_id"], run_id)
                self.assertEqual(saved["historical_context"]["question"], first["chat"]["question"])
                self.assertEqual(saved["historical_context"]["proposal_ids"], [])
                foreign = self.invocation(UUID("66666666-6666-4666-8666-666666666666"))
                denied = await client.post("/invocations", json={**followup, "tokens": foreign["tokens"]})
                self.assertEqual(denied.status_code, 404)
                self.assertNotIn(first["chat"]["question"], denied.text)
                with patch("hydro_orchestrator.service.utc_now", return_value=utc_now() + timedelta(minutes=31)):
                    self.assertEqual((await client.post("/invocations", json=followup)).status_code, 409)
                fresh = await client.post("/invocations", json=first)
                self.assertEqual(fresh.status_code, 200)
                saved = RunJournal(namespaces[-1] / fresh.json()["run_id"] / "receipts").read("request")
                self.assertIsNone(saved["historical_context"])

    async def test_ingress_isolates_users_and_audits_and_clears_tokens(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            app, leases, namespaces, contexts = self.ingress(root)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                first = self.invocation()
                response = await client.post("/invocations", json=first)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["audit_url"], "/invocations")
                run_id = response.json()["run_id"]
                audit = {"operation": "evidence", "run_id": run_id, "tokens": first["tokens"]}
                receipt = await client.post("/invocations", json=audit)
                self.assertEqual(receipt.status_code, 200, receipt.text)
                self.assertNotIn("tokens", receipt.json()["request"])
                other = self.invocation(UUID("66666666-6666-4666-8666-666666666666"))
                denied = await client.post("/invocations", json={**audit, "tokens": other["tokens"]})
                self.assertEqual(denied.status_code, 404)
                second = await client.post("/invocations", json=other)
                self.assertEqual(second.status_code, 200, second.text)
            self.assertNotEqual(namespaces[0], namespaces[1])
            self.assertEqual(contexts, ["closed", "closed"])
            for lease in leases:
                with self.assertRaises(SourceAuthorizationError):
                    await lease.get_token(self.policy.scopes["fabric"])
            persisted = "".join(path.read_text() for path in root.rglob("*.json"))
            for raw in first["tokens"].values():
                self.assertNotIn(raw, persisted)

    async def test_streaming_ingress_emits_backend_events_and_one_certified_answer(self):
        with TemporaryDirectory() as directory:
            app, leases, _, contexts = self.ingress(Path(directory))
            invocation = {**self.invocation(), "stream": True}
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                response = await client.post("/invocations", json=invocation)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("application/x-ndjson", response.headers["content-type"])
            messages = [json.loads(line) for line in response.text.splitlines()]
            self.assertEqual([message["type"] for message in messages],
                             ["run", "event", "event", "answer"])
            self.assertEqual(messages[1]["event"]["status"], "running")
            self.assertEqual(messages[2]["event"]["status"], "completed")
            self.assertEqual(messages[-1]["answer"]["presentation"]["schema_version"], 1)
            self.assertEqual(contexts, ["closed"])
            with self.assertRaises(SourceAuthorizationError):
                await leases[0].get_token(self.policy.scopes["fabric"])

    async def test_streaming_failure_terminates_chief_without_disclosing_exception_text(self):
        async def fail(_request):
            raise RuntimeError("private diagnostic content")

        with TemporaryDirectory() as directory:
            app, _, _, contexts = self.ingress(Path(directory), run=fail)
            invocation = {**self.invocation(), "stream": True}
            with self.assertLogs("hydro_orchestrator.service", level="ERROR") as logs:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://test") as client:
                    response = await client.post("/invocations", json=invocation)
            messages = [json.loads(line) for line in response.text.splitlines()]
            self.assertEqual([message["type"] for message in messages],
                             ["run", "event", "event", "error"])
            self.assertEqual(messages[-2]["event"]["status"], "error")
            self.assertEqual(messages[-2]["event"]["id"], messages[1]["event"]["id"])
            self.assertEqual(messages[-1]["status"], 500)
            combined = response.text + "\n".join(logs.output)
            self.assertIn("RuntimeError", combined)
            self.assertNotIn("private diagnostic content", combined)
            self.assertEqual(contexts, ["closed"])

    async def test_ingress_validation_and_auth_errors_never_echo_token_input(self):
        with TemporaryDirectory() as directory:
            app, leases, _, _ = self.ingress(Path(directory))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                valid = self.invocation()
                invalid = {**valid, "unexpected": valid["tokens"]["fabric"]}
                response = await client.post("/invocations", json=invalid)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(valid["tokens"]["fabric"], response.text)
                expired = self.invocation()
                expired["tokens"]["fabric"] = self.token("fabric", exp=int(time()) - 1)
                response = await client.post("/invocations", json=expired)
                self.assertEqual(response.status_code, 401)
                self.assertNotIn(expired["tokens"]["fabric"], response.text)
                response = await client.post("/invocations", content=b"x" * 100001)
                self.assertEqual(response.status_code, 413)
            self.assertEqual(leases, [])

    async def test_ingress_timeout_closes_context_and_credentials(self):
        async def never_finishes(request):
            await asyncio.Event().wait()

        with TemporaryDirectory() as directory:
            app, leases, _, contexts = self.ingress(Path(directory), run=never_finishes, timeout=0.05)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                response = await client.post("/invocations", json=self.invocation())
            self.assertEqual(response.status_code, 504, response.text)
            self.assertEqual(contexts, ["closed"])
            with self.assertRaises(SourceAuthorizationError):
                await leases[0].get_token(self.policy.scopes["fabric"])

    async def test_ingress_rejects_factory_credential_or_namespace_substitution(self):
        for alter in (
            lambda owner: setattr(owner, "credential", object()),
            lambda owner: setattr(owner, "root", Path("another-user")),
        ):
            with self.subTest(alter=alter), TemporaryDirectory() as directory:
                root = Path(directory)
                app, leases, _, contexts = self.ingress(root, alter_owner=alter)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://test") as client:
                    response = await client.post("/invocations", json=self.invocation())
                self.assertEqual(response.status_code, 500, response.text)
                self.assertEqual(contexts, ["closed"])
                receipts = list(root.rglob("*.json"))
                self.assertEqual(len(receipts), 1)
                self.assertIn('"status":"failed"', receipts[0].read_text())
                with self.assertRaises(SourceAuthorizationError):
                    await leases[0].get_token(self.policy.scopes["fabric"])

    async def test_failed_runs_have_sanitized_same_user_audits(self):
        for status, category, error in (
            (500, "execution", RuntimeError("sensitive-source-response")),
            (502, "source_request", httpx.ConnectError("sensitive-source-response")),
            (401, "authorization", SourceAuthorizationError("sensitive-source-response")),
        ):
            with self.subTest(category=category), TemporaryDirectory() as directory:
                root = Path(directory)
                invocation = self.invocation()

                async def fail(request):
                    raise error

                app, _, _, _ = self.ingress(root, run=fail)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://test") as client:
                    response = await client.post("/invocations", json=invocation)
                    self.assertEqual(response.status_code, status, response.text)
                    detail = response.json()["detail"]
                    self.assertTrue(detail["audit_available"])
                    audit = {"operation": "evidence", "run_id": detail["run_id"],
                             "tokens": invocation["tokens"]}
                    receipt = await client.post(detail["audit_url"], json=audit)
                    self.assertEqual(receipt.status_code, 200, receipt.text)
                    self.assertEqual(receipt.json()["status"], "failed")
                    self.assertEqual(receipt.json()["category"], category)
                    self.assertEqual(receipt.json()["run_id"], detail["run_id"])
                    other = self.invocation(UUID("66666666-6666-4666-8666-666666666666"))
                    denied = await client.post("/invocations", json={**audit, "tokens": other["tokens"]})
                    self.assertEqual(denied.status_code, 404)
                persisted = "".join(path.read_text() for path in root.rglob("*.json"))
                self.assertNotIn("sensitive-source-response", persisted + response.text + receipt.text)
                for token in invocation["tokens"].values():
                    self.assertNotIn(token, persisted + response.text + receipt.text)

    async def test_failure_audit_storage_errors_are_explicit_and_sanitized(self):
        async def fail(request):
            raise RuntimeError("sensitive-source-response")

        with TemporaryDirectory() as directory:
            app, leases, _, contexts = self.ingress(Path(directory), run=fail)
            with patch.object(RunJournal, "save", side_effect=OSError("sensitive-storage-response")):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://test") as client:
                    response = await client.post("/invocations", json=self.invocation())
            self.assertEqual(response.status_code, 500, response.text)
            self.assertFalse(response.json()["detail"]["audit_available"])
            self.assertNotIn("sensitive-storage-response", response.text)
            self.assertNotIn("sensitive-source-response", response.text)
            self.assertEqual(contexts, ["closed"])
            with self.assertRaises(SourceAuthorizationError):
                await leases[0].get_token(self.policy.scopes["fabric"])

    async def test_fabric_factory_wires_verified_delegated_sources_and_rejects_changed_readback(self):
        self.policy.scopes["fabric"] = "https://api.fabric.microsoft.com/.default"
        source = SourceIdentity(
            tenant_id=TENANT, workspace_id=UUID("44444444-4444-4444-8444-444444444444"),
            ontology_id=UUID("55555555-5555-4555-8555-555555555555"), generation=2,
            configuration_digest="a" * 64,
        )
        owners = []
        native_binding = NativeBinding(source=source, connections={"data-agent": "test-native-connection"})

        async def execute(owner, request):
            owners.append(owner)
            self.assertIsInstance(owner.tools, FabricBackendTools)
            self.assertIs(owner.tools.sources.credential, owner.credential)
            self.assertEqual(owner.credential.principal_id, USER)
            self.assertEqual(owner.native_binding, native_binding)
            return ChatAnswer(
                run_id=request.run_id, source=request.source, requested_at=request.requested_at,
                source_read_times=(), summary="Composition fixture, not a live source answer.",
                tables=(), charts=(), limitations=(), cell_sources=(), specialists=(),
                audit_url=f"/chat/runs/{request.run_id}/evidence",
            )

        for changed in (False, True):
            with self.subTest(changed=changed), TemporaryDirectory() as directory:
                actual = source.model_copy(update={"workspace_id": SPA}) if changed else source
                bridge = AsyncMock()
                bridge.call.side_effect = [
                    {"charts_requested": False, "native_sources": [], "proposal_priority": "Medium"},
                    {"tenant_id": str(TENANT), "configuration_digest": source.configuration_digest},
                    {"source": actual.model_dump(mode="json"), "cluster": "https://cluster.test", "database": "test"},
                ]
                app = create_fabric_delegated_app(
                    Path(directory), self.verifier, source,
                    "https://project.services.ai.azure.com/api/projects/hydro", bridge=bridge,
                    native_binding=native_binding,
                )
                with patch("hydro_orchestrator.live_sources.AzureCliCredential",
                           side_effect=AssertionError("Hosted source factory cannot instantiate CLI authentication.")):
                    with patch.object(FoundrySupervisor, "run", autospec=True, side_effect=execute) as run:
                        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                                     base_url="http://test") as client:
                            response = await client.post("/invocations", json=self.invocation())
                        self.assertEqual(response.status_code, 500 if changed else 200, response.text)
                        self.assertEqual(run.call_count, 0 if changed else 1)
        self.assertEqual(len(owners), 1)
        with self.assertRaises(SourceAuthorizationError):
            await owners[0].credential.get_token(self.policy.scopes["fabric"])

    async def test_runtime_bootstrap_requires_explicit_matching_configuration_and_closes_http_client(self):
        policy = self.policy.model_copy(update={"scopes": {
            "fabric": "https://api.fabric.microsoft.com/.default", "foundry": "https://ai.azure.com/.default",
        }})
        source = SourceIdentity(tenant_id=TENANT, workspace_id=SPA, ontology_id=USER,
                                generation=2, configuration_digest="a" * 64)
        endpoint = "https://test.services.ai.azure.com/api/projects/test"
        runtime = RuntimeConfiguration(source=source, authorization=policy, project_endpoint=endpoint)
        configured = {"tenant_id": str(TENANT), "workspace_id": str(SPA), "ontology_id": str(USER),
                      "configuration_digest": source.configuration_digest}
        bridge = AsyncMock()
        bridge.call.return_value = configured
        with TemporaryDirectory() as directory:
            tracked_client = httpx.AsyncClient()
            with patch("hydro_orchestrator.service.httpx.AsyncClient", return_value=tracked_client):
                app = await create_runtime_app(Path(directory), runtime, bridge=bridge)
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://test") as client:
                    health = await client.get("/health")
                    self.assertFalse(health.json()["live_sources_verified"])
                    self.assertFalse(health.json()["production_writes_enabled"])
                    readiness = await client.get("/readiness")
                    self.assertEqual(readiness.json(), health.json())
            self.assertTrue(tracked_client.is_closed)
            self.assertEqual(list(Path(directory).iterdir()), [])
            with patch("hydro_orchestrator.service.TemporaryDirectory",
                       side_effect=PermissionError("sensitive-filesystem-response")):
                with self.assertRaises(PermissionError):
                    await create_runtime_app(Path(directory), runtime, bridge=bridge)
            for key in configured:
                with self.subTest(key=key):
                    bridge.call.return_value = {**configured, key: "changed"}
                    with self.assertRaises(SourceFailure):
                        await create_runtime_app(Path(directory), runtime, bridge=bridge)
        with self.assertRaises(ValidationError):
            RuntimeConfiguration(source=source.model_copy(update={"configuration_digest": None}),
                                 authorization=policy, project_endpoint=endpoint)
        with self.assertRaises(ValidationError):
            RuntimeConfiguration(source=source, authorization=self.policy, project_endpoint=endpoint)
        for binding in (
            NativeBinding(source=source.model_copy(update={"ontology_id": TENANT}),
                          connections={"data-agent": "test-native-connection"}),
            NativeBinding(source=source, connections={"data-agent": " "}),
        ):
            with self.subTest(binding=binding), self.assertRaises(ValidationError):
                RuntimeConfiguration(source=source, authorization=policy, project_endpoint=endpoint,
                                     native_binding=binding)

    async def test_runtime_cli_stops_without_explicit_configuration_and_serves_uncertified_health(self):
        environment = {**os.environ, "HYDRO_ORCHESTRATOR_CONFIG": "", "HYDRO_FABRIC_SOURCE_CONFIG": ""}
        stopped = await asyncio.to_thread(
            subprocess.run, [sys.executable, "-m", "hydro_orchestrator", "--serve-delegated"],
            env=environment, capture_output=True, timeout=15,
        )
        self.assertEqual(stopped.returncode, 2)
        self.assertIn(b"local deployment fallback is not enabled", stopped.stderr)
        identity = str(TENANT)
        source_config = dict.fromkeys(
            ("tenant_id", "workspace_id", "ontology_id", "eventhouse_id", "database_id", "graphql_id", "appbackend_id"),
            identity,
        )
        source_config.update(
            api_url=f"https://{identity.replace('-', '')}.pbidedicated.windows.net"
                    f"/webapi/capacities/{identity}/workloads/baas/baasservice/automatic/v1"
                    f"/workspaces/{identity}/appbackends/{identity}",
            publishable_key="pk-test",
        )
        with patch.dict(os.environ, {"HYDRO_FABRIC_SOURCE_CONFIG": json.dumps(source_config)}):
            verified = await NodeSourceBridge().call({"action": "configuration"})
        policy = self.policy.model_copy(update={"scopes": {
            "fabric": "https://api.fabric.microsoft.com/.default", "foundry": "https://ai.azure.com/.default",
        }})
        runtime = RuntimeConfiguration(
            source=SourceIdentity(tenant_id=TENANT, workspace_id=TENANT, ontology_id=TENANT, generation=2,
                                  configuration_digest=verified["configuration_digest"]),
            authorization=policy, project_endpoint="https://test.services.ai.azure.com/api/projects/test",
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        with TemporaryDirectory() as directory:
            environment.update(
                HYDRO_ORCHESTRATOR_CONFIG=runtime.model_dump_json(),
                HYDRO_FABRIC_SOURCE_CONFIG=json.dumps(source_config),
                HYDRO_RUNTIME_STATE_DIR=directory, HYDRO_RUNTIME_HOST="127.0.0.1",
                HYDRO_RUNTIME_PORT=str(port),
            )
            process = subprocess.Popen(
                [sys.executable, "-m", "hydro_orchestrator", "--serve-delegated"],
                env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            try:
                async with httpx.AsyncClient(timeout=0.25) as client:
                    health = None
                    for _ in range(100):
                        if process.poll() is not None:
                            _, error = await asyncio.to_thread(process.communicate, timeout=5)
                            self.fail(f"Runtime exited before readiness: {error.decode()}")
                        try:
                            health = await client.get(f"http://127.0.0.1:{port}/health")
                            break
                        except (httpx.ConnectError, httpx.ConnectTimeout):
                            await asyncio.sleep(0.1)
                    self.assertIsNotNone(health)
                    self.assertEqual(health.status_code, 200)
                    self.assertEqual(health.json()["status"], "awaiting_authorized_invocation")
                    self.assertFalse(health.json()["live_sources_verified"])
                    self.assertFalse(health.json()["production_writes_enabled"])
                    readiness = await client.get(f"http://127.0.0.1:{port}/readiness")
                    self.assertEqual(readiness.status_code, 200)
                    self.assertEqual(readiness.json(), health.json())
                    rejected = await client.post(f"http://127.0.0.1:{port}/invocations", json={})
                    self.assertEqual(rejected.status_code, 422)
            finally:
                if process.poll() is None:
                    process.terminate()
                await asyncio.to_thread(process.communicate, timeout=10)

    async def test_ingress_rejects_over_capacity_without_leasing_a_second_supervisor(self):
        started, release = asyncio.Event(), asyncio.Event()

        async def running(request):
            started.set()
            await release.wait()
            raise RuntimeError("Synthetic source failure must not become an empty answer.")

        with TemporaryDirectory() as directory:
            app, leases, _, contexts = self.ingress(Path(directory), run=running, capacity=1)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://test") as client:
                first = asyncio.create_task(client.post("/invocations", json=self.invocation()))
                await asyncio.wait_for(started.wait(), timeout=5)
                second = await client.post("/invocations", json=self.invocation())
                self.assertEqual(second.status_code, 429)
                self.assertEqual(len(leases), 1)
                release.set()
                failed = await first
                self.assertEqual(failed.status_code, 500)
                self.assertNotIn("Synthetic source failure", failed.text)
            self.assertEqual(contexts, ["closed"])
            with self.assertRaises(SourceAuthorizationError):
                await leases[0].get_token(self.policy.scopes["fabric"])

    async def test_existing_source_reader_uses_verified_spa_lease_without_cli_auth(self):
        self.policy.scopes["fabric"] = "https://api.fabric.microsoft.com/.default"
        body = self.body()
        credential = await self.verifier.verify(body, self.deadline)
        source = SourceIdentity(
            tenant_id=TENANT, workspace_id=UUID("44444444-4444-4444-8444-444444444444"),
            ontology_id=UUID("55555555-5555-4555-8555-555555555555"), generation=2,
            configuration_digest="a" * 64,
        )
        bridge = AsyncMock()
        bridge.call.side_effect = [
            {"tenant_id": str(TENANT), "configuration_digest": source.configuration_digest},
            {"source": source.model_dump(mode="json"), "cluster": "https://cluster.test", "database": "test"},
        ]
        with patch("hydro_orchestrator.live_sources.AzureCliCredential",
                   side_effect=AssertionError("Delegated source execution must not start CLI authentication.")):
            reader = await LiveSources.open_delegated(credential, bridge)
        self.assertEqual(reader.discovery.source, source)
        self.assertEqual(bridge.call.call_args.args[0]["tokens"]["fabric"],
                         body.tokens["fabric"].get_secret_value())
        await reader.close()
        with self.assertRaises(SourceAuthorizationError):
            await credential.get_token(self.policy.scopes["fabric"])

    def test_auth_policy_rejects_missing_audience_and_duplicate_resources(self):
        with self.assertRaises(ValidationError):
            self.policy.model_validate({
                **self.policy.model_dump(), "audiences": {"fabric": ("https://fabric.test",)},
            })
        with self.assertRaises(ValidationError):
            self.policy.model_validate({
                **self.policy.model_dump(), "scopes": {"fabric": "same-resource", "foundry": "same-resource"},
            })
