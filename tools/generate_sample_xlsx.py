"""Recreate the optional XLSX sample from the checked-in canonical JSON."""
import json
from pathlib import Path

def generate():
    from openpyxl import Workbook
    root = Path(__file__).resolve().parents[1]
    data = json.loads((root / "transfers.json").read_text(encoding="utf-8"))
    wb = Workbook()
    wb.remove(wb.active)
    for name, fields in (
        ("accounts", ["id", "balance", "currency"]),
        ("transfers", ["reference", "from", "to", "amount", "currency", "memo"]),
    ):
        ws = wb.create_sheet(name)
        ws.append(fields)
        for item in data[name]:
            ws.append([item.get(key) for key in fields])
        ws.freeze_panes = "A2"
    destination = root / "transfers.xlsx"
    wb.save(destination)
    return destination

if __name__ == "__main__":
    print(generate())
