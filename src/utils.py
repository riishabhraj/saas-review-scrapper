# src/utils.py
from dateutil import parser as dateparser
from datetime import datetime, date
from pathlib import Path
import re
import json

def parse_date_fuzzy(s):
    if not s:
        return None
    try:
        dt = dateparser.parse(str(s), fuzzy=True)
        return dt.date() if isinstance(dt, datetime) else dt
    except Exception:
        return None

def safe_filename(s: str) -> str:
    return re.sub(r'[^A-Za-z0-9\-_\.]+', '_', s).strip('_')

def iso_now():
    return datetime.utcnow().isoformat() + "Z"

def ensure_outputs_dir():
    p = Path("outputs")
    p.mkdir(parents=True, exist_ok=True)
    return p
