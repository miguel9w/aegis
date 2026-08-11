"""G4 — Aprendizados estruturados e versionados + grafo de conhecimento.

A reflexão pós-turno (C1) classifica cada lição em 4 categorias (decisão,
lição, padrão, surpresa), grava em `docs/learnings/<sessao>.md` (versionado)
e indexa no grafo de conhecimento consultável via tool `consultar_grafo`
(sem LLM, sem rede). Regressão: sem ferramentas → nenhum arquivo novo.
"""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from aegis.aprendizados import (
    GrafoConhecimento,
    bloco_markdown,
    classificar,
    nome_arquivo_sessao,
)
from aegis.grafo import montar_grafo
from aegis.memoria import criar_checkpointer_sync, criar_store_sync
from conftest import ModeloFake, basico_tools, chamada_tool


# ---------------------------------------------------------------------
# Classificação por regras (4 categorias)
# ---------------------------------------------------------------------

def test_classificar_quatro_categorias():
    assert classificar("decidimos usar uv para gerenciar o projeto") == "decisao"
    assert classificar("foi uma surpresa: o teste passou sem mock") == "surpresa"
    assert classificar("padrão recorrente: sempre validar entrada") == "padrao"
    assert classificar("aprendemos que o timeout precisa ser maior") == "licao"
    # fallback: sem palavra-chave → lição
    assert classificar("o cache da api") == "licao"


def test_nome_arquivo_sessao_sanitiza():
    assert nome_arquivo_sessao("default") == "default"
    assert nome_arquivo_sessao("t/1:2") == "t_1_2"
    assert nome_arquivo_sessao("") == "default"


def test_bloco_markdown_traz_categorias_e_prioridades():
    bloco = bloco_markdown([
        ("decidimos usar X", "alta", "decisao"),
        ("falha no build", "media", "licao"),
    ], ts="2026-08-11 10:00:00")
    assert "## 2026-08-11 10:00:00" in bloco
    assert "**[decisao]**" in bloco and "*(prioridade alta)*" in bloco
    assert "**[licao]**" in bloco and "*(prioridade media)*" in bloco


# ---------------------------------------------------------------------
# Grafo de conhecimento — navegação por relação (sem LLM, sem rede)
# ---------------------------------------------------------------------

def test_grafo_consulta_direta_e_relacionada(tmp_path):
    grafo = GrafoConhecimento(tmp_path / "grafo.json")
    grafo.adicionar("decisao", "decidimos usar docker para o sandbox",
                    ferramenta="comando_sandbox", fase="execute", erro="")
    grafo.adicionar("licao", "aprendemos que o docker precisa de timeout",
                    ferramenta="comando_sandbox", fase="execute", erro="")
    grafo.adicionar("padrao", "padrão: sempre verificar o healthz",
                    ferramenta="curl", fase="verify", erro="")

    # direta: a lição do timeout casa o termo; relacionada: a decisão
    # compartilha a MESMA ferramenta (navegação de grau 1, sem LLM)
    itens = grafo.consultar("timeout")
    diretas = [i for i in itens if i["tipo"] == "direta"]
    relacionadas = [i for i in itens if i["tipo"] == "relacionada"]
    assert len(diretas) == 1 and diretas[0]["categoria"] == "licao"
    assert any("docker para o sandbox" in i["texto"] for i in relacionadas)
    assert any(i["categoria"] == "decisao" for i in relacionadas)
    # consulta por categoria também navega (mesma categoria ↔ relacionadas)
    assert any(i["categoria"] == "decisao" for i in grafo.consultar("decisao"))

    assert "docker" in grafo.formatar("timeout")
    assert "Nada encontrado" in grafo.formatar("zzz-inexistente")
    assert grafo.consultar("") == []


def test_grafo_persiste_e_recarrega(tmp_path):
    caminho = tmp_path / "grafo.json"
    grafo = GrafoConhecimento(caminho)
    grafo.adicionar("licao", "aprendemos algo importante", ferramenta="git")
    grafo.salvar()
    recarregado = GrafoConhecimento(caminho)
    assert len(recarregado.entidades) == 1
    assert any(i["tipo"] == "direta" for i in recarregado.consultar("git"))


# ---------------------------------------------------------------------
# Reflexão pós-turno G4: documento versionado + grafo (fluxo completo)
# ---------------------------------------------------------------------

def _cfg(tmp_path):
    from aegis.config import Config
    c = Config()
    c.banco = tmp_path / "teste.db"
    c.thread_id = "t-g4"
    c.limiar_compressao = 100
    c.memoria_ativa = True
    c.learnings_dir = tmp_path / "docs" / "learnings"
    c.grafo_path = tmp_path / "grafo.json"
    return c


def _app(tmp_path, modelo):
    cfg = _cfg(tmp_path)
    checkpointer = criar_checkpointer_sync(cfg.banco)
    st = criar_store_sync(cfg.banco)
    return montar_grafo(modelo, basico_tools(), checkpointer=checkpointer,
                        store=st, cfg=cfg), cfg


def _resposta_verificacao(veredito, evidencias):
    return AIMessage(content=json.dumps(
        {"veredito": veredito, "evidencias": evidencias}, ensure_ascii=False))


def _resposta_licoes(licoes):
    return AIMessage(content=json.dumps({"licoes": licoes}, ensure_ascii=False))


