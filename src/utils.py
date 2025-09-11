# src/utils.py
from dateutil import parser as dateparser
from datetime import datetime
import re
import pathlib

def parse_date_fuzzy(s):
    try:
        dt = dateparser.parse(s, fuzzy=True)
        if dt:
            return dt.date()
    except Exception:
        return None
    return None

def safe_filename(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9\-_\.]+', '_', s).strip('_')

def iso_now():
    return datetime.utcnow().isoformat() + "Z"

def ensure_outputs_dir():
    pathlib.Path("outputs").mkdir(parents=True, exist_ok=True)
