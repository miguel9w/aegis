"""Testes da Fase X3 — Fact-checking com fontes (paridade web-deep-research).

Cobre: extração de fontes `{url, titulo, trecho}` do registro da `buscar_web`,
classificação determinística (afirmado / divergencia / fonte_unica), bloco
"Fontes verificadas" anexado à resposta, turno sem web → zero custo, e a
integração no grafo com busca fake (concordância e conflito).
"""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from aegis import factcheck as fc
from aegis.seguranca import marcar_conteudo


def _registro_busca(consulta: str, fontes: list[dict]) -> dict:
    """Registro de ferramenta como o `no_ferramentas` grava (resultado marcado C5)."""
    return {
        "nome": "buscar_web",
        "args": {"consulta": consulta, "max_resultados": 2},
        "resultado": marcar_conteudo(json.dumps(fontes, ensure_ascii=False), fonte="busca web"),
        "erro": False,
    }


def _fonte(url: str, trecho: str) -> dict:
    return {"url": url, "titulo": f"Titulo {url}", "trecho": trecho}


# ---------------------------------------------------------------------
# Extração de fontes
# ---------------------------------------------------------------------

def test_extrair_fontes_do_registro_json():
    registro = _registro_busca("aegis", [
        {"url": "https://ex.com/1", "titulo": "Um", "trecho": "Aegis usa LangGraph."},
        {"url": "https://ex.com/2", "titulo": "Dois", "trecho": "Aegis usa LangGraph para orquestracao."},
    ])
    fontes = fc.extrair_fontes([registro])
    assert len(fontes) == 2
    assert fontes[0]["url"] == "https://ex.com/1"
    assert fontes[0]["consulta"] == "aegis"
    assert "trecho" in fontes[0] and "titulo" in fontes[0]


def test_extrair_fontes_ignora_erro_e_outras_tools():
    registro_erro = _registro_busca("aegis", [])
    registro_erro["erro"] = True
    outros = [{"nome": "calculadora", "args": {}, "resultado": "42", "erro": False}]
    assert fc.extrair_fontes([registro_erro, outros[0]]) == []


def test_extrair_fontes_resultado_sem_json():
    registro = {"nome": "buscar_web", "args": {"consulta": "x"}, "resultado": "sem resultados", "erro": False}
    assert fc.extrair_fontes([registro]) == []


# ---------------------------------------------------------------------
# Classificação determinística
# ---------------------------------------------------------------------

def test_duas_fontes_concordando_afirmado():
    fontes = [
        {"consulta": "aegis", **_fonte("https://ex.com/1", "O Aegis usa LangGraph para orquestrar agentes com persistencia em sqlite.")},
        {"consulta": "aegis", **_fonte("https://ex.com/2", "O Aegis usa LangGraph para orquestrar agentes e salva estado em sqlite.")},
    ]
    afirmacoes = fc.classificar_afirmacoes(fontes)
    assert afirmacoes[0]["status"] == "afirmado"
    assert set(afirmacoes[0]["urls"]) == {"https://ex.com/1", "https://ex.com/2"}


def test_fontes_conflitantes_divergencia():
    fontes = [
        {"consulta": "aegis", **_fonte("https://ex.com/1", "O Aegis roda apenas na maquina local.")},
        {"consulta": "aegis", **_fonte("https://ex.com/2", "O Aegis exige cluster kubernetes distribuido.")},
    ]
    afirmacoes = fc.classificar_afirmacoes(fontes)
    assert afirmacoes[0]["status"] == "divergencia"
    assert len(afirmacoes[0]["urls"]) == 2  # cita as duas


def test_contradicao_lexical_divergencia():
    """Trechos similares, mas um nega → contradição detectada (divergencia)."""
    fontes = [
        {"consulta": "aegis", **_fonte("https://ex.com/1", "O Aegis suporta o backend docker.")},
        {"consulta": "aegis", **_fonte("https://ex.com/2", "O Aegis nao suporta o backend docker.")},
    ]
    afirmacoes = fc.classificar_afirmacoes(fontes)
    assert afirmacoes[0]["status"] == "divergencia"


def test_fonte_unica():
    fontes = [{"consulta": "aegis", **_fonte("https://ex.com/1", "Unico resultado sobre o Aegis.")}]
    afirmacoes = fc.classificar_afirmacoes(fontes)
    assert afirmacoes[0]["status"] == "fonte_unica"
    assert afirmacoes[0]["urls"] == ["https://ex.com/1"]


# ---------------------------------------------------------------------
# Nó no_fact_check
# ---------------------------------------------------------------------

def test_turno_sem_web_zero_custo():
    estado = {"registros_ferramentas": [{"nome": "calculadora", "args": {}, "resultado": "42", "erro": False}]}
    assert fc.no_fact_check(estado) == {}


