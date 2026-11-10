"""Keep the XLSX equivalence test runnable even in a source-only checkout."""
from pathlib import Path
from tools.generate_sample_xlsx import generate

if not (Path(__file__).resolve().parents[1] / "transfers.xlsx").exists():
    generate()
