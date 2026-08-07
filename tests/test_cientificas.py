"""
Testes das features científicas (aegis/cientificas.py).

Cobrem: parse do feed Atom (puro), BibTeX/APA determinísticos, biblioteca
local e o fallback offline de rede.
"""

from __future__ import annotations

import json

import pytest
import requests

from aegis import cientificas as ci
from aegis.config import config

# Amostra mínima e realista de um feed Atom do arXiv (2 entries)
FIXTURE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>ArXiv Query</title>
  <entry>
    <id>http://arxiv.org/abs/2401.12345v2</id>
    <published>2025-01-15T00:00:00Z</published>
    <title>Um estudo sobre agentes autônomos</title>
    <summary>Resumo curto do paper um.</summary>
    <author><name>Maria Silva</name></author>
    <link href="http://arxiv.org/abs/2401.12345v2" rel="alternate" type="text/html"/>
    <link href="http://arxiv.org/pdf/2401.12345v2" title="pdf"/>
  </entry>
  <entry>
    <id>http://arxiv.org/abs/2312.99999v1</id>
    <published>2024-06-01T00:00:00Z</published>
    <title>Memória de longo prazo em LLMs</title>
    <summary>Resumo do segundo artigo.</summary>
    <author><name>João Souza</name></author>
  </entry>
</feed>
"""


def _paper():
    papers = ci.parsear_arxiv_xml(FIXTURE_XML)
    return papers[0]


def test_parsear_feed_atom():
    papers = ci.parsear_arxiv_xml(FIXTURE_XML)
    assert len(papers) == 2
    p = papers[0]
    assert p["id"] == "2401.12345v2"
    assert p["titulo"] == "Um estudo sobre agentes autônomos"
    assert p["autores"] == ["Maria Silva"]
    assert p["publicado"] == "2025-01-15"
    assert p["pdf"].endswith("/pdf/2401.12345v2")


def test_parser_xml_invalido():
    assert ci.parsear_arxiv_xml("{{{ não é xml") == []


def test_extrair_arxiv_id():
    assert ci._extrair_arxiv_id("http://arxiv.org/abs/2401.12345v2") == "2401.12345v2"
    assert ci._extrair_arxiv_id("") == ""


def test_gerar_bibtex_deterministico():
    bib = ci.gerar_bibtex(_paper())
    assert "@article{Silva2025" in bib
    assert "Maria Silva" in bib
    assert "eprint = {2401.12345v2}" in bib


def test_citar_apa():
    cit = ci.citar_apa(_paper())
    assert cit.startswith("Silva, M.")
    assert "2025" in cit
    assert "2401.12345v2" in cit


def test_citar_apa_multiplos_autores():
    xml = FIXTURE_XML.replace("<name>João Souza</name>",
                              "<name>João Souza</name><name>Ana Lima</name>")
    papers = ci.parsear_arxiv_xml(xml)
    apa = ci.citar_apa(papers[1])
    assert "Souza, J" in apa and "et al." in apa


def test_salvar_na_biblioteca_dedupe(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "biblioteca_path", tmp_path / "biblioteca.json")
    p = _paper()
    assert ci._salvar_paper_biblioteca(p) is True
    assert ci._salvar_paper_biblioteca(p) is False
    assert len(ci._biblioteca()) == 1


def test_biblioteca_arquivo_invalido(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "biblioteca_path", tmp_path / "biblioteca.json")
    (tmp_path / "biblioteca.json").write_text("lixo", encoding="utf-8")
    assert ci._biblioteca() == []


def test_buscar_papers_falha_offline(monkeypatch):
    """Falha de rede → [] (o agente nunca cai por rede)."""

    def _fake_get(*a, **k):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr(ci.requests, "get", _fake_get)
    assert ci.buscar_papers("qualquer coisa") == []


def test_buscar_paper_por_id_offline(monkeypatch):
    def _raise_():
        raise requests.ConnectionError

    monkeypatch.setattr(ci.requests, "get", lambda *a, **k: _raise_())
    assert ci.buscar_paper_por_id("2401.12345v2") is None