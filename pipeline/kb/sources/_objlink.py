"""Enlace conservador de objetos de catálogo (carteles, partituras, libretos, fotos) a entidades del corpus.

Regla: un objeto se enlaza a una OBRA solo si un título normalizado coincide con la etiqueta de la obra Y algún
creador del objeto coincide con el compositor/libretista de esa obra (por identificador BNE/Wikidata o por nombre
exacto normalizado). Se enlaza a una PERSONA solo por identificador o por nombre exacto normalizado.
"""
from __future__ import annotations

import json
import re
import sqlite3

from ..util import norm

_BRACKET = re.compile(r"\[[^\]]*\]|\([^)]*\)")


def _clean(s: str) -> str:
    return re.sub(r"[^\w ]+", " ", norm(s)).strip()


def title_keys(title: str) -> set[str]:
    """Variantes del título: completo (sin corchetes) y antes de ':' / ' / ' / ' ; ' / ', zarzuela'."""
    t = _BRACKET.sub(" ", title)
    keys = {_clean(t)}
    for sep in (":", " / ", ";", ",", " - "):
        if sep in t:
            keys.add(_clean(t.split(sep)[0]))
    return {re.sub(r"\s+", " ", k) for k in keys if len(k) >= 4}


def person_keys(creator: str) -> tuple[set[str], set[str]]:
    """-> (nombres normalizados, identificadores 'bne:XX…'/'wikidata:Q…') de una cadena de creador."""
    ids: set[str] = set()
    m = re.search(r"datos\.bne\.es/resource/(\w+)", creator)
    if m:
        ids.add("bne:" + m.group(1))
    m = re.search(r"wikidata\.org/(?:entity|wiki)/(Q\d+)", creator)
    if m:
        ids.add("wikidata:" + m.group(1))
    if "http" in creator:
        return set(), ids
    s = creator.split(". ")[0]                       # quita "Compositeur", "Auteur du texte"
    s = re.sub(r"\([^)]*\)", " ", s)                 # (1821-1894)
    s = re.sub(r",\s*(m\.|fl\.|ca\.)?\s*[\d?\-–]+.*$", "", s)  # ", 1878-1942" / ", m. 1927"
    names: set[str] = set()
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) == 2:
        names.add(_clean(f"{parts[1]} {parts[0]}"))
    elif len(parts) == 1:
        names.add(_clean(parts[0]))
    return {n for n in names if " " in n}, ids


class Index:
    def __init__(self, con: sqlite3.Connection):
        self.persons: dict[str, set[str]] = {}   # clave (nombre/id) -> entidades
        for r in con.execute("SELECT id,type,label,labels_alt,external_ids FROM entities WHERE type='person' AND provisional=0"):
            for n in [r["label"], *json.loads(r["labels_alt"] or "[]")]:
                self.persons.setdefault(_clean(n), set()).add(r["id"])
            x = json.loads(r["external_ids"] or "{}")
            if x.get("bne"):
                self.persons.setdefault("bne:" + x["bne"], set()).add(r["id"])
            self.persons.setdefault("wikidata:" + r["id"], set()).add(r["id"])
        creators: dict[str, set[str]] = {}
        for r in con.execute("SELECT subject_id, value_json FROM claims WHERE property IN ('composer','librettist') AND value_type='entity'"):
            creators.setdefault(r["subject_id"], set()).add(json.loads(r["value_json"]))
        self.works: dict[str, set[str]] = {}     # clave de título -> obras
        self.creators = creators
        for r in con.execute("SELECT id,label,labels_alt FROM entities WHERE type='work' AND provisional=0"):
            if r["id"] not in creators:
                continue
            for n in [r["label"], *json.loads(r["labels_alt"] or "[]")]:
                k = _clean(_BRACKET.sub(" ", n))
                if len(k) >= 4:
                    self.works.setdefault(k, set()).add(r["id"])

    def people(self, creators: list[str]) -> set[str]:
        out: set[str] = set()
        for c in creators:
            names, ids = person_keys(c)
            for k in [*names, *ids]:
                got = self.persons.get(k, set())
                if len(got) == 1:                # ambigüedad -> no enlazar
                    out |= got
        return out

    def link(self, titles: list[str], creators: list[str]) -> set[str]:
        """Ids de entidades (personas y obras) a las que enlazar el objeto."""
        people = self.people(creators)
        out = set(people)
        for t in titles:
            for k in title_keys(t):
                for w in self.works.get(k, ()):
                    if self.creators.get(w, set()) & people:
                        out.add(w)
        return out
