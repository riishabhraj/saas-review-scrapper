# src/output.py
import json
from src.utils import safe_filename, ensure_outputs_dir
from pathlib import Path

def write_result(result: dict, company: str, source: str, start: str, end: str):
    ensure_outputs_dir()
    filename = f"{safe_filename(company)}-{source}-{start}_{end}.json"
    path = Path("outputs") / filename
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, default=str, ensure_ascii=False)
    return str(path)
