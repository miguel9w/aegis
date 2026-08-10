"""Testes da ponte web — frames do protocolo via executar_job (sem subprocesso)."""

import asyncio
import io
import json
import sys

from langchain_core.messages import AIMessage

from aegis.config import config
from aegis.ferramentas.sistema import escrever_arquivo
from aegis.grafo import montar_grafo
from aegis.webui_bridge import (
    _redigir, executar_job, listar_historico, main, processar_comando,
    snapshot_estado,
)

from conftest import ModeloFake, basico_tools, chamada_tool


def _coletar(app, texto, thread_id="teste-1", cfg=None, dominio=""):
    return asyncio.run(_coletar_async(app, texto, thread_id, cfg, dominio))


async def _coletar_async(app, texto, thread_id, cfg, dominio):
    return [f async for f in executar_job(app, texto, thread_id, "j-1", cfg, dominio)]


def _grafo_simples(m, ferramentas=None, cfg=None):
    from aegis.config import Config
    c = cfg or Config()
    c.multiagente_ativos = False
    return montar_grafo(m, ferramentas or [], cfg=c)


# ------------------------------------------------------------ contrato (sintéticos)

from types import SimpleNamespace as NS


def test_processar_evento_contrato_completo():
    """O contrato evento v2 → frames (o runtime 1.x não streama invoke de
    modelos customizados — mesmo motivo dos testes da TUI com produtor
    injetável; estes eventos sintéticos CÓPIAM o formato real do v2)."""
    from aegis.webui_bridge import _estado_job, _processar_evento
    est = _estado_job()

    # token + reasoning no mesmo chunk (com cumulativo)
    chunk = NS(content="olá", additional_kwargs={"reasoning_content": "pensando"})
    frames = _processar_evento({"event": "on_chat_model_stream", "tags": ["resposta"],
                                "data": {"chunk": chunk}}, est)
    assert frames[0] == {"kind": "token", "texto": "olá", "cumulativo": "olá"}
    assert frames[1]["kind"] == "reasoning" and frames[1]["cumulativo"] == "pensando"

    # chunk sem reasoning (só token)
    est2 = _estado_job()
    chunk2 = NS(content="continua", additional_kwargs={})
    frames2 = _processar_evento({"event": "on_chat_model_stream", "tags": ["resposta"],
                                 "data": {"chunk": chunk2}}, est2)
    assert [f["kind"] for f in frames2] == ["token"]
    # sem a tag resposta (LLM do multiagente) → nada
    frames3 = _processar_evento({"event": "on_chat_model_stream", "tags": ["seq:step:1"],
                                 "data": {"chunk": chunk2}}, _estado_job())
    assert frames3 == []

    # tool: inicio guarda args, fim deriva arquivo
    est4 = _estado_job()
    _processar_evento({"event": "on_tool_start", "name": "escrever_arquivo",
                       "run_id": "r1", "data": {"input": {"caminho": "/tmp/x.txt",
                                                          "conteudo": "oi"}}}, est4)
    fim = _processar_evento({"event": "on_tool_end", "run_id": "r1",
                             "data": {"output": NS(content="ok — 2 caracteres\n+oi",
                                                   name="escrever_arquivo")}}, est4)
    kinds_fim = [f["kind"] for f in fim]
    assert "tool_fim" in kinds_fim and "arquivo" in kinds_fim
    arquivo = next(f for f in fim if f["kind"] == "arquivo")
    assert arquivo["acao"] == "escrever" and arquivo["caminho"] == "/tmp/x.txt"
    assert arquivo["status"] == "ok"

    # comando derivado com duração e confirmado
    est5 = _estado_job()
    _processar_evento({"event": "on_tool_start", "name": "executar_comando",
                       "run_id": "r2", "data": {"input": {"comando": "git status",
                                                          "confirmar": True}}}, est5)
    fim5 = _processar_evento({"event": "on_tool_end", "run_id": "r2",
                              "data": {"output": NS(content="ok — duração=150ms\nbranch master",
                                                    name="executar_comando")}}, est5)
    cmd = next(f for f in fim5 if f["kind"] == "comando")
    assert cmd["cmd"] == "git status" and cmd["confirmado"] is True
    assert cmd["status"] == "ok" and cmd["duracao_ms"] == 150

    # recusado (denylist) → status próprio
    est6 = _estado_job()
    _processar_evento({"event": "on_tool_start", "name": "executar_comando",
                       "run_id": "r3", "data": {"input": {"comando": "rm -rf /"}}}, est6)
    fim6 = _processar_evento({"event": "on_tool_end", "run_id": "r3",
                              "data": {"output": NS(content="erro — comando recusado pela política de segurança",
                                                    name="executar_comando")}}, est6)
    cmd6 = next(f for f in fim6 if f["kind"] == "comando")
    assert cmd6["status"] == "recusado" and cmd6["confirmado"] is False

    # subgrafo (multiagente) start/end
    est7 = _estado_job()
    s_inicio = _processar_evento({"event": "on_chain_start", "name": "sub_programacao"}, est7)
    assert s_inicio == [{"kind": "subgrafo", "nome": "sub_programacao",
                         "evento": "start", "nivel": 1, "tipo": "multiagente"}]
    s_fim = _processar_evento({"event": "on_chain_end", "name": "sub_programacao",
                               "data": {"output": {"dominio": "programacao"}}}, est7)
    assert s_fim[0]["evento"] == "end"
    assert est7["ultimo_output"]["dominio"] == "programacao"  # chain_end guarda estado


