"""Format-neutral parsing; no business rules live here."""
import json
from pathlib import Path
from typing import Protocol

class Parser(Protocol):
    def parse(self, path: Path) -> dict: ...

class JsonParser:
    def parse(self, path: Path) -> dict:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("transfers"), list):
            raise ValueError("Expected object with transfers array")
        return data

class ExcelParser:
    def parse(self, path: Path) -> dict:
        from openpyxl import load_workbook
        wb = load_workbook(path, read_only=True, data_only=True)
        try:
            def rows(name):
                sheet = wb[name]
                iterator = sheet.iter_rows(values_only=True)
                header = next(iterator)
                return [{key: value for key, value in zip(header, row) if value is not None}
                        for row in iterator if any(v is not None for v in row)]
            return {"accounts": rows("accounts"), "transfers": rows("transfers")}
        finally:
            wb.close()

def parse_file(path: str | Path) -> dict:
    path = Path(path)
    parsers: dict[str, Parser] = {".json": JsonParser(), ".xlsx": ExcelParser()}
    if path.suffix.lower() not in parsers:
        raise ValueError("Unsupported file type")
    return parsers[path.suffix.lower()].parse(path)
