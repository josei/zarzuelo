"""Pruebas de bne.py y viaf.py. Todos los datos son FICTICIOS; sin red."""
import io
import json
import zipfile

import httpx

from kb import db as kbdb
from kb.sources import bne, viaf

CFG = {"license": "CC-BY-4.0", "reuse": "reusable", "rate_limit_per_s": 1000}
# FICTICIO: cabeceras y filas inventadas para la prueba.
CSV = ("Identificador,Encabezamiento,Notas\n"
       "XX100001,\"Ficticia, Ana, 1850-1910\",Compositora ficticia\n"
       "XX100002,\"Otro, Pepe, 1800-1860\",Nada relevante\n"
       "XX100003,\"Tercero, Luis\",\"Libretista de zarzuela ficticia,\nmultilínea\"\n")


def make_zip(path, data: str, name="x.csv"):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(name, data.encode("utf-8"))


class FakeHttp:
    def __init__(self, zpath, status=200):
        self.cfg, self.zpath, self.status, self.client = CFG, zpath, status, self
        self.urls = []

    def _wait(self): pass

    def head(self, url, follow_redirects=True):
        req = httpx.Request("HEAD", url)
        return httpx.Response(self.status, headers={"content-length": "123"}, request=req)

    def download(self, url, dest, max_bytes=None, **kw):
        self.urls.append(url)
        if self.status != 200:
            raise httpx.HTTPStatusError("x", request=httpx.Request("GET", url), response=httpx.Response(self.status))
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(self.zpath.read_bytes())
        return dest.stat().st_size


def seed(con):
    con.execute("INSERT INTO entities(id,type,label,external_ids) VALUES('Q1','person','Ana Ficticia','{\"wikidata\":\"Q1\"}')")
    con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,status,source_kind) VALUES('c1','Q1','birth_date','\"1850\"','edtf','asserted','structured')")
    con.execute("INSERT INTO claims(id,subject_id,property,value_json,value_type,status,source_kind) VALUES('c2','Q1','death_date','\"1910-05\"','edtf','asserted','structured')")
    con.execute("INSERT INTO entities(id,type,label,external_ids) VALUES('Q2','person','Pepe Otro','{\"bne\":\"XX100002\",\"viaf\":\"9\"}')")
    con.commit()


