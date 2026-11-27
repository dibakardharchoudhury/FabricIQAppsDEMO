# Private Logic Apps Standard

This deployment adds a private Standard WS1 Logic App to the existing
`vnet_dbfabric` in Sweden Central, resource group `dbfabricvnet`.
It does not deploy or alter HydroOperationsApp, Foundry agents or the AI proxy.

**Current status: infrastructure deployed, workflow NOT operational.**
The inherited management-group policy `StorageAccount_DisableLocalAuth_Modify`
forces storage shared-key access off. ARM deployment can succeed while that
Modify policy changes the requested property. Private file publishing therefore
failed before any successful starter invocation. All temporary containers,
identities, assignments and the helper subnet have been removed. The WS1 plan
and private app/storage/network resources remain and continue incurring charges.
Do not rerun this deployment to try to override policy.

The [classic Foundry wizard](https://learn.microsoft.com/azure/logic-apps/add-agent-action-create-run-workflow)
creates Consumption workflows. Standard is intentionally used instead to meet
the private networking requirement. The `starter` workflow retains the simple
HTTP Request -> Response pattern, but no Foundry connection is configured.
WS1 has ongoing idle compute charges, plus storage and private endpoint costs.

## Security and networking

App and storage public network access are disabled in their initial ARM creation.
Five private endpoints provide app/SCM and blob/file/queue/table connectivity.
The existing VNet itself is not redeployed; dedicated child subnets are added:

| Subnet | CIDR | Purpose |
|---|---|---|
| `snet-logic-integration` | `10.0.5.0/26` | Outbound app integration |
| `snet-logic-endpoints` | `10.0.6.0/27` | Five private endpoints |
| `snet-logic-deploy` | `10.0.7.0/28` | Temporary publisher; removed after verification |

Azure DNS links resolve this app and storage to private addresses. NXDOMAIN
fallback preserves resolution of unrelated services in the shared VNet;
it does not enable public access on this app/storage. Azure-issued HTTPS
certificates cover the default app hostname. FTP and SCM basic authentication
are disabled. Runtime storage keys are generated/resolved within ARM and kept
in protected application settings, never committed or printed. Shared-key
storage authentication is explicitly enabled on this new account because this
Azure Files configuration requires it; a default-disabled account prevents both
content publishing and runtime storage access. Public access remains disabled.
The single WS1 instance is not configured for zone-redundant high availability.

No public IP or incoming ports are provisioned for the temporary container.
It temporarily uses the existing `nat-aigproxy` for outbound Azure access only;
the NAT itself, proxy and proxy identity are not modified or deleted.
This is private **ingress**, not a blanket prohibition on outbound internet.
Default Azure hostnames and the Azure management plane still exist.

## Deploy and verify

The commands below describe the prepared WS1 deployment; they are **blocked in
the current governed subscription**. The script now checks existing storage's
live shared-key availability before mutations and again before creating a publisher.

From the repository root, with the approved subscription authenticated:

```sh
az bicep build --file infra/private-logic-app/main.bicep --stdout > /tmp/private-logic-app.json
az deployment group validate -g dbfabricvnet --template-file infra/private-logic-app/main.bicep
az deployment group what-if -g dbfabricvnet --template-file infra/private-logic-app/main.bicep
python3 infra/private-logic-app/deploy.py
```

Review the preview and obtain approval before deploying into another environment.
The script is deliberately scoped to the approved subscription/resource group.
It must not adopt a pre-existing temporary helper/identity of the same name.
The helper template uses Microsoft's public Azure CLI image, a new identity,
Website Contributor on the **new app only**, and Storage Account Key Operator
Service Role on the **new storage only**. These temporary assignments are removed.

The helper resolves all six app/SCM/storage hostnames privately, uploads the
package to the private Azure Files content share, retrieves the starter callback
inside Azure, and makes a real private request. Success requires HTTP 200 and
the exact starter response. Neither callback signatures nor storage credentials
are returned to the deployer. Cleanup runs even when publishing/testing fails:
container first, then its role assignments, identity and temporary subnet.
If cleanup fails, the script fails explicitly; inspect the named resources
before rerunning. Infrastructure is retained on runtime failure for diagnosis.

Only the harmless starter workflow is called. No models, MCP tools, managed
connectors, business actions, VPN/peering or client connectivity are configured.
Clients and private designer/SCM access require a routed VNet connection and
private DNS resolution. Private ingress alone does not establish Foundry access.
Signed Request callback URLs are credentials and must not be committed or logged.

## References

- [Documented keyless limitation and ASE v3 alternative](https://learn.microsoft.com/azure/logic-apps/create-single-tenant-workflows-azure-portal#set-up-managed-identity-access-to-your-storage-account)
- [Private app ingress and outbound integration](https://learn.microsoft.com/azure/logic-apps/secure-single-tenant-workflow-virtual-network-private-endpoint)
- [ARM deployment with private storage](https://learn.microsoft.com/azure/logic-apps/deploy-single-tenant-logic-apps-private-storage-account)
- [Private DNS fallback](https://learn.microsoft.com/azure/dns/private-dns-fallback)

## Supported keyless alternative

Microsoft Learn explicitly states that storage account key access cannot be
disabled for Standard Logic Apps on Workflow Service Plans (including WS1).
Managed identity for `AzureWebJobsStorage` alone does not remove that limitation.
For ASE v3 hosting, the same documentation allows disabling storage account key
access after configuring user-assigned managed identity and documented roles.
No-Key samples that still require a separate key-enabled content account do not
solve the inherited policy restriction.

The supported replacement preserving the Logic App product is a **Windows
Standard Logic App hosted in an internal-VIP ASE v3**, with an Isolated v2
plan, dedicated `Microsoft.Web/hostingEnvironments` subnet, private DNS and
private runtime storage accessed using managed identity. An internal VIP gives
the app a VNet-only inbound address. This is a hosting architecture change, not
an app-setting switch, and requires separate cost/architecture approval.
No existing ASE was found and none has been created.

[ASE v3 pricing](https://learn.microsoft.com/azure/app-service/environment/overview#pricing)
uses dedicated Isolated v2 capacity; even an empty ASE has a minimum Windows
I1v2-equivalent charge. No verified regional quote is available here.
The [networking documentation](https://learn.microsoft.com/azure/app-service/environment/networking)
recommends a dedicated `/24` for scaling, with `/27` as the minimum.
An internal ASE does not mean the global Azure control plane or every outbound
platform dependency ceases to have public endpoints.

No policy exemption, exception tag, keyless workaround, alternate hosting plan
or Foundry connection was deployed during the investigation.