def test_no_fact_check_anexa_bloco_e_grava_fontes():
    registro = _registro_busca("aegis", [
        {"url": "https://ex.com/1", "titulo": "Um", "trecho": "O Aegis usa LangGraph para orquestrar agentes."},
        {"url": "https://ex.com/2", "titulo": "Dois", "trecho": "O Aegis usa LangGraph para orquestrar agentes com store."},
    ])
    ultima = AIMessage(content="O Aegis usa LangGraph.")
    estado = {"registros_ferramentas": [registro], "mensagens": [ultima]}
    saida = fc.no_fact_check(estado)
    assert saida["fontes"][0]["status"] == "afirmado"
    nova = saida["mensagens"][0]
    assert "Fontes verificadas" in nova.content
    assert "https://ex.com/1" in nova.content
    assert nova.id == ultima.id  # substitui a mesma mensagem (add_messages)


def test_turno_usou_busca_web():
    assert fc.turno_usou_busca_web({"registros_ferramentas": [_registro_busca("x", [])]})
    assert not fc.turno_usou_busca_web({"registros_ferramentas": []})
    erro = _registro_busca("x", [])
    erro["erro"] = True
    assert not fc.turno_usou_busca_web({"registros_ferramentas": [erro]})


# ---------------------------------------------------------------------
# Integração: grafo completo com busca fake
# ---------------------------------------------------------------------

@pytest.fixture
def grafo_com_busca_fake(monkeypatch, tmp_path):
    """Monta o grafo com ModeloFake e DDGS fake (2 fontes por consulta)."""
    import conftest
    from conftest import chamada_tool, ModeloFake
    from aegis.config import config as cfg
    from aegis.ferramentas import recarregar_tudo
    from aegis.ferramentas import basicas as mod_basicas
    from aegis.grafo import montar_grafo
    from aegis.memoria import criar_checkpointer_sync, criar_store_sync

    class DDGSFake:
        def __init__(self, resultados):
            self._resultados = resultados

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, consulta, max_results):
            return self._resultados

    def _montar(resultados_ddgs, respostas):
        monkeypatch.setattr(cfg, "searxng_url", "")
        monkeypatch.setattr(cfg, "artefatos_dir", tmp_path)
        monkeypatch.setattr(cfg, "multiagente_ativos", False)
        # Banco ISOLADO por teste (threads X3 não persistem entre rodadas)
        monkeypatch.setattr(cfg, "banco", str(tmp_path / "agente.db"))
        monkeypatch.setattr(cfg, "learnings_dir", tmp_path / "learnings")  # não poluir docs/ (versionado)
        monkeypatch.setattr(mod_basicas, "DDGS", lambda: DDGSFake(resultados_ddgs))

        modelo = ModeloFake()
        modelo.configurar(respostas)
        ferramentas = recarregar_tudo()
        checkpointer = criar_checkpointer_sync(cfg.banco)
        store = criar_store_sync(str(tmp_path / "store.db"))
        grafo = montar_grafo(modelo, ferramentas, checkpointer=checkpointer, store=store, cfg=cfg)
        return grafo

    return _montar


def test_integracao_duas_fontes_afirmado(grafo_com_busca_fake):
    from conftest import chamada_tool, ModeloFake

    resultados = [
        {"href": "https://ex.com/1", "title": "Um", "body": "O Aegis usa LangGraph para orquestrar agentes com persistencia em sqlite."},
        {"href": "https://ex.com/2", "title": "Dois", "body": "O Aegis usa LangGraph para orquestrar agentes e salva estado em sqlite."},
    ]
    r1 = chamada_tool("buscar_web", {"consulta": "aegis langgraph", "max_resultados": 2}, "bw1")
    r2 = AIMessage(content="O Aegis usa LangGraph, confirmado por duas fontes.")

    grafo = grafo_com_busca_fake(resultados, [r1, r2])
    final = grafo.invoke(
        {"mensagens": [HumanMessage(content="O Aegis usa LangGraph?")]},
        config={"configurable": {"thread_id": "x3-integracao"}},
    )
    assert final["fontes"][0]["status"] == "afirmado"
    assert "https://ex.com/1" in final["mensagens"][-1].content
    assert "Fontes verificadas" in final["mensagens"][-1].content


def test_integracao_fontes_divergentes(grafo_com_busca_fake):
    from conftest import chamada_tool

    resultados = [
        {"href": "https://ex.com/1", "title": "Um", "body": "O Aegis roda apenas na maquina local sem dependencias externas."},
        {"href": "https://ex.com/2", "title": "Dois", "body": "O Aegis exige cluster kubernetes distribuido com orquestracao."},
    ]
    r1 = chamada_tool("buscar_web", {"consulta": "aegis requisitos", "max_resultados": 2}, "bw1")
    r2 = AIMessage(content="Os requisitos do Aegis sao...")

    grafo = grafo_com_busca_fake(resultados, [r1, r2])
    final = grafo.invoke(
        {"mensagens": [HumanMessage(content="Quais os requisitos do Aegis?")]},
        config={"configurable": {"thread_id": "x3-divergencia"}},
    )
    assert final["fontes"][0]["status"] == "divergencia"
    conteudo = final["mensagens"][-1].content
    assert "fontes divergem" in conteudo
    assert "https://ex.com/1" in conteudo and "https://ex.com/2" in conteudo
