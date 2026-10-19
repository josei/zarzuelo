"""Cliente HTTP con límite de peticiones, User-Agent identificable y caché en disco."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import yaml

from .db import DATA, ROOT
from .util import sha256

UA = os.environ.get("KB_USER_AGENT", "zarzuelo-kb/0.1 (research project)")


class SourceNotValidated(RuntimeError):
    pass


def load_env() -> None:
    """Carga .env (claves locales) en os.environ sin pisar lo ya definido."""
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


load_env()
UA = os.environ.get("KB_USER_AGENT", UA)


def load_sources() -> dict:
    """config/sources.yaml + config/sources.d/*.yaml (una por conector; las entradas de .d prevalecen)."""
    out = (yaml.safe_load((ROOT / "config" / "sources.yaml").read_text()) or {}).get("sources", {})
    for f in sorted((ROOT / "config" / "sources.d").glob("*.yaml")):
        out.update((yaml.safe_load(f.read_text()) or {}).get("sources", {}))
    return out


def require_source(name: str) -> dict:
    """Ninguna fuente se ingiere sin entrada validada en sources.yaml (§3.8)."""
    s = load_sources().get(name)
    if not s or not s.get("validated"):
        raise SourceNotValidated(f"la fuente '{name}' no está validada en config/sources.yaml")
    return s


class Http:
    def __init__(self, source: str, cache: bool = True, cfg: dict | None = None):
        self.cfg = cfg or require_source(source)
        self.min_interval = 1.0 / float(self.cfg.get("rate_limit_per_s", 1))
        self.cache_dir = DATA / "http" / source if cache else None
        self._last = 0.0
        self.client = httpx.Client(headers={"User-Agent": UA}, timeout=60, follow_redirects=True)

    def _wait(self):
        d = self.min_interval - (time.monotonic() - self._last)
        if d > 0:
            time.sleep(d)
        self._last = time.monotonic()

    def get(self, url: str, params: dict | None = None, headers: dict | None = None, *, use_cache: bool = True) -> httpx.Response | str:
        key = sha256(json.dumps([url, params, headers], sort_keys=True))
        cp = self.cache_dir / key if self.cache_dir else None
        if use_cache and cp and cp.exists():
            return cp.read_text()
        for attempt in range(4):
            self._wait()
            r = self.client.get(url, params=params, headers=headers)
            if r.status_code in (429, 502, 503, 504):
                time.sleep(2 ** (attempt + 1))
                continue
            r.raise_for_status()
            break
        else:
            r.raise_for_status()
        if cp:
            cp.parent.mkdir(parents=True, exist_ok=True)
            cp.write_text(r.text)
        return r.text

    def download(self, url: str, dest: Path, params: dict | None = None, headers: dict | None = None, max_bytes: int | None = None) -> int:
        """Descarga en streaming (ficheros grandes) respetando el límite de peticiones. Reanuda si `dest` ya existe parcial.
        Con `max_bytes` corta al llegar (para muestras). -> bytes escritos en total."""
        dest.parent.mkdir(parents=True, exist_ok=True)
        have = dest.stat().st_size if dest.exists() else 0
        h = dict(headers or {})
        if have:
            h["Range"] = f"bytes={have}-"
        self._wait()
        with self.client.stream("GET", url, params=params, headers=h) as r:
            if r.status_code == 416:
                return have
            r.raise_for_status()
            mode = "ab" if r.status_code == 206 else "wb"
            if mode == "wb":
                have = 0
            with open(dest, mode) as f:
                for chunk in r.iter_bytes(1 << 20):
                    f.write(chunk)
                    have += len(chunk)
                    if max_bytes and have >= max_bytes:
                        break
        return have

    def json(self, url, params=None, headers=None, **kw):
        return json.loads(self.get(url, params, headers, **kw))