def test_zip_streaming_truncated(tmp_path):
    z = tmp_path / "a.zip"
    big = "id,t\n" + "".join(f"{i},fila {i}\n" for i in range(20000))
    make_zip(z, big)
    cut = tmp_path / "cut.zip"
    cut.write_bytes(z.read_bytes()[: z.stat().st_size // 2])  # prefijo: sin directorio central
    rows = list(bne.read_records(cut))
    assert 100 < len(rows) < 20000 and rows[0] == {"id": "0", "t": "fila 0"}


def test_json_records(tmp_path):
    z = tmp_path / "j.zip"
    make_zip(z, json.dumps([{"a": "1", "b": ["x", "y"]}, {"a": "2"}]), "x.json")
    assert list(bne.read_records(z, "json")) == [{"a": "1", "b": "x | y"}, {"a": "2"}]


def test_name_and_years():
    assert bne.name_key("Ficticia, Ana, 1850-1910") == "ana ficticia"
    assert bne.heading_years("Ficticia, Ana, 1850-1910") == (1850, 1910)


def test_ingest_authorities(tmp_path, con):
    seed(con)
    z = tmp_path / "p.zip"
    make_zip(z, CSV)
    st = bne.ingest(con, None, name="bne_autoridades", http=FakeHttp(z))
    assert st["scanned"] == 3 * 2 and st["reconciled"] == 1  # persona + entidad usan el mismo zip simulado
    ext = json.loads(con.execute("SELECT external_ids FROM entities WHERE id='Q1'").fetchone()[0])
    assert ext == {"wikidata": "Q1", "bne": "XX100001"}  # fusiona sin pisar
    # el ID BNE conocido de Q2 coincide pero ya estaba: no cuenta; ruido ('Otro, Pepe') solo entra por ID
    assert con.execute("SELECT COUNT(*) FROM documents WHERE source='bne_autoridades'").fetchone()[0] == 4  # 3 persona + 1 entidad (solo por tema)
    assert con.execute("SELECT COUNT(*) FROM passages").fetchone()[0] >= 4
    assert bne.ingest(con, None, name="bne_autoridades", http=FakeHttp(z))["documents"] == 0  # idempotente


def test_no_conflict_overwrite(con):
    seed(con)
    assert bne.merge_external(con, "Q2", "bne", "XX999") == "conflict"
    assert json.loads(con.execute("SELECT external_ids FROM entities WHERE id='Q2'").fetchone()[0])["bne"] == "XX100002"


def test_name_without_dates_not_reconciled(con):
    seed(con)
    m = bne.Matcher(bne.corpus_people(con))
    assert m.person("XX5", "Ficticia, Ana")[0] is None
    assert m.person("XX5", "Ficticia, Ana, 1851-1910")[0] is None
    assert m.person("XX5", "Ficticia, Ana, 1850-1910")[1] == "name+dates"


def test_bibliographic_filter_and_limit(tmp_path, con):
    z = tmp_path / "b.zip"
    make_zip(z, "titulo,autor\nZarzuela ficticia en tres actos,Ficticia\nManual de cocina,Nadie\nTonadilla ficticia,X\n")
    st = bne.ingest(con, 1, name="bne_partituras", http=FakeHttp(z))
    assert st["scanned"] == 1 and st["documents"] == 1


def test_blocked_phase0(tmp_path, monkeypatch):
    z = tmp_path / "p.zip"
    make_zip(z, CSV)
    monkeypatch.setattr(bne, "Http", lambda name, cfg=None: _Blocked(FakeHttp(z, 403)))
    rep = bne.phase0("bne_autoridades", 10)
    assert rep["validated"] is False and "403" in rep["blocked"]


class _Blocked:
    def __init__(self, f): self.f = f

    def __getattr__(self, k): return getattr(self.f, k)

    def get(self, *a, **k): return "User-agent: *"


def test_phase0_ok(tmp_path, monkeypatch):
    z = tmp_path / "p.zip"
    make_zip(z, CSV)
    monkeypatch.setattr(bne, "Http", lambda name, cfg=None: _Blocked(FakeHttp(z)))
    rep = bne.phase0("bne_autoridades", 2)
    assert rep["validated"] and len(rep["sample"]) == 2 and rep["files"]["persona"]["size_bytes"] == 123


def test_wrapper_modules():
    from kb.sources import bne_autoridades, bne_biblioteca_digital, bne_grabaciones, bne_partituras
    for m in (bne_autoridades, bne_biblioteca_digital, bne_grabaciones, bne_partituras):
        assert callable(m.phase0) and callable(m.ingest)


class FakeViaf:
    cfg = CFG

    def get(self, url, params=None, headers=None):
        if "sourceID/BNE%7CXX100001" in url:
            return json.dumps({"viafID": "111", "mainHeadings": {"data": [{"text": "Ficticia, Ana, 1850-1910"}]}, "birthDate": "1850"})
        raise httpx.HTTPStatusError("nf", request=httpx.Request("GET", url), response=httpx.Response(404))


def test_viaf_by_bne_id(con):
    seed(con)
    con.execute("UPDATE entities SET external_ids='{\"bne\":\"XX100001\"}' WHERE id='Q1'")
    st = viaf.ingest(con, None, http=FakeViaf())
    assert st["reconciled"] == 1  # Q2 ya tiene viaf: se salta
    assert json.loads(con.execute("SELECT external_ids FROM entities WHERE id='Q1'").fetchone()[0]) == {"bne": "XX100001", "viaf": "111"}
