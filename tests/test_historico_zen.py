"""Sanitização do histórico p/ provider zen (regressão 2026-08).

O zen (free) rejeita com HTTP 400 ("Messages with role 'tool' must be a
response to a preceding message with 'tool_calls'") requests cujo histórico
contenha tool_calls de turnos ANTERIORES. `_sanitizar_historico` remove os
tool_calls já resolvidos do payload enviado ao LLM (e as ToolMessages órfãs),
preservando o bloco ativo do turno e o estado original (auditoria).
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from pydantic import PrivateAttr

from aegis.nos import _sanitizar_historico


def _ai_com_tools(id_chamada: str = "call_1", conteudo: str = "") -> AIMessage:
    return AIMessage(
        content=conteudo,
        tool_calls=[{"name": "calculadora", "args": {"expressao": "2+2"}, "id": id_chamada, "type": "tool_call"}],
        additional_kwargs={"tool_calls": [{"id": id_chamada, "type": "function", "function": {"name": "calculadora", "arguments": "{}"}}]},
    )


# ---------------------------------------------------------------------
# Unit — sanitização
# ---------------------------------------------------------------------

def test_historico_resolvido_e_sanitizado():
    """tool_calls resolvidos saem do payload; ToolMessages órfãs dropadas."""
    historico = [
        HumanMessage(content="pergunta"),
        _ai_com_tools("call_1"),
        ToolMessage(content="4", tool_call_id="call_1"),
        AIMessage(content="resposta final"),
    ]
    limpo = _sanitizar_historico(historico)
    assert len(limpo) == 3  # ToolMessage dropada
    assert all(getattr(m, "type", "") != "tool" for m in limpo)
    for m in limpo:
        if getattr(m, "type", "") == "ai":
            assert not m.tool_calls  # tool_calls zerados
            assert not m.additional_kwargs.get("tool_calls")
    assert limpo[-1].content == "resposta final"  # resposta intacta


def test_bloco_ativo_terminando_em_tool_intacto():
    """Fluxo de ferramentas em andamento (fim em ToolMessage) NÃO é tocado."""
    historico = [
        HumanMessage(content="pergunta"),
        _ai_com_tools("call_1"),
        ToolMessage(content="4", tool_call_id="call_1"),
    ]
    limpo = _sanitizar_historico(historico)
    assert limpo == historico
    assert limpo[1].tool_calls  # bloco ativo preservado


def test_bloco_ativo_terminando_em_ai_com_tools_intacto():
    historico = [HumanMessage(content="pergunta"), _ai_com_tools("call_1")]
    limpo = _sanitizar_historico(historico)
    assert limpo == historico
    assert limpo[1].tool_calls


def test_turno_novo_com_tools_resolvidas_no_meio():
    """O caso do bug real: novo turno, mas o histórico carrega tools antigas
    (AI com tool_calls + ToolMessage + resposta final) — tudo é sanitizado."""
    historico = [
        HumanMessage(content="pergunta 1"),
        _ai_com_tools("call_1"),
        ToolMessage(content="4", tool_call_id="call_1"),
        AIMessage(content="resposta 1"),
        HumanMessage(content="pergunta 2"),
    ]
    limpo = _sanitizar_historico(historico)
    assert not any(getattr(m, "type", "") == "tool" for m in limpo)
    for m in limpo:
        if getattr(m, "type", "") == "ai":
            assert not m.tool_calls
    assert limpo[-1].content == "pergunta 2"


def test_sem_tool_calls_retorna_igual():
    historico = [HumanMessage(content="oi"), AIMessage(content="olá")]
    assert _sanitizar_historico(historico) == historico


def test_vazio():
    assert _sanitizar_historico([]) == []


# ---------------------------------------------------------------------
# Integração — dois turnos na mesma thread (o cenário que falhava)
# ---------------------------------------------------------------------

def test_segundo_turno_mesma_thread_historico_limpo(monkeypatch, tmp_path):
    import conftest
    from conftest import ModeloFake, chamada_tool
    from aegis.config import config as cfg
    from aegis.ferramentas import recarregar_tudo
    from aegis.grafo import montar_grafo
    from aegis.memoria import criar_checkpointer_sync, criar_store_sync

    class ModeloGravador(ModeloFake):
        _recebidas: list = PrivateAttr(default_factory=list)

        @property
        def recebidas(self) -> list:
            return self._recebidas

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self._recebidas.append(list(messages))
            return super()._generate(messages, stop=stop, run_manager=run_manager, **kwargs)

    monkeypatch.setattr(cfg, "artefatos_dir", tmp_path)
    monkeypatch.setattr(cfg, "multiagente_ativos", False)
    monkeypatch.setattr(cfg, "banco", str(tmp_path / "agente.db"))  # isolado (X3)

    gravador = ModeloGravador()
    ferramentas = recarregar_tudo()
    checkpointer = criar_checkpointer_sync(cfg.banco)
    store = criar_store_sync(str(tmp_path / "store.db"))
    grafo = montar_grafo(gravador, ferramentas, checkpointer=checkpointer, store=store, cfg=cfg)
    thread = {"configurable": {"thread_id": "zen-2-turnos"}}

    # Turno 1: agente chama calculadora e responde (deixa tool_calls no estado)
    gravador.configurar([
        chamada_tool("calculadora", {"expressao": "2+2"}, "call_1"),
        AIMessage(content="O resultado é 4."),
    ])
    final_1 = grafo.invoke({"mensagens": [HumanMessage(content="quanto é 2+2?")]}, config=thread)
    assert final_1["mensagens"][-1].content == "O resultado é 4."

    # Turno 2: mesma thread — o payload enviado ao LLM deve estar limpo
    gravador.configurar([AIMessage(content="Segunda resposta.")])
    final_2 = grafo.invoke({"mensagens": [HumanMessage(content="segunda pergunta")]}, config=thread)

    envio_turno_2 = next(
        msgs for msgs in gravador.recebidas
        if any(getattr(m, "type", "") == "human" and "segunda pergunta" in m.content for m in msgs)
    )
    assert not any(getattr(m, "type", "") == "tool" for m in envio_turno_2)
    assert not any(getattr(m, "type", "") == "ai" and m.tool_calls for m in envio_turno_2)

    # Auditoria preservada: o ESTADO mantém a AIMessage com tool_calls do turno 1
    assert any(getattr(m, "type", "") == "ai" and m.tool_calls for m in final_2["mensagens"])
    assert final_2["mensagens"][-1].content == "Segunda resposta."
