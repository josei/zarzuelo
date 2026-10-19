from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone


def sha256(s: str | bytes) -> str:
    return hashlib.sha256(s.encode() if isinstance(s, str) else s).hexdigest()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def norm(s: str) -> str:
    """Minúsculas, sin acentos, espacios colapsados: para comparar literales."""
    s = unicodedata.normalize("NFD", s.lower())
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return re.sub(r"\s+", " ", s).strip()


# --- EDTF mínimo (plan §2.1): 1894-02-17, 1894-02, 1894, 1890~, 189X, [1893..1895], 1890?
_EDTF = re.compile(
    r"^(?:\d{4}(?:-\d{2}(?:-\d{2})?)?[~?%]?|\d{3}X|\d{2}XX|\[\d{4}\.\.\d{4}\]|\d{4}/\d{4})$"
)


def is_edtf(s: str) -> bool:
    return bool(_EDTF.match(s))


def edtf_interval(s: str) -> tuple[int, int] | None:
    """Intervalo de años (min, max) para dibujar imprecisiones en cronogramas."""
    if not is_edtf(s):
        return None
    m = re.match(r"^\[(\d{4})\.\.(\d{4})\]$|^(\d{4})/(\d{4})$", s)
    if m:
        a, b = (m.group(1) or m.group(3)), (m.group(2) or m.group(4))
        return int(a), int(b)
    if "X" in s:
        lo = int(s.replace("X", "0"))
        return lo, int(s.replace("X", "9"))
    y = int(s[:4])
    if s.endswith("~"):
        return y - 5, y + 5
    return y, y


def edtf_year(s: str) -> int | None:
    iv = edtf_interval(s)
    return iv[0] if iv else None


def source_family(source: str) -> str:
    """Fuentes que no son independientes entre sí (se alimentan unas de otras) cuentan como una sola familia."""
    if source.startswith(("wiki", "commons")):
        return "wikimedia"
    if source.startswith("bne"):
        return "bne"
    return source
