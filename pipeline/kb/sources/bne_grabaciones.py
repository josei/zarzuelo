"""Conector bne_grabaciones (ver bne.py)."""
from functools import partial

from . import bne

phase0 = partial(bne.phase0, "bne_grabaciones")


def ingest(con, limit=None, **kw):
    return bne.ingest(con, limit, name="bne_grabaciones", **kw)
