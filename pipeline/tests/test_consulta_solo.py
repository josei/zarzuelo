"""Pruebas de consulta_solo. Todos los identificadores y títulos son FICTICIOS (marcados)."""
import pytest

from kb.sources.consulta_solo import cita

FECHA = "2026-01-02"  # ficticia


def test_juan_march_ficha_ficticia():
    c = cita("juan_march_teatro_musical", id="999999", titulo="[FICTICIO] Obra de prueba", fecha_consulta=FECHA)
    assert c["url"] == "https://www.march.es/es/coleccion/teatro-musical-espanol/ficha/--999999"
    assert c["reuse"] == "consult_only"
    assert c["accessed"] == FECHA and "[FICTICIO]" in c["citation"]


def test_espinos_y_cdaem_ficticios():
    e = cita("espinos_memoria_madrid", id="1", fecha_consulta=FECHA)
    assert e["url"].endswith("buscador.php?accion=VerFicha&id=1")
    c = cita("cdaem_musicadanza", slug="inventario-ficticio-0000-0001", fecha_consulta=FECHA)
    assert c["url"].endswith("/programas-de-mano/inventario-ficticio-0000-0001")
    assert c["reuse"] == e["reuse"] == "consult_only"


def test_iccmu_indice_y_url():
    assert cita("iccmu_diccionario_zarzuela", letra="Z", fecha_consulta=FECHA)["url"].endswith("indice?l=z&p=1")
    u = "https://www.diccionariodelazarzuela.es/dz/voz-ficticia/0"
    assert cita("iccmu_diccionario_zarzuela", url=u, fecha_consulta=FECHA)["url"] == u
    with pytest.raises(ValueError):
        cita("iccmu_diccionario_zarzuela", url="https://ejemplo.invalid/x")


def test_validaciones():
    with pytest.raises(KeyError):
        cita("inexistente")
    with pytest.raises(ValueError):
        cita("juan_march_teatro_musical", id="12; DROP")
    with pytest.raises(ValueError):
        cita("cdaem_musicadanza", slug="../x")
    with pytest.raises(ValueError):
        cita("espinos_memoria_madrid", id="1", fecha_consulta="ayer")


def test_fecha_por_defecto():
    assert len(cita("espinos_memoria_madrid", id="2")["accessed"]) == 10
