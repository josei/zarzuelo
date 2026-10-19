"""Conector bne_biblioteca_digital (ver bne.py)."""
from functools import partial

from . import bne

phase0 = partial(bne.phase0, "bne_biblioteca_digital")


def ingest(con, limit=None, **kw):
    return bne.ingest(con, limit, name="bne_biblioteca_digital", **kw)
