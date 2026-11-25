# Private AI Gateway reverse proxy

These incremental ARM templates target the approved `dbfabricvnet` resource
group, `vnet_dbfabric` VNet and Sweden Central, not the Hydro app deployment.
They do not change `AIGatewayPower`, its credentials, policies or MCP bindings.
Do not reuse the hardcoded subnet ranges in another environment without a new
network/conflict review and cost approval.

## Addressing and access

The private NGINX listener is `https://ai.espen.io`, currently at
`10.0.3.12:443`. This is a single-instance, non-HA deployment. ACI private IPs can
change after recreation; reapply `nginx-dns.json` using the live container
reference whenever that happens. Do not copy the recorded IP into other deployments.
The private
DNS zone is deliberately **`ai.espen.io`**, not `espen.io`, so it does not shadow
unrelated records in the parent domain. Its apex A record maps to the listener;
this is not a public CNAME. Only `vnet_dbfabric` is linked.

MCP paths are unchanged:

```text
https://ai.espen.io/default/toolservers/fabric-espen-ontology/mcp
https://ai.espen.io/default/toolservers/learn/mcp
https://ai.espen.io/default/toolservers/health/mcp
```

Clients need all three prerequisites: private routing into this VNet, private
DNS resolution, and trust in the self-signed `ai.espen.io` certificate. No VPN,
VNet peering, DNS forwarding for VPN clients, client trust installation or
Agent 365 private-network integration is provisioned. Do not bypass certificate
verification. The certificate is a self-signed server certificate, not a CA for
issuing other certificates. Its public certificate can be distributed for
explicit client trust. The private key remains in Key Vault and the explicitly
approved, ephemeral Azure proxy volume; it is never exported to the deployer.
The startup loader uses only the existing proxy managed identity's scoped
certificate-secret permission. Other gateway credentials are not retrieved.

The current backend client intentionally pins the original gateway origin for
its approval and redirect guard. This infrastructure deployment does not change
that client, enable a new credential destination, or certify proxy-based MCP use.

## Resources and security boundaries

| Resource | Purpose |
| --- | --- |
| `aci-aigproxy` | Private NGINX and TLS-loader container group; 1 vCPU and 2 GiB total |
| `nat-aigproxy`, `pip-aigproxy-egress` | Outbound connectivity only, not a public listener |
| `kv-aigp-<resource-group-hash>` | Dedicated RBAC vault; public access Disabled, firewall default Deny |
| `id-aigproxy` | Proxy identity; Secrets User only on that dedicated vault |
| `pe-aigproxy-vault`, `snet-aigproxy-vault`, `10.0.4.0/28` | Private vault access |
| `snet-aigproxy-cert`, `10.0.3.0/28`, `nsg-aigproxy-cert` | Proxy/worker subnet; only private-source HTTPS inbound allowed |
| `aci-aigproxy-cert`, `id-aigproxy-cert` | Temporary certificate/verifier worker; removed with its vault-scoped role after success |
| `snet-aigproxy`, `10.0.2.0/24`, `nsg-aigproxy` | Reserved foundation from the superseded Application Gateway attempt; no active listener |

The proxy preserves request paths, query strings and bearer authorization.
The upstream hostname is used for Host and verified TLS/SNI. Request and response
buffering, automatic upstream retries, redirects and error interception are
disabled. The upstream idle read/send timeouts are 300 seconds, and requests
have a 1 MiB body limit. Long-lived
streams still need appropriate protocol keepalives and actual client testing;
these settings do not promise unlimited SSE duration.

The existing upstream gateway remains publicly addressed. A private proxy does
not disable direct access to that gateway or repair its source-authentication
failures. The separate NAT public IP is egress-only.

`/healthz` returns HTTP 200 for **proxy-local health only**, with no upstream call.
The container readiness check validates its local NGINX configuration and loaded
certificate, not the upstream gateway. No recurring upstream health probes are
needed. Authenticated checks and tool execution require separate approval.

On November 25, 2026, private DNS and trusted HTTPS verification passed,
`/healthz` returned 200, and one unauthenticated forwarded GET `/` returned the
upstream's JSON HTTP 502 unchanged. No model or MCP call was made. This verifies
the private proxy boundary and error propagation, not authenticated gateway or
agent execution. The original ontology/Health blockers are not fixed here.

