# Concord: Fabric master data stewardship

Concord is a **separate, reusable app experience**, not a Hydro Operations page. Its
frontend is `HydroOperationsApp/master-data.html`; it shares the existing Rayfin
build, authentication and Fabric hosting foundation. The Python runtime in this
directory has no Hydro business dependencies.

It is a governance gate **after Silver and before Gold**, not an ingestion tool.
The app manages source-schema inventory, canonical naming, ontology mappings,
column exclusions, conflict policies, lineage and release candidates.
The pipeline executes the reviewed contract against complete Delta tables.

**Deployment status (2026-11-08):** the frontend and additive SQL draft schema
were published to `ws-FabricIQAppsDemoEspenFeature` through the canonical
orchestrator. Open [Concord](https://savvy-coast-6cc7d37cc3-norwayeast.webapp.fabricapps.net/master-data.html)
with normal Fabric sign-in. Deployment verified the AppBackend identity,
protected hosting gate, CORS/POST readiness, redirects and current-operator
consent. Tenant-wide consent still requires an administrator. The embedded
browser's sign-in popup was blocked, so authenticated UI and draft save/readback
acceptance remain unperformed.

Live ontology access, approved-contract Fabric Spark execution and semantic-model
deployment remain separate acceptance steps. No MDM Gold release or semantic
model was published by this app deployment. A local simulation or a generated
TMDL definition does not certify those services.

## Run the standalone app

```bash
cd HydroOperationsApp
npm ci
npm run dev:master-data
```

Open `/master-data.html`. Start with **Explore customer example** for an explicit,
synthetic training session. The initial page does not load sample data or pretend
to connect to Fabric. The example is never substituted for a failed live source.

For hosted use, the normal multi-entry build emits both `index.html` and
`master-data.html`. Follow the existing `HydroOperationsApp/DEPLOY.md` and canonical
deployment orchestrator; do not create a second Rayfin deployment or alter Entra
redirects by hand. This is an independently navigable application entry, **not yet
an independently provisioned Fabric AppBackend**.

The existing required deployment environment must pass `npm run validate-env`
before any production build/deploy. Local metadata editing needs no Fabric
credentials. SQL history and live ontology verification do.

## Hosted Hydro pilot

On 2026-11-08, the user selected the existing Hydro equipment/facility demo and
approved an isolated, schema-enabled `Concord_MDM_Pilot` Lakehouse in
`ws-FabricIQAppsDemoEspenFeature`. The original source Lakehouse and ontology
bindings were not changed. These are **hosted demo datasets**, not production
customer feeds or independent overlapping source systems.

`MDM_000_prepare_hydro_silver` completed successfully. Its read-back Silver
receipt records 15 equipment, 3 systems and 3 facilities, copied from original
Delta version 5 for each table. Equipment criticality was explicitly widened
from `int` to `bigint`; installation date was retained as ISO text in Silver and
explicitly excluded from proposed Gold mappings. Systems is included because
the ontology declares Equipment → Systems → Facilities, not a direct
Equipment → Facilities relationship.

The reusable preparation builder is:

```bash
python3 -m MasterDataManagement.hydro_pilot \
  --source-workspace <verified-hydro-workspace-guid> \
  --source-lakehouse <verified-hydro-lakehouse-guid> \
  --target-workspace <verified-pilot-workspace-guid> \
  --target-lakehouse <verified-schema-enabled-pilot-lakehouse-guid> \
  --target-name <verified-pilot-lakehouse-name> \
  --output /path/to/pilot-artifacts
```

It creates local notebook/definition artifacts only. The deployed notebook is
**one-shot**: existing tables or a receipt block another run rather than
overwriting prior data. For an incomplete run, inspect the actual job and tables;
do not automatically delete or replay it.

`hydro_pilot.build_draft(receipt, ontology)` generates an unreviewed contract from
a verified Silver receipt and live ontology identity/fingerprint. All 23 column
decisions remain unreviewed. The live Hydro ontology's returned relationship
TMDL omits explicit `fromCardinality`/`toCardinality`. Microsoft's actual TOM
19.84.1 TMDL deserializer was used to verify the formal defaults (`many`/`one`);
its serializer also omits these values on roundtrip. Concord normalizes those
defaults at the **physical** endpoints before orienting the declared semantic
relationship. Explicit incompatible or unsupported values still fail validation,
and unbound relationships acquire no defaults. This is format interpretation,
not cardinality inferred from a sample or names; no ontology mutation is needed.

No MDM Gold release, publisher run, production approval, or semantic model has
been created for this pilot. Sign-in and authenticated SQL draft acceptance also
remain outstanding. After resolving ontology validation, a steward must review
the contract and an authorized operator must independently pin its digest before
deploying the Gold notebook and its succeeded-dependency pipeline activity.

## Steward workflow

1. Import a format-version-1 JSON contract or create one from the customer template.
   The contract editor supports entities, canonical column names, business-friendly
   semantic labels, source bindings, ontology property names and relationships.
   Full-contract edits clear previous column reviews and the ontology fingerprint.
2. Select an entity and review each source column. Map it to a canonical property
   or explicitly exclude it, with a business rationale. Source/target scalar
   types must already match; conform types in Silver rather than silently casting.
   Similarly named unmapped columns are highlighted as **potential overlap**,
   separately from already mapped attributes; no suggestion applies itself.
3. Define attribute-level survivorship: **require equal** or **first non-null by an
   explicit source-priority list**. Changing a rule resets affected column reviews.
4. Refresh inventory from `inspect_sources(spark, sources)` when source schemas
   change. Every observed column, including excluded columns, is tracked. Added,
   removed or retyped columns and changed source identities block release until
   resolved. Removed mappings remain visible in release blockers.
5. Optionally upload bounded sample JSON keyed by source ID and inspect the golden
   record simulation. Samples stay in active-tab memory, never SQL draft history,
   localStorage, IndexedDB or bundled live fallbacks. Sample conflicts block
   preparation while loaded; clearing a sample does **not** bypass the runner's
   mandatory whole-data checks.
6. Under **Versions & release**, provide exact ontology workspace/item GUIDs and
   verify live numeric `properties.generation == 2`. The app uses the existing v2
   TMDL parser to verify entity/property types, single-property keys, and explicit
   many-to-one relationship endpoints and keys. No ontology is modified, and no
   graph materialization, GraphModel or agent is required.
7. Save an immutable private draft to Fabric SQL, or download a draft offline.
   **Prepare release candidate** performs fresh live ontology verification and
   exports canonical contract JSON with its SHA-256 shown in the app.
8. A separate authorized pipeline operator reviews the artifact and pins its
   SHA-256 in protected deployment configuration. Browser review checkboxes and SQL
   draft records are **not production authorization**.

### Example: two `customer` columns

CRM `customer` and ERP `customer` can both map to canonical `customer_name`, with
semantic label **Customer name** and ontology property `Customer.name`.
Their names alone do not establish that the fields mean the same thing.

Record matching uses an unchanged, nonempty string business key in an explicitly
shared namespace, for example `enterprise_customer_id`. ERP-local `42` must not
silently join CRM-local `42`. Resolve that identity through a verified crosswalk
in Silver first. Fuzzy matching, probabilistic entity resolution and automated
surrogate-key assignment are deliberately not implemented.

With priority `crm, erp`, the CRM non-null name wins and ERP fills missing names.
For an email with **require equal**, two unequal normalized addresses block the
entire release. Null/empty values are treated as missing; `false` and numeric zero
are valid values. Keys are never normalized. Duplicate source keys, blank keys,
required-value gaps and orphan relationship keys are errors, never dropped rows.

For a new CRM `loyalty_tier`, either add and describe a canonical attribute in the
contract and coordinate its declaration in the v2 ontology, or exclude it with a
rationale. The app cannot approve an undeclared ontology property; it does not
silently rewrite the ontology, source data or existing semantic models.

## Fabric SQL draft history

`rayfin/data/schema.ts` declares `MasterDataDraft`. Only authenticated `create` and
`read` are exposed, with a server-side `claims.sub == createdBy` policy. Updates
and deletes are not exposed. Saves verify exact SQL readback; uncertain writes
surface an error and must be inspected before retrying. History uses stable,
cursor-paginated reads. Metadata only is stored. Contract JSON uses MSSQL
`NVARCHAR(MAX)`; Rayfin's bounded `@text({ max: ... })` is limited to 4,000
characters and must not be used to truncate a contract.

Apply this additive schema through the existing canonical deployment flow. Do not
expect SQL history to work against an older deployment without this entity.
Drafts are private per identity; shared team queues, reviewer role administration
and formal separation-of-duty approval services are not implemented. Production
approval instead occurs through the separately protected pipeline digest.

## Silver-to-Gold execution

The target must be a **schema-enabled Lakehouse**. Upload the reviewed contract to
`Files/master-data/contracts/<approved-sha256>.json` in that target.

Build a runtime zip and bound Fabric notebook using actual discovered identities:

```bash
python3 -m MasterDataManagement.build_artifacts \
  --contract /path/to/reviewed-contract.json \
  --approved-sha256 <independently-approved-sha256> \
  --target-workspace <gold-workspace-guid> \
  --target-lakehouse <gold-lakehouse-guid> \
  --target-lakehouse-name <live-lakehouse-display-name> \
  --output /path/to/deployment-artifacts
```

The command is local packaging only, not a deployment shortcut. It produces
`mdm-runtime.zip` and `MDM_001_publish_gold.ipynb`, with the required Lakehouse
dependency metadata and valid Jupyter execution fields. Upload the zip to
`Files/master-data/mdm-runtime.zip`, then import/deploy the generated notebook.
Rerun the builder with `--notebook-id <actual-deployed-notebook-guid>` to generate
`pipeline-content.json` using that exact notebook. No workspace IDs or endpoints
are inferred or reused from Hydro.

The generated notebook has the approved digest **embedded by the release operator**,
not supplied by a browser upload or freely editable pipeline run parameter.
Protect the notebook, runtime zip, contract directory, pipeline configuration and
Gold write permissions from draft-only authors. Someone with Spark/Gold write
access can bypass any application-level gate; a hash is an integrity/approval
binding, not a digital signature or an RBAC substitute.

Add the generated notebook activity to the existing Silver-to-Gold pipeline
**after the Silver producer succeeds**, using a `Succeeded` dependency on the
actual producer activity. The standalone generated pipeline assumes Silver is
already complete. It uses concurrency 1 and zero automatic mutation retries.
Do not run it concurrently with another publisher.

The runner:

- Checks contract shape, independently pinned digest, exact notebook/Gold identity,
  numeric ontology generation and the exact reviewed definition fingerprint.
- Pins every source read to a Delta version and compares the entire live schema
  with the approved inventory. Inaccessible/missing sources and type drift fail.
- Unions sources on explicit canonical keys, then evaluates deterministic
  per-property survivorship with distributed Spark aggregates. It never collects
  source records on the driver.
- Validates every entity and relationship **before any Gold write**, including
  source-key uniqueness, required attributes, conflict rules and orphan keys.
- Writes exclusive, immutable release tables:
  `gold.mdm_<canonical_table>_<digest-prefix>` plus matching `_lineage` tables.
  Provenance records the winning source, source column and Delta version per
  populated attribute.
- Verifies Gold/provenance readback, then creates the one-row
  `gold.mdm_release_<digest-prefix>` commit marker **last**. Consumers must require
  this marker. The returned receipt reports `gold_committed`, never semantic
  model or agent readiness.

The publication contract is an immutable, full-source snapshot, not an incremental
CDC merge or destructive overwrite. Multi-table Delta writes are not one
transaction: a storage failure can leave uncommitted tables. Such a failure never
produces the marker. Incomplete runs and existing destinations are hard failures;
inspect them and explicitly resolve/reapprove rather than automatically deleting
or retrying. Retention/cleanup and current-release switching are operator tasks.

## Semantic model

The commit marker contains `semantic_definition_json`: a Fabric item-definition
payload with explicit TMDL, Direct Lake partitions pointing to that release's Gold
tables, business-friendly labels, hidden key columns, and only declared,
single-direction many-to-one relationships. It uses the actual target OneLake
workspace/Lakehouse GUIDs, not an analytics endpoint ID. Ontology names and
canonical physical column names are separately tracked.

Deploy this payload as a **new semantic model** through your established model
release process, after verifying the commit marker and Gold table visibility.
Configure its connection/permissions, refresh it, and query it before promoting
consumers. No existing model is overwritten. No model is actually created or
certified by generating the payload. This master-data model intentionally does
not invent transaction facts, DAX measures, business metrics or relationships.

Initial supported columns are `string`, `int64` (Spark `bigint`), and `boolean`.
Other types, composite keys, self-relations, many-to-many relations, cycles and
multiple active relationship paths are rejected explicitly. Model extensions
need an approved contract-version change, not an implicit fallback.

## Validation

```bash
npm --prefix HydroOperationsApp run test:master-data
npm --prefix HydroOperationsApp run typecheck
python3 -m unittest MasterDataManagement.test_contract -v
```

The optional distributed-runtime tests need **Python 3.11**, Spark 3.5, Delta 3.3
and Java 17. In an isolated Python environment install `pyspark==3.5.8` and
`delta-spark==3.3.2`, set `JAVA_HOME` to Java 17, then run:

```bash
python3 -m unittest MasterDataManagement.test_spark -v
```

They run locally against temporary Delta tables with the live ontology transport
replaced by a test double. They do not prove Fabric SQL, Fabric Spark, Entra,
ontology API or deployed semantic-model readiness.
