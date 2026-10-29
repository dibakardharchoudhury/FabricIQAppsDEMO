"""Distribute ontology support to 004/005/006, mirror 009, and shared-store support to 001/011.

Preserves canonical headers and notebook/cell metadata. Never discovers or writes
other notebooks. All generated sources are compiled before any file is written.

From the repository root:
    python Raw\\workspace-reset\\sync_ontology_notebooks.py --check
    python Raw\\workspace-reset\\sync_ontology_notebooks.py --sync

Edit ontology_notebook_support.py or operational_store_support.py, then run --sync to refresh embedded copies
and Raw mirrors. --check is read-only and fails on drift. The default is --sync.
No colleague Downloads files are required.
"""
import argparse
import ast
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OWNED = (
    "RTI_004_build_ontology_mapping_rti_structured",
    "RTI_005_entity_DataBinding_rti_structured",
    "RTI_006_TimeSeriesBinding_RTI_signal",
)
CELL = "# CELL ********************"
METADATA = "# METADATA ********************"


def synchronize(root=ROOT, check=False):
    support = (root / "Raw/workspace-reset/ontology_notebook_support.py").read_text(encoding="utf-8").strip()
    pending = {}
    for name in OWNED:
        canonical = root / "Notebooks" / (name + ".Notebook") / "notebook-content.py"
        raw = root / "Raw/RTI_Notebooks" / (name + ".ipynb")
        sections = canonical.read_text(encoding="utf-8").split(CELL)
        support_count = 0
        for index in range(1, len(sections)):
            code, metadata = sections[index].split(METADATA, 1)
            if code.strip().startswith('"""Pure support source embedded in 004/005/006;'):
                code = "\n\n" + support + "\n\n"
                support_count += 1
            sections[index] = code + METADATA + metadata
        if support_count != 1:
            raise RuntimeError(f"Expected one embedded support cell in {canonical}")
        content = "\n".join(line.rstrip() for line in CELL.join(sections).splitlines()).rstrip() + "\n"
        cells = [section.split(METADATA, 1)[0].strip() for section in content.split(CELL)[1:]]
        compile(content, str(canonical), "exec")
        notebook = json.loads(raw.read_text(encoding="utf-8"))
        raw_cells = [cell for cell in notebook["cells"] if cell["cell_type"] == "code"]
        if len(raw_cells) > len(cells):
            raise RuntimeError(f"Raw notebook has extra code cells in {raw}; refusing to delete metadata")
        for _ in range(len(cells) - len(raw_cells)):
            cell = {
                "cell_type": "code", "execution_count": None,
                "metadata": {}, "outputs": [], "source": [],
            }
            notebook["cells"].append(cell)
            raw_cells.append(cell)
        for index, (cell, code) in enumerate(zip(raw_cells, cells)):
            compile(code, f"{raw} cell {index}", "exec")
            cell["source"] = code.splitlines(keepends=True)
            cell["outputs"] = []
            cell["execution_count"] = None
        pending[canonical] = content
        pending[raw] = json.dumps(notebook, ensure_ascii=False, indent=1) + "\n"
    changed = {path: content for path, content in pending.items()
               if path.read_text(encoding="utf-8") != content}
    if check and changed:
        raise RuntimeError("Ontology distribution drift; run --sync:\n" + "\n".join(map(str, changed)))
    if not check:
        for path, content in changed.items():
            path.write_text(content, encoding="utf-8", newline="\n")
    return tuple(pending)


