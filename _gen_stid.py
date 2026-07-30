"""One-shot generator: 3-facility STID seed.

Writes the 4 Raw reference CSVs and rewrites the embedded STID_FILES block in
NB01_SelfContained (.py + .ipynb mirror). Deterministic; safe to re-run. Delete after use.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).parent

MANUF = ["Andritz", "Voith", "GE Vernova", "Toshiba", "Hitachi Energy"]
MODELS = ["RTI-Turbine-A", "RTI-Turbine-B", "RTI-Turbine-C", "RTI-Turbine-D", "RTI-Turbine-E"]

# (signal, instrument_type, unit, range_low, range_high, sample_rate_hz)
SIGNALS = [
    ("inlet_pressure", "pressure", "bar", "5", "25", "1.0"),
    ("power_output", "power", "MW", "0", "120", "1.0"),
    ("turbine_speed", "speed", "rpm", "290", "310", "1.0"),
    ("turbine_temp", "temperature", "C", "30", "110", "0.2"),
    ("vibration_a", "vibration", "mm_s", "0", "45", "1.0"),
    ("vibration_d", "vibration", "mm_s", "0", "45", "1.0"),
]

# facility_id, name, type, country, lat, lon, commissioned, first_turbine_index, install_start_year
FACILITIES = [
    ("FACILITY_RTI_001", "RTI Demo Hydropower Plant", "Hydropower", "NO", "60.25", "2.10", "2015-06-01", 1, 2016),
    ("FACILITY_RTI_002", "RTI Fjord Hydropower Plant", "Hydropower", "NO", "61.20", "7.10", "2012-05-01", 6, 2013),
    ("FACILITY_RTI_003", "RTI Highland Hydropower Plant", "Hydropower", "NO", "59.90", "8.60", "2018-09-01", 11, 2019),
]


def tag(n):
    return f"T{n:03d}"


def build():
    fac_rows, sys_rows, eq_rows, inst_rows = [], [], [], []
    for fi, (fid, fname, ftype, country, lat, lon, comm, start, iyear) in enumerate(FACILITIES, start=1):
        sid = f"SYSTEM_RTI_{fi:03d}"
        fac_rows.append(f"{fid},{fname},{ftype},{country},{lat},{lon},{comm}")
        sys_rows.append(f"{sid},{fid},RTI Turbine System,TURBINE")
        for k in range(5):
            n = start + k
            t = tag(n)
            eid = f"EQUIP_RTI_{t}"
            manuf = MANUF[k]
            model = MODELS[k]
            install = f"{iyear + k}-07-01"
            eq_rows.append(f"{eid},{fid},{sid},TURB,Turbine,{t},{manuf},{model},1,{install},ACTIVE")
            for sig, itype, unit, lo, hi, rate in SIGNALS:
                iid = f"INST_{t}_{sig.upper()}"
                node = f"ns=2;s={t}.{sig}"
                inst_rows.append(f"{iid},{eid},{fid},{sid},{itype},{sig},{unit},{node},{lo},{hi},{rate}")
    return fac_rows, sys_rows, eq_rows, inst_rows


fac_rows, sys_rows, eq_rows, inst_rows = build()

FAC_HDR = "facility_id,facility_name,type,country,lat,lon,commissioned_date"
SYS_HDR = "system_id,facility_id,system_name,oag_rds_system_code"
EQ_HDR = "equipment_id,facility_id,system_id,equipment_type_code,equipment_type_name,tag,manufacturer,model,criticality,install_date,status"
INST_HDR = "instrument_id,equipment_id,facility_id,system_id,instrument_type,tag,unit,opcua_node_id,range_low,range_high,sample_rate_hz"

# 1) Raw reference CSVs
raw = ROOT / "Raw" / "stid_rti_fixed_source_files"
(raw / "facilities_stid.csv").write_text(FAC_HDR + "\n" + "\n".join(fac_rows) + "\n", encoding="utf-8")
(raw / "systems_stid.csv").write_text(SYS_HDR + "\n" + "\n".join(sys_rows) + "\n", encoding="utf-8")
(raw / "equipment_stid.csv").write_text(EQ_HDR + "\n" + "\n".join(eq_rows) + "\n", encoding="utf-8")
(raw / "instruments_stid.csv").write_text(INST_HDR + "\n" + "\n".join(inst_rows) + "\n", encoding="utf-8")
print("Wrote Raw CSVs:", len(fac_rows), "facilities,", len(eq_rows), "equipment,", len(inst_rows), "instruments")


# 2) Build the STID_FILES python-literal block (matches existing indentation/style).
def py_csv(header, rows):
    lines = [f'        "{header}\\n"']
    for r in rows:
        lines.append(f'        "{r}\\n"')
    return "\n".join(lines)


stid_files_block = (
    "STID_FILES = {\n"
    '    "facilities_stid.csv": (\n'
    f"{py_csv(FAC_HDR, fac_rows)}\n"
    "    ),\n"
    '    "systems_stid.csv": (\n'
    f"{py_csv(SYS_HDR, sys_rows)}\n"
    "    ),\n"
    '    "equipment_stid.csv": (\n'
    f"{py_csv(EQ_HDR, eq_rows)}\n"
    "    ),\n"
    '    "instruments_stid.csv": (\n'
    f"{py_csv(INST_HDR, inst_rows)}\n"
    "    ),\n"
    "}"
)

# 3) Rewrite the .py notebook STID_FILES block.
nb_py = ROOT / "Notebooks" / "RTI_001_create_lakehouse_SelfContained.Notebook" / "notebook-content.py"
text = nb_py.read_text(encoding="utf-8")
pattern = re.compile(r"STID_FILES = \{.*?\n\}", re.DOTALL)
if not pattern.search(text):
    raise SystemExit("STID_FILES block not found in .py")
text = pattern.sub(lambda _: stid_files_block, text, count=1)
nb_py.write_text(text, encoding="utf-8")
print("Updated notebook-content.py")

# 4) Rewrite the .ipynb mirror STID_FILES block (inside the cell source).
nb_ipynb = ROOT / "Raw" / "RTI_Notebooks" / "RTI_001_create_lakehouse_SelfContained.ipynb"
nb = json.loads(nb_ipynb.read_text(encoding="utf-8"))
found = False
for cell in nb.get("cells", []):
    if cell.get("cell_type") != "code":
        continue
    src = "".join(cell.get("source", []))
    if "STID_FILES = {" in src:
        new_src = pattern.sub(lambda _: stid_files_block, src, count=1)
        # Re-split into line list preserving newlines (nbformat convention).
        parts = new_src.split("\n")
        cell["source"] = [p + "\n" for p in parts[:-1]] + ([parts[-1]] if parts[-1] else [])
        found = True
        break
if not found:
    raise SystemExit("STID_FILES cell not found in .ipynb")
nb_ipynb.write_text(json.dumps(nb, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
print("Updated .ipynb mirror")