def test_reflexao_grava_arquivo_versionado_e_grafo(tmp_path):
    """Critério de aceite: após vários turnos, docs/learnings/<sessao>.md tem
    as 4 categorias (acumuladas) e o grafo responde consultas de relação."""
    modelo = ModeloFake()
    modelo.configurar([
        # turno 1 — decisao + surpresa (5 chamadas LLM: tool, resposta,
        # verificação, resumo estrutural, reflexão)
        chamada_tool("calculadora", {"expressao": "2+2"}, id_chamada="call_a"),
        AIMessage(content="Resultado: 4."),
        _resposta_verificacao("ok", [{"fonte": "calculadora", "conferida": True,
                                      "observacao": "bate"}]),
        AIMessage(content='{"resumo": "turno 1 ok"}'),
        _resposta_licoes([
            {"texto": "decidimos usar uv para o projeto", "prioridade": "media"},
            {"texto": "foi uma surpresa: o teste passou sem mock", "prioridade": "media"},
        ]),
        # turno 2 — padrao + licao
        chamada_tool("calculadora", {"expressao": "3+3"}, id_chamada="call_b"),
        AIMessage(content="Resultado: 6."),
        _resposta_verificacao("ok", [{"fonte": "calculadora", "conferida": True,
                                      "observacao": "bate"}]),
        AIMessage(content='{"resumo": "turno 2 ok"}'),
        _resposta_licoes([
            {"texto": "padrão recorrente: validar entrada sempre", "prioridade": "media"},
            {"texto": "aprendemos que o timeout precisa ser maior", "prioridade": "media"},
        ]),
    ])
    app, cfg = _app(tmp_path, modelo)
    config_exec = {"configurable": {"thread_id": cfg.thread_id}}
    r1 = app.invoke({"mensagens": [HumanMessage("calcule 2+2")],
                     "metadados_sessao": {"thread_id": cfg.thread_id}},
                    config=config_exec)
    r2 = app.invoke({"mensagens": [HumanMessage("calcule 3+3")],
                     "metadados_sessao": {"thread_id": cfg.thread_id}},
                    config=config_exec)
    assert r1.get("licoes_turno") and r2.get("licoes_turno")

    arquivo = cfg.learnings_dir / "t-g4.md"
    assert arquivo.exists(), "documento versionado não criado"
    conteudo = arquivo.read_text(encoding="utf-8")
    for categoria in ("decisao", "surpresa", "padrao", "licao"):
        assert f"[{categoria}]" in conteudo, f"categoria {categoria} ausente"

    grafo = GrafoConhecimento(cfg.grafo_path)
    assert len(grafo.entidades) == 4
    # relação com a ferramenta do turno (calculadora) navegável
    assert any(i["tipo"] == "direta" for i in grafo.consultar("calculadora"))
    # decisão e lição compartilham a ferramenta → navegação de grau 1
    assert any(i["tipo"] == "relacionada" for i in grafo.consultar("timeout"))


def test_reflexao_sem_ferramentas_nao_cria_arquivo(tmp_path, monkeypatch):
    """Regressão do C1: turno sem ferramentas → nenhum arquivo novo."""
    modelo = ModeloFake()
    modelo.configurar([AIMessage(content="oi!")])
    app, cfg = _app(tmp_path, modelo)
    resultado = app.invoke(
        {"mensagens": [HumanMessage("oi")],
         "metadados_sessao": {"thread_id": cfg.thread_id}},
        config={"configurable": {"thread_id": cfg.thread_id}},
    )
    assert not resultado.get("licoes_turno")
    assert not (cfg.learnings_dir / "t-g4.md").exists()
    assert not cfg.grafo_path.exists()


def test_reflexao_com_lição_vazia_nao_grava_arquivo(tmp_path):
    """LLM retorna sem lições → zero arquivo/grafo (zero custo)."""
    modelo = ModeloFake()
    modelo.configurar([
        chamada_tool("calculadora", {"expressao": "1+1"}, id_chamada="call_a"),
        AIMessage(content="Resultado: 2."),
        _resposta_verificacao("ok", [{"fonte": "calculadora", "conferida": True,
                                      "observacao": "bate"}]),
        _resposta_licoes([]),
    ])
    app, cfg = _app(tmp_path, modelo)
    resultado = app.invoke(
        {"mensagens": [HumanMessage("calcule 1+1")],
         "metadados_sessao": {"thread_id": cfg.thread_id}},
        config={"configurable": {"thread_id": cfg.thread_id}},
    )
    assert not resultado.get("licoes_turno")
    assert not (cfg.learnings_dir / "t-g4.md").exists()
    assert not cfg.grafo_path.exists()


# ---------------------------------------------------------------------
# Tool consultar_grafo (via ferramenta, sem rede)
# ---------------------------------------------------------------------

def test_tool_consultar_grafo(monkeypatch, tmp_path):
    from aegis.config import config
    from aegis.ferramentas.basicas import consultar_grafo

    grafo = GrafoConhecimento(tmp_path / "grafo.json")
    grafo.adicionar("licao", "aprendemos que o banco precisa de índice",
                    ferramenta="sqlite", fase="execute", erro="")
    grafo.salvar()
    monkeypatch.setattr(config, "grafo_path", tmp_path / "grafo.json")

    saida = consultar_grafo.invoke({"termo": "sqlite"})
    assert "Grafo de conhecimento" in saida
    assert "banco precisa de índice" in saida
    assert "[licao]" in saida

    vazio = consultar_grafo.invoke({"termo": "nada-que-exista"})
    assert "Nada encontrado" in vazio