# ---------------------------------------------------- multiagente (subgrafo+veredito)


def test_multiagente_subgrafos_e_vereditos():
    from aegis.config import Config
    cfg = Config()
    cfg.multiagente_ativos = True
    m = ModeloFake()
    m.configurar([
        AIMessage(content="r1 programacao"),
        AIMessage(content="r2 seguranca"),
        AIMessage(content="r3 performance"),
        AIMessage(content="r4 integrador"),
        AIMessage(content='{"status": "aprovado", "nota": 8.5, "feedback": "ok"}'),
    ])
    app = montar_grafo(m, [], cfg=cfg)
    frames = _coletar(app, "revise meu codigo", cfg=cfg)
    kinds = [f["kind"] for f in frames]
    assert "subgrafo" in kinds
    sub = next(f for f in frames if f["kind"] == "subgrafo")
    assert sub["nome"].startswith("sub_")
    assert sub["evento"] in ("start", "end") and sub["tipo"] == "multiagente"
    vereditos = [f for f in frames if f["kind"] == "veredito"]
    assert vereditos, "avaliador deveria produzir veredito"
    assert vereditos[-1]["veredito"]["status"] == "aprovado"
    fim = next(f for f in frames if f["kind"] == "fim")
    assert "orquestracao_final" in fim["estado_final"] or fim["texto"]


# ------------------------------------------------- ferramenta do sistema (arquivo)


def test_turno_simples_fim_e_metrica():
    """Integração: o fake gera (não streama) — tokens só via contrato (acima),
    mas fim/metríca/job_id/estado_final vêm do fluxo real."""
    m = ModeloFake()
    m.configurar([AIMessage(content="resposta do teste")])
    app = _grafo_simples(m)
    frames = _coletar(app, "oi")
    kinds = [f["kind"] for f in frames]
    assert "fim" in kinds and "metrica" in kinds
    fim = next(f for f in frames if f["kind"] == "fim")
    assert fim["job_id"] == "j-1"
    assert fim["estado_final"]  # redigido e truncado
    assert "mensagens" in fim["estado_final"]
    assert fim["texto"] == "resposta do teste"  # _texto_final cai na última AI
    metrica = next(f for f in frames if f["kind"] == "metrica")
    assert metrica["duracao_s"] >= 0


def test_tool_sistema_frame_arquivo(tmp_path, monkeypatch):
    from aegis.config import Config
    cfg = Config()
    cfg.multiagente_ativos = False
    monkeypatch.setattr(config, "artefatos_dir", tmp_path / "artefatos")
    m = ModeloFake()
    m.configurar([
        chamada_tool("escrever_arquivo", {"caminho": str(tmp_path / "artefatos" / "x.txt"),
                                          "conteudo": "oi"}, "call_f1"),
        AIMessage(content="arquivo escrito"),
    ])
    app = montar_grafo(m, [escrever_arquivo], cfg=cfg)
    frames = _coletar(app, "crie um arquivo", cfg=cfg)
    kinds = [f["kind"] for f in frames]
    assert "tool_inicio" in kinds and "tool_fim" in kinds
    arquivos = [f for f in frames if f["kind"] == "arquivo"]
    assert arquivos
    assert arquivos[0]["acao"] == "escrever"
    assert "+oi" in arquivos[0]["diff"] or arquivos[0]["status"] == "ok"
    comando = [f for f in frames if f["kind"] == "comando"]
    assert not comando  # nenhum comando neste turno


