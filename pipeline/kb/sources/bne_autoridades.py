"""Conector bne_autoridades (ver bne.py)."""
from functools import partial

from . import bne

phase0 = partial(bne.phase0, "bne_autoridades")


def ingest(con, limit=None, **kw):
    return bne.ingest(con, limit, name="bne_autoridades", **kw)
