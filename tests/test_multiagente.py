"""Testes do multiagente: orquestrador, especialistas paralelos, avaliador.

Cobertura determinística (sem rede):
- classificação de domínio por regras (zero LLM)
- parse de veredito do avaliador
- integridade das pools contra a lista real de ferramentas (∪ POOLS ⊆ nomes)
- reducer de rascunhos (merge de escritas paralelas)
- rota pós-orquestrador (legado × subgrafo por domínio)
- fluxo completo com modelo fake: orquestrador → 3 especialistas → integrador
  → avaliador aprovado → entrega
- loop de reprovação: avaliador reprova 1×, segunda rodada é aprovada
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage

from aegis.config import Config
from aegis.estado import _merge_dict
from aegis.ferramentas import recarregar_tudo
from aegis.ferramentas.pools import POOLS, integridade, pool_da_lista
from aegis.grafo import montar_grafo
from aegis.memoria import criar_checkpointer_sync, criar_store_sync
from aegis.multiagente import (
    classificar_dominio,
    divisao_do_dominio,
    montar_multiagente,
    parsear_veredito,
)
from conftest import ModeloFake, basico_tools


def _cfg(tmp_path) -> Config:
    c = Config()
    c.banco = tmp_path / "teste_multi.db"
    c.thread_id = "t-multi"
    c.limiar_compressao = 100
    c.memoria_ativa = True
    c.multiagente_ativos = True
    c.orquestracoes_path = tmp_path / "orquestracoes.jsonl"
    return c


def _app(tmp_path, modelo, cfg=None):
    cfg = cfg or _cfg(tmp_path)
    checkpointer = criar_checkpointer_sync(cfg.banco)
    store = criar_store_sync(cfg.banco)
    return montar_grafo(
        modelo, basico_tools(), checkpointer=checkpointer, store=store, cfg=cfg
    ), cfg


def _invocar(app, cfg, pergunta: str):
    return app.invoke(
        {
            "mensagens": [HumanMessage(pergunta)],
            "metadados_sessao": {"thread_id": cfg.thread_id},
        },
        config={"configurable": {"thread_id": cfg.thread_id}},
    )


# ---------------------------------------------------------------------
# Classificador e parsers (funções puras)
# ---------------------------------------------------------------------


def test_classifica_dominio_por_regras():
    assert classificar_dominio("implemente um app de tarefas em python") == "programacao"
    assert classificar_dominio("pesquise sobre o mercado de IA em 2026") == "pesquisa"
    assert classificar_dominio("escreva um artigo sobre café") == "escrita"
    assert classificar_dominio("organize minhas notas no obsidian") == "obsidian"
    assert classificar_dominio("lembre que prefiro café sem açúcar") == "memoria"
    assert classificar_dominio("oi, tudo bem?") == ""
    assert classificar_dominio("quanto é 2 + 2") == ""


def test_divisao_do_dominio_limita_especialistas():
    slots = divisao_do_dominio("programacao", "crie um app", 3)
    assert len(slots) == 3
    assert [s["slot"] for s in slots] == ["estrutura", "implementacao", "testes"]
    assert all(s["estrategia"] == "paralelo" for s in slots)
    assert divisao_do_dominio("programacao", "crie um app", 1)  # teto mínimo respeitado


def test_parsear_veredito_tolerante():
    bom = parsear_veredito(
        '{"status": "aprovado", "nota": 4.5, "confianca": 0.9, '
        '"feedback": "ok", "criterios_checados": ["coesão"]}'
    )
    assert bom and bom["status"] == "aprovado" and bom["nota"] == 4.5

    com_ruido = parsear_veredito(
        'Aqui vai o JSON:\n```\n{"status": "reprovado", "nota": 2, '
        '"confianca": 0.3, "feedback": "faltam testes"}\n```\nFim.'
    )
    assert com_ruido and com_ruido["status"] == "reprovado"

    assert parsear_veredito("sem json nenhum aqui") is None
    assert parsear_veredito('{"status": "talvez"}') is None


def test_merge_dict_combina_slots_de_escritas_paralelas():
    assert _merge_dict(None, {"a": 1}) == {"a": 1}
    assert _merge_dict({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}
    assert _merge_dict({"a": 1}, {"a": 2}) == {"a": 2}  # slot do mesmo nó vence


# ---------------------------------------------------------------------
# Pools de ferramentas
# ---------------------------------------------------------------------


def test_pools_integridade_contra_lista_real():
    """∪ POOLS ⊆ nomes das ferramentas registradas — nenhuma string órfã."""
    nomes = {f.name for f in recarregar_tudo()}
    orfaos = integridade(nomes)
    assert orfaos == [], f"pools com referências órfãs: {orfaos}"


def test_pool_da_lista_filtra_e_none_devolve_tudo():
    ferramentas = basico_tools()
    todas = pool_da_lista(ferramentas, None)
    assert todas == list(ferramentas)

    fatia = pool_da_lista(ferramentas, "programacao")
    assert len(fatia) >= 1
    assert set(f.name for f in fatia) <= POOLS["programacao"]

    inexistente = pool_da_lista(ferramentas, "dominio-inexistente")
    assert inexistente == list(ferramentas)


# ---------------------------------------------------------------------
# Rota do orquestrador
# ---------------------------------------------------------------------


def test_rota_apos_orquestrador(tmp_path):
    multi = montar_multiagente(_cfg(tmp_path))
    rota = multi["rota_apos_orquestrador"]
    assert rota({"dominio": ""}) == "legado"
    assert rota({"dominio": "programacao"}) == "sub_programacao"
    assert rota({"dominio": "dominio-desconhecido"}) == "legado"


def test_orquestrador_registra_auditoria(tmp_path):
    cfg = _cfg(tmp_path)
    multi = montar_multiagente(cfg)
    estado = {"mensagens": [HumanMessage("implemente um app de tarefas")]}
    saida = multi["no_orquestrador"](estado)
    assert saida["dominio"] == "programacao"
    assert len(saida["divisao"]) == 3
    registros = cfg.orquestracoes_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(registros) == 1
    assert json.loads(registros[0])["dominio"] == "programacao"


def test_orquestrador_dominio_explicito_nos_metadados(tmp_path):
    """`@escrita` na web UI força o subgrafo mesmo sem gatilho no texto."""
    cfg = _cfg(tmp_path)
    multi = montar_multiagente(cfg)
    estado = {
        "mensagens": [HumanMessage("organize essas ideias")],
        "metadados_sessao": {"dominio": "escrita"},
    }
    saida = multi["no_orquestrador"](estado)
    assert saida["dominio"] == "escrita"
    assert len(saida["divisao"]) == 3
    # sem metadados o texto não dispararia domínio nenhum
    sem_meta = multi["no_orquestrador"]({"mensagens": [HumanMessage("organize essas ideias")]})
    assert sem_meta["dominio"] == ""
    # domínio desconhecido nos metadados cai na classificação por regras
    regras = multi["no_orquestrador"]({
        "mensagens": [HumanMessage("implemente um app")],
        "metadados_sessao": {"dominio": "inexistente"},
    })
    assert regras["dominio"] == "programacao"


def test_orquestrador_pergunta_simples_nao_dispara(tmp_path):
    multi = montar_multiagente(_cfg(tmp_path))
    saida = multi["no_orquestrador"]({"mensagens": [HumanMessage("oi")]})
    assert saida["dominio"] == ""
    assert saida["divisao"] == []


# ---------------------------------------------------------------------
# Fluxo completo (modelo fake determinístico)
# ---------------------------------------------------------------------


def test_fluxo_multiagente_aprovado(tmp_path):
    """Orquestrador → 3 especialistas paralelos → integrador → avaliador OK."""
    modelo = ModeloFake()
    modelo.configurar([
        AIMessage(content="Rascunho estrutura: plano de módulos"),
        AIMessage(content="Rascunho implementacao: funções implementadas"),
        AIMessage(content="Rascunho testes: casos cobertos"),
        AIMessage(content="ARTEFATO CONSOLIDADO do integrador"),
        AIMessage(content=json.dumps({
            "status": "aprovado", "nota": 4.8, "confianca": 0.9,
            "feedback": "excelente", "criterios_checados": ["coesão", "execução"],
        })),
    ])
    app, cfg = _app(tmp_path, modelo)
    resultado = _invocar(app, cfg, "implemente um app de tarefas em python")

    assert resultado["dominio"] == "programacao"
    assert set(resultado["rascunhos"]) == {"slot_0", "slot_1", "slot_2"}
    conteudos = [str(v) for v in resultado["rascunhos"].values()]
    assert any("estrutura" in c for c in conteudos)
    assert any("implementacao" in c for c in conteudos)
    assert any("testes" in c for c in conteudos)
    assert resultado["vereditos"][0]["status"] == "aprovado"
    # a resposta entregue ao usuário é o artefato consolidado
    assert resultado["mensagens"][-1].content == "ARTEFATO CONSOLIDADO do integrador"


def test_fluxo_multiagente_loop_reprovacao(tmp_path):
    """Avaliador reprova → especialistas rodam de novo → aprova na 2ª."""
    modelo = ModeloFake()
    modelo.configurar([
        AIMessage(content="estrutura v1"),
        AIMessage(content="implementacao v1"),
        AIMessage(content="testes v1"),
        AIMessage(content="ARTEFATO V1"),
        AIMessage(content=json.dumps({
            "status": "reprovado", "nota": 2, "confianca": 0.4,
            "feedback": "faltam testes de limite", "criterios_checados": [],
        })),
        AIMessage(content="estrutura v2 com correções"),
        AIMessage(content="implementacao v2"),
        AIMessage(content="testes v2 de limite"),
        AIMessage(content="ARTEFATO V2"),
        AIMessage(content=json.dumps({
            "status": "aprovado", "nota": 4.5, "confianca": 0.8,
            "feedback": "melhorou", "criterios_checados": [],
        })),
    ])
    cfg = _cfg(tmp_path)
    cfg.max_tentativas_correcao = 3
    app, cfg = _app(tmp_path, modelo, cfg)
    resultado = _invocar(app, cfg, "implemente um app de tarefas em python")

    assert len(resultado["vereditos"]) == 2
    assert resultado["vereditos"][0]["status"] == "reprovado"
    assert resultado["vereditos"][1]["status"] == "aprovado"
    assert resultado["mensagens"][-1].content == "ARTEFATO V2"


def test_fluxo_legado_intocado_quando_sem_dominio(tmp_path):
    """Pergunta simples continua no fluxo de agente único (sem multiagente)."""
    modelo = ModeloFake()
    modelo.configurar([AIMessage(content="olá! como posso ajudar?")])
    app, cfg = _app(tmp_path, modelo)
    resultado = _invocar(app, cfg, "oi, tudo bem?")

    assert resultado.get("dominio") in ("", None)
    assert resultado["mensagens"][-1].content == "olá! como posso ajudar?"