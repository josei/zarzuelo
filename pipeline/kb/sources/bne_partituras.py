"""Conector bne_partituras (ver bne.py)."""
from functools import partial

from . import bne

phase0 = partial(bne.phase0, "bne_partituras")


def ingest(con, limit=None, **kw):
    return bne.ingest(con, limit, name="bne_partituras", **kw)