The initial Application Gateway design was superseded because both approved
anonymous upstream probe paths returned 502. Marking those responses healthy
would obscure the failure. NGINX instead forwards actual caller requests and
preserves their upstream responses, without using an anonymous backend health
gate. The Application Gateway was removed after replacement verification.
Scoped resource readback also confirms the temporary worker and its identity are
absent; its temporary vault role was removed. The persistent proxy and its
certificate loader remain running.

## Deployment and certificate lifecycle

Use the existing approved Azure CLI user binding for deployment. No gateway key
retrieval is necessary. Certificate generation runs inside the private vault.
The separately approved runtime loader reads only the new proxy TLS secret
inside Azure; this is not permission for the deployer to read that secret.
Obtain approval for actual resource changes, costs, role assignments and checks
before replaying any stage. Run `az deployment group validate` and `what-if`
before the corresponding `create`, always using incremental mode, the explicit
subscription and the approved resource group.

1. Register the approved `Microsoft.Network` features
   `EnableApplicationGatewayNetworkIsolation` and, where required for this
   subscription, `AllowBringYourOwnPublicIpAddress`. Propagate feature changes
   with provider registration. Do not infer this registration grants public
   ingress or allows an unsupported networking workaround.
2. Deploy `foundation.json` with `operatorObjectId` and `tenantId`. It adds only
   the dedicated subnet/resources; it does not replace the VNet or existing
   subnets. The operator receives Certificates Officer on the new vault only.
3. Deploy `certificate-network.json` with the foundation's `vaultName`. It
   uses the existing linked `privatelink.vaultcore.azure.net` zone and adds
   only the new vault's endpoint record.
4. Deploy `certificate-worker.json`, passing the literal text of
   `certificate-worker.py` as `workerScript`, `certificate-policy.json` as
   the string `certificatePolicy`, and the actual `vaultName`. Its official
   Azure CLI container uses its own managed identity to generate the certificate
   inside the private vault. Creation is never retried automatically, and reruns
   reuse the named certificate rather than rotate it. Verify the worker exit
   code is zero; ARM provisioning success alone does not establish script success.
5. Capture only the worker's public certificate and non-secret reference.
   Export the public DER/PEM for managed client trust, never the secret/PFX.
   Deploy `nginx-proxy.json` with the foundation's `identityId`, actual `vaultName`,
   public SHA-256 `certificateFingerprint`, and literal text of `nginx.conf`
   and `certificate-loader.py` as `nginxConfig` and `certificateLoader`.
   The loader places TLS files in an ephemeral volume with restrictive permissions;
   the NGINX mount is read-only. Only the loader may fetch this one certificate
   secret. The template pins the image digests observed in the verified deployment.
6. Deploy `nginx-dns.json` after the container is running. It resolves the
   actual container private IP through ARM readback, not a hardcoded IP.
7. With separate approval, run `verify-nginx-proxy.py` via the temporary worker,
   with public PEM `publicCertificatePem` and live ACI address `expectedPrivateIp`.
   It makes exactly one proxy-local `/healthz` GET and one unauthenticated
   forwarded GET `/`, with trusted TLS and no redirects, inference or MCP calls.
   It distinguishes verified forwarding of the known upstream JSON 502 from
   successful runtime execution. Read back the private frontend, DNS, vault
   network state and scoped roles. After success, remove only the approved
   temporary worker, its role and identity. Preserve the vault private endpoint,
   persistent proxy identity and loader.

The self-signed certificate expires on November 25, 2027, at 17:23:06 UTC.
The policy does not enable automatic key rotation. Arrange a separately approved
replacement and client trust rollout before expiry. Restart/redeploy the proxy
with the approved public fingerprint after replacement; the loader does not
silently replace its current certificate. No Key Vault contacts are configured by these
templates, so the policy's email-contact action is not a guaranteed alert.

Approved replacement estimate: approximately USD 85-120/month for container
compute, NAT and private endpoint baseline, plus data, DNS, Key Vault and taxes.
This is not a verified regional/account-specific quote. The removed temporary
worker and superseded Application Gateway no longer have active compute
resources; retained networking and the persistent proxy remain billable.

Run the worker's offline regression cases without Azure access:

```sh
python3 -m unittest discover -s infra/private-ai-gateway -p 'test_*.py'
```
