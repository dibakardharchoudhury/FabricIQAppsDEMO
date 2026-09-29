"""Distribute embedded support and code only for the owned 004/005/006 notebooks.

Preserves canonical headers and notebook/cell metadata. Never discovers or writes
other notebooks. All generated sources are compiled before any file is written.

From the repository root:
    python Raw\\workspace-reset\\sync_ontology_notebooks.py --check
    python Raw\\workspace-reset\\sync_ontology_notebooks.py --sync

Edit ontology_notebook_support.py, then run --sync to refresh embedded copies
and Raw mirrors. --check is read-only and fails on drift. The default is --sync.
No colleague Downloads files are required.
"""
import argparse
import json
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
        if len(raw_cells) != len(cells):
            raise RuntimeError(f"Cell-count mismatch in {raw}; refusing to replace metadata")
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true", help="Check embedded support and Raw mirrors without writing")
    modes.add_argument("--sync", action="store_true", help="Refresh only 004/005/006 (the default)")
    arguments = parser.parse_args()
    for destination in synchronize(check=arguments.check):
        print(destination)