# ------------------------------------------------------- redação e snapshot


def test_redigir_nunca_vaza_chave():
    arv = {
        "config": {"api_key": "sk-123", "modelo": "deepseek"},
        "OPENAI_API_KEY": "sk-segredo",
        "mensagens": [{"content": "olá", "extra": "x" * 3000}],
    }
    saida = _redigir(arv)
    assert "sk-123" not in json.dumps(saida)
    assert "sk-segredo" not in json.dumps(saida)
    assert saida["config"]["api_key"] == "[REDACTED]"
    assert len(saida["mensagens"][0]["extra"]) == 2001  # truncado


def test_snapshot_sem_segredos():
    snap = json.dumps(snapshot_estado())
    for segredo in ("api_key", "OPENAI_API_KEY", "sk-"):
        assert segredo not in snap
    assert "modelo" in snap and "n_ferramentas" in snap


# ---------------------------------------------------------------- comandos


def test_processar_ping_e_desconhecido():
    assert '"pong"' in processar_comando({"cmd": "ping"})
    assert "desconhecido" in processar_comando({"cmd": "zzz"})


def test_processar_estado():
    saida = processar_comando({"cmd": "estado"})
    assert '"modelo"' in saida


def test_processar_sugestoes_catalogo_real():
    saida = json.loads(processar_comando({"cmd": "sugestoes"}))
    dados = saida["dados"]
    nomes = [c["nome"] for c in dados["comandos"]]
    assert "ajuda" in nomes and "prompt" in nomes and "definir_papel" in nomes
    agentes = [a["nome"] for a in dados["agentes"]]
    assert "programacao" in agentes and "escrita" in agentes
    assert isinstance(dados["papeis"], list) and isinstance(dados["prompts"], list)


def test_processar_slash_status():
    saida = json.loads(processar_comando({"cmd": "slash", "nome": "status"}))
    assert "Aegis" in saida["texto"]


def test_processar_slash_desconhecido_nao_derruba():
    saida = json.loads(processar_comando({"cmd": "slash", "nome": "nao_existe"}))
    assert "desconhecido" in saida["texto"]


def test_executar_job_dominio_explicito_dispara_subgrafo(tmp_path):
    """`dominio` na mensagem vira metadado → orquestrador roteia p/ subgrafo."""
    from aegis.config import Config
    from aegis.multiagente import classificar_dominio
    c = Config()
    c.banco = tmp_path / "multi.webui.db"
    c.thread_id = "t-multi"
    c.multiagente_ativos = True
    c.orquestracoes_path = tmp_path / "orquestracoes.jsonl"
    m = ModeloFake()
    app = montar_grafo(m, basico_tools(), cfg=c)
    # o texto NÃO tem gatilho de escrita (classificação por regras daria "")
    assert classificar_dominio("organize essas ideias") == ""
    frames = _coletar(app, "organize essas ideias", "t-multi", c, dominio="escrita")
    inicio = next(f for f in frames
                  if f["kind"] == "subgrafo" and f["evento"] == "start")
    assert inicio["nome"] == "sub_escrita"
    assert any(f["kind"] == "fim" for f in frames)


def test_historico_threads(tmp_path, monkeypatch):
    from aegis.config import Config
    from aegis.memoria import criar_checkpointer_async
    cfg = Config()
    cfg.multiagente_ativos = False
    cfg.banco = tmp_path / "db-teste.sqlite"
    monkeypatch.setattr(config, "banco", cfg.banco)
    m = ModeloFake()
    m.configurar([AIMessage(content="oi")])
    checkpointer = asyncio.run(criar_checkpointer_async(cfg.banco))
    app = montar_grafo(m, [], checkpointer=checkpointer, cfg=cfg)
    _coletar(app, "primeira conversa", thread_id="web-hist", cfg=cfg)
    threads = asyncio.run(listar_historico(app, limite=10))
    assert any(t["thread_id"] == "web-hist" for t in threads)


def test_linha_malformada_nao_derruba(monkeypatch, capsys):
    import aegis.webui_bridge as bridge
    monkeypatch.setattr(sys, "stdin", io.StringIO('{"cmd":"ping"}\nnao-eh-json\n'))
    monkeypatch.setattr(bridge, "montar_app", lambda: None)
    main()  # deve processar o ping e sobreviver à linha inválida
    capturado = capsys.readouterr().out
    linhas = [l for l in capturado.strip().splitlines() if l]
    assert any("pong" in l for l in linhas)
    assert any("inválida" in l for l in linhas)