def synchronize_operational_support(root=ROOT, check=False):
    support = (root / "Raw/workspace-reset/operational_store_support.py").read_text(encoding="utf-8").strip()
    support = support.replace(
        "from ontology_notebook_support import _fabric_operation_url, _fabric_result_url, _project_semantics\n", "")
    ontology_support = (root / "Raw/workspace-reset/ontology_notebook_support.py").read_text(encoding="utf-8")
    shared_names = {"_normal_text", "_decode_part", "_encode_part", "_parts_by_path", "_identifier_parts",
                    "_local_name", "_quote_name", "_object_spans", "_append_model_ref",
                    "_structural_lines", "_json_contains", "_verify_definition", "_project_semantics",
                    "_semantic_description", "_enrich_semantic_block", "_project_business_rules",
                    "_enrich_project_semantics", "_verify_semantic_descriptions",
                    "_fabric_operation_url", "_fabric_result_url"}
    helpers = [ast.get_source_segment(ontology_support, node) for node in ast.parse(ontology_support).body
               if isinstance(node, ast.FunctionDef) and node.name in shared_names]
    if len(helpers) != len(shared_names):
        raise RuntimeError("Shared ontology readback verifier could not be distributed completely.")
    support += ("\n\nfrom collections import Counter\n"
                "from urllib.parse import urljoin, urlsplit, urlunsplit\n\n" + "\n\n".join(helpers))
    marker = '"""Shared operational-store support embedded in NB01 and NB11; no local imports."""'
    pending = {}
    support_notebooks = ("RTI_001_create_lakehouse_SelfContained", "RTI_001_create_lakehouse_shortcut",
                         "RTI_011_seed_sql_wire_graphql_agent")
    for name in (*support_notebooks, "RTI_003_ingest_transform_medallion",
                 "RTI_003_ingest_transform_medallion_SelfContained", "Weather_020_area_calculations",
                 "RTI_Orchestrator_Setup", "Weather_001_create_lakehouse", "Weather_002_fetch_area_weather",
                 "Weather_003_fetch_ukmet", "Weather_Orchestrator"):
        canonical = root / "Notebooks" / (name + ".Notebook") / "notebook-content.py"
        raw = root / "Raw/RTI_Notebooks" / (name + ".ipynb")
        sections = canonical.read_text(encoding="utf-8").split(CELL)
        matches = []
        for index in range(1, len(sections)):
            code, metadata = sections[index].split(METADATA, 1)
            if code.strip().startswith(marker):
                matches.append(index)
                sections[index] = "\n\n" + support + "\n\n" + METADATA + metadata
        if len(matches) != (1 if name in support_notebooks else 0):
            raise RuntimeError(f"Unexpected shared operational support cell count in {canonical}")
        content = CELL.join(sections)
        compile(content, str(canonical), "exec")
        notebook = json.loads(raw.read_text(encoding="utf-8"))
        def identity(kind, source):
            first = next((line.strip() for line in source.splitlines()
                          if line.strip() and not line.lstrip().startswith("#")), "")
            return kind, first
        existing = {}
        for cell in notebook["cells"]:
            existing.setdefault(identity(cell["cell_type"], "".join(cell["source"])), []).append(cell)
        chunks = re.split(r"(?m)^# (CELL|PARAMETERS CELL|MARKDOWN) \*+[ \t]*$", content)
        cells = []
        for index in range(1, len(chunks), 2):
            kind, body = chunks[index], chunks[index + 1].split(METADATA, 1)[0].strip()
            cell_type = "markdown" if kind == "MARKDOWN" else "code"
            if cell_type == "markdown":
                body = "\n".join(line[2:] if line.startswith("# ") else line[1:] if line.startswith("#") else line
                                 for line in body.splitlines())
            else:
                compile(body, f"{raw} cell {len(cells)}", "exec")
            matches = existing.get(identity(cell_type, body), [])
            cell = dict(matches.pop(0)) if matches else {"cell_type": cell_type, "metadata": {}}
            cell["source"] = (body + "\n").splitlines(keepends=True) if body else []
            cell["metadata"] = dict(cell.get("metadata", {}))
            if cell_type == "code":
                cell.update(execution_count=None, outputs=[])
            if kind == "PARAMETERS CELL":
                cell["metadata"]["tags"] = ["parameters"]
            cells.append(cell)
        notebook["cells"] = cells
        pending[canonical] = content
        pending[raw] = json.dumps(notebook, ensure_ascii=False, indent=1) + "\n"
    changed = {path: content for path, content in pending.items() if path.read_text(encoding="utf-8") != content}
    if check and changed:
        raise RuntimeError("Operational notebook distribution drift; run --sync:\n" + "\n".join(map(str, changed)))
    if not check:
        for path, content in changed.items():
            path.write_text(content, encoding="utf-8", newline="\n")
    return tuple(pending)


