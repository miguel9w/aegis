"""Testes dos subagentes avançados (agent-as-tool). Determinístico, sem rede."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

import aegis.subagentes as sub
from aegis.config import config
from aegis.prompts import sistema_pesquisador, sistema_redator
from conftest import ModeloFake, chamada_tool  # noqa: F401 (reuso do fake)

C = {"configurable": {"thread_id": "t-sub"}}


def _sub(nome: str, prompt: str, ferramentas: list, llm):
    return sub.criar_subagente(nome, prompt, ferramentas, config, llm)


def test_pesquisador_usa_ferramenta_e_responde():
    from aegis.ferramentas.basicas import ferramentas_basicas

    calculadora = next(f for f in ferramentas_basicas() if f.name == "calculadora")
    llm = ModeloFake()
    llm.configurar([
        chamada_tool("calculadora", {"expressao": "2+2"}),
        AIMessage(content="O resultado é 4."),
    ])
    app = _sub("pesquisador", sistema_pesquisador(), [calculadora], llm)
    resultado = app.invoke({"mensagens": [HumanMessage("quanto é 2+2?")]}, C)

    assert "4" in sub._resposta_final(resultado)
    registros = resultado.get("registros_ferramentas") or []
    assert any(r["nome"] == "calculadora" for r in registros)


def test_redator_gera_texto_sem_ferramentas():
    llm = ModeloFake()
    llm.configurar([
        AIMessage(content="Título\n\nParágrafo longo em pt-BR."),
    ])
    app = _sub("redator", sistema_redator(), [], llm)
    resultado = app.invoke({"mensagens": [HumanMessage("escreva sobre café")]}, C)

    saida = sub._resposta_final(resultado)
    assert "Título" in saida
    assert (resultado.get("registros_ferramentas") or []) == []


def test_delegar_redacao_invoca_subagente(monkeypatch):
    llm = ModeloFake()
    llm.configurar([
        AIMessage(content="Texto do redator"),
    ])
    app = _sub("redator", sistema_redator(), [], llm)
    monkeypatch.setattr(sub, "SUBAGENTES_ATUAIS", {"redator": app})

    saida = sub.delegar_redacao.invoke({"tarefa": "escreva um parágrafo"})
    assert saida == "Texto do redator"


def test_delegar_redacao_aceita_contexto(monkeypatch):
    llm = ModeloFake()
    llm.configurar([
        AIMessage(content="resposta com contexto"),
    ])
    app = _sub("redator", sistema_redator(), [], llm)
    monkeypatch.setattr(sub, "SUBAGENTES_ATUAIS", {"redator": app})

    saida = sub.delegar_redacao.invoke(
        {"tarefa": "resuma", "contexto": "fatos: chama-se Miguel"}
    )
    assert saida == "resposta com contexto"


def test_delegar_sem_subagente_configurado(monkeypatch):
    monkeypatch.setattr(sub, "SUBAGENTES_ATUAIS", {})
    saida = sub.delegar_pesquisa.invoke({"pergunta": "qual a capital?"})
    assert "ERRO_FERRAMENTA" in saida
    assert "não configurado" in saida


def test_configurar_subagentes_registra_ambos():
    sub.configurar_subagentes(ModeloFake(), config)
    assert set(sub.SUBAGENTES_ATUAIS) == {"pesquisador", "redator"}


def test_erro_de_ferramenta_dispara_reflexao_no_subagente():
    @tool
    def sempre_falha(descricao: str = "") -> str:
        """Ferramenta que sempre falha (para testar a auto-correção)."""
        return "ERRO_FERRAMENTA: falha proposital"

    llm = ModeloFake()
    llm.configurar([
        chamada_tool("sempre_falha", {"descricao": "x"}),
        AIMessage(content="corrigi e completo a resposta"),
    ])
    app = _sub("pesquisador", sistema_pesquisador(), [sempre_falha], llm)
    resultado = app.invoke({"mensagens": [HumanMessage("faca algo")]}, C)

    assert resultado.get("tentativas_correcao") == 1
    assert "corrigi" in sub._resposta_final(resultado)
    erros = resultado.get("erros_ferramenta") or []
    assert any("proposital" in e for e in erros)