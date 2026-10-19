import pytest

from kb import db as kbdb
from kb import store
from kb.llm.core import LLM, MockBackend, load_config


@pytest.fixture(autouse=True)
def tmp_data(tmp_path, monkeypatch):
    """Aísla data/ en un directorio temporal: las pruebas nunca tocan la base real."""
    monkeypatch.setattr(kbdb, "DATA", tmp_path)
    monkeypatch.setattr(store, "DATA", tmp_path)
    import kb.generate as g
    monkeypatch.setattr(g, "DATA", tmp_path)
    return tmp_path


@pytest.fixture
def con():
    return kbdb.connect(":memory:")


def make_llm(con, responses):
    return LLM(con, {"mock": MockBackend(responses)}, load_config())