def synchronize_data_agent(root=ROOT, check=False):
    """Mirror NB009's canonical cells without replacing cell or notebook metadata."""
    name = "RTI_009_build_data_agent"
    canonical = root / "Notebooks" / (name + ".Notebook") / "notebook-content.py"
    raw = root / "Raw/RTI_Notebooks" / (name + ".ipynb")
    content = canonical.read_text(encoding="utf-8")
    support = (root / "Raw/workspace-reset/operational_store_support.py").read_text(encoding="utf-8")
    names = {"selected_ontology_entities", "ontology_source_bindings", "prune_ontology_duplicate_sources",
             "ontology_context_elements", "refresh_ontology_context_parts"}
    helpers = [ast.get_source_segment(support, node) for node in ast.parse(support).body
               if isinstance(node, ast.FunctionDef) and node.name in names]
    if len(helpers) != len(names):
        raise RuntimeError("Incomplete Data Agent source topology support.")
    content, count = re.subn(
        r"(?ms)^# BEGIN ONTOLOGY SOURCE TOPOLOGY SUPPORT\n.*?^# END ONTOLOGY SOURCE TOPOLOGY SUPPORT",
        lambda _match: "# BEGIN ONTOLOGY SOURCE TOPOLOGY SUPPORT\n" + "\n\n".join(helpers)
                       + "\n# END ONTOLOGY SOURCE TOPOLOGY SUPPORT",
        content,
    )
    if count != 1:
        raise RuntimeError("Expected one Data Agent source topology support region.")
    if canonical.read_text(encoding="utf-8") != content:
        if check:
            raise RuntimeError("NB009 source topology distribution drift; run --sync.")
        compile(content, str(canonical), "exec")
        canonical.write_text(content, encoding="utf-8", newline="\n")
    compile(content, str(canonical), "exec")
    notebook = json.loads(raw.read_text(encoding="utf-8"))
    chunks = re.findall(
        r"# (MARKDOWN|CELL|PARAMETERS CELL) \*+\n(.*?)(?=\n# (?:MARKDOWN|CELL|PARAMETERS CELL) \*+|\Z)",
        content, re.S,
    )
    if len(chunks) != len(notebook["cells"]):
        raise RuntimeError("NB009 cell count changed; explicitly reconcile metadata before mirroring.")
    for cell, (kind, body) in zip(notebook["cells"], chunks):
        body = body.split(METADATA, 1)[0].strip("\n")
        expected_type = "markdown" if kind == "MARKDOWN" else "code"
        if cell["cell_type"] != expected_type:
            raise RuntimeError("NB009 cell type changed; refusing to move metadata.")
        if kind == "MARKDOWN":
            body = "\n".join(line[2:] if line.startswith("# ") else "" if line == "#" else line
                             for line in body.splitlines())
        else:
            compile(body, str(raw), "exec")
        cell["source"] = (body + "\n").splitlines(keepends=True)
    desired = json.dumps(notebook, ensure_ascii=False, indent=2) + "\n"
    if raw.read_text(encoding="utf-8") != desired:
        if check:
            raise RuntimeError("NB009 distribution drift; run --sync.")
        raw.write_text(desired, encoding="utf-8", newline="\n")
    return (canonical, raw)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="Check embedded support and Raw mirrors without writing")
    modes.add_argument("--sync", action="store_true", help="Refresh owned ontology and operational notebooks (the default)")
    arguments = parser.parse_args()
    for destination in (*synchronize(check=arguments.check), *synchronize_data_agent(check=arguments.check),
                        *synchronize_operational_support(check=arguments.check)):
        print(destination)
