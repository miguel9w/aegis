"""
Nós do grafo LangGraph do Aegis.

Implementação dos 4 nós funcionais (+ nó de memória de longo prazo):

  - no_agente               : cognitivo — injeta sistema, invoca LLM
  - no_ferramentas          : execução — ToolNode com logging e detecção de erro
  - no_reflexao_auto_correcao : resiliência — analisa erro e reformula chamada
  - no_compressao_contexto  : gestão de janela — resume histórico antigo
  - no_memoria              : extrai fatos duráveis para a Store
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langgraph.graph.message import RemoveMessage
from langgraph.prebuilt import ToolNode
from langgraph.store.base import BaseStore

from .config import Config
from .estado import EstadoAegis
from .llm import com_retry
from .memoria import namespace_perfil
from .prompts import extrair_memoria, reflexao_auto_correcao, resumir_historico, sistema

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

_LIMITE_RESULTADO = 8000  # truncamento de resultados no estado


def _eh_erro(mensagem: BaseMessage) -> bool:
    """True se a mensagem de ferramenta indica falha (prefixo de erro)."""
    conteudo = str(getattr(mensagem, "content", ""))
    return conteudo.startswith("Error:") or conteudo.startswith("ERRO_FERRAMENTA:")


def _truncar(texto: Any, limite: int = _LIMITE_RESULTADO) -> str:
    s = str(texto)
    if len(s) > limite:
        return s[:limite] + f"\n… (truncado, {len(s)} chars)"
    return s


def _extrair_erros(mensagens: list[BaseMessage]) -> list[str]:
    return [str(m.content) for m in mensagens if isinstance(m, ToolMessage) and _eh_erro(m)]


def _trecho_para_llm(mensagens: list[BaseMessage], limite: int = 4000) -> str:
    linhas = []
    total = 0
    for m in reversed(mensagens):
        bloco = f"[{type(m).__name__}] {_truncar(m.content, 800)}"
        if total + len(bloco) > limite:
            break
        linhas.append(bloco)
        total += len(bloco)
    return "\n".join(reversed(linhas))


def _parsear_json_fatos(texto: str) -> dict[str, Any]:
    """Faz parse tolerante do JSON de fatos retornado pelo LLM."""
    import re

    texto = texto.strip()
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if not m:
        return {}
    try:
        dados = json.loads(m.group(0))
        return dados.get("fatos", {}) if isinstance(dados, dict) else {}
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------
# Fábrica de nós (recebe LLM, ferramentas, store e config por closure)
# ---------------------------------------------------------------------

def fabricar_nos(llm, ferramentas: list[BaseTool], store: BaseStore | None,
                 cfg: Config, prompt_fn: Callable[..., str] | None = None) -> dict[str, Any]:
    """Cria todos os nós do grafo com o contexto injetado.

    `prompt_fn` (opcional) substitui o prompt de sistema padrão — usado pelos
    subagentes especialistas (pesquisador, redator) que têm persona própria.
    Assinatura: ``prompt_fn(perfil, resumo, ferramentas, metadados) -> str``.
    """

    llm_com_ferramentas = llm.bind_tools(ferramentas)
    executor = ToolNode(ferramentas, messages_key="mensagens")

    # ---- 1. Cognitivo -------------------------------------------------
    def no_agente(state: EstadoAegis) -> dict:
        perfil = None
        if store is not None:
            try:
                item = store.get(namespace_perfil(), "perfil")
                perfil = item.value if item else None
            except Exception:  # noqa: BLE001 — perfil é otimização, nunca bloqueia
                perfil = None

        resumo = state.get("contexto_comprimido") or ""
        if prompt_fn is not None:
            texto_sistema = prompt_fn(
                perfil, resumo, ferramentas, state.get("metadados_sessao")
            )
        else:
            texto_sistema = sistema(perfil, resumo, ferramentas, state.get("metadados_sessao"))
        system = SystemMessage(texto_sistema)
        mensagens = [system, *state["mensagens"]]
        # tag "resposta" → a TUI filtra apenas os tokens desta chamada no streaming
        resposta = com_retry(
            lambda: llm_com_ferramentas.with_config(tags=["resposta"]).invoke(mensagens)
        )
        return {"mensagens": [resposta], "perfil_usuario": perfil or {}}

    # ---- 2. Execução ---------------------------------------------------
    def no_ferramentas(state: EstadoAegis) -> dict:
        saida = executor.invoke(state)

        # Localiza as chamadas pendentes (AIMessage imediatamente anterior)
        chamadas: dict[str, dict] = {}
        for m in reversed(state["mensagens"]):
            if isinstance(m, AIMessage):
                chamadas = {tc["id"]: tc for tc in m.tool_calls}
                break

        registros: list[dict] = []
        for m in saida.get("mensagens", saida.get("messages", [])):
            if isinstance(m, ToolMessage):
                chamada = chamadas.get(m.tool_call_id, {})
                registros.append({
                    "nome": chamada.get("name", "?"),
                    "args": chamada.get("args", {}),
                    "resultado": _truncar(m.content),
                    "erro": _eh_erro(m),
                    "ts": time.strftime("%H:%M:%S"),
                })

        erros = _extrair_erros(saida.get("mensagens", saida.get("messages", [])))
        return {
            "mensagens": saida.get("mensagens", saida.get("messages", [])),
            "registros_ferramentas": registros,
            "erros_ferramenta": erros,
        }

    # ---- 3. Reflexão / auto-correção -----------------------------------
    def no_reflexao_auto_correcao(state: EstadoAegis) -> dict:
        tentativas = (state.get("tentativas_correcao") or 0) + 1
        erros = state.get("erros_ferramenta") or []
        trecho_erros = "\n".join(_truncar(e, 1500) for e in erros[-3:])

        mensagens = [
            SystemMessage(reflexao_auto_correcao()),
            *state["mensagens"],
            SystemMessage(f"ERROS DA ÚLTIMA EXECUÇÃO:\n{trecho_erros}"),
        ]
        resposta = com_retry(lambda: llm_com_ferramentas.invoke(mensagens))
        return {"mensagens": [resposta], "tentativas_correcao": tentativas}

    # ---- 4. Compressão de contexto --------------------------------------
    def _resumir(mensagens_antigas: list[BaseMessage], resumo_anterior: str) -> str:
        trecho = _trecho_para_llm(mensagens_antigas, limite=6000)
        try:
            resp = com_retry(lambda: llm.invoke([
                SystemMessage(resumir_historico()),
                HumanMessage(
                    f"Resumo anterior:\n{resumo_anterior or '(nenhum)'}\n\n"
                    f"Novo trecho a resumir:\n{trecho}"
                ),
            ]))
            return _truncar(resp.content, 4000)
        except Exception as exc:  # noqa: BLE001 — nunca deixa a conversa quebrar
            return (
                f"[compressão de emergência — resumo LLM indisponível: {exc}]\n"
                f"Resumo anterior preservado: {resumo_anterior or '(nenhum)'}"
            )

    def no_compressao_contexto(state: EstadoAegis) -> dict:
        mensagens = state["mensagens"]
        manter = max(2, cfg.manter_apos_compressao)
        if len(mensagens) <= manter:
            return {"contexto_comprimido": state.get("contexto_comprimido", "")}

        antigas = mensagens[:-manter]
        recentes = mensagens[-manter:]
        resumo_anterior = state.get("contexto_comprimido", "")
        novo_resumo = _resumir(antigas, resumo_anterior)

        # RemoveMessage é o único jeito seguro de PODAR histórico com add_messages
        remocoes = [RemoveMessage(id=m.id) for m in antigas if getattr(m, "id", None)]
        return {
            "mensagens": remocoes,
            "contexto_comprimido": novo_resumo,
        }

    # ---- 5. Memória de longo prazo ---------------------------------------
    def no_memoria(state: EstadoAegis) -> dict:
        if not cfg.memoria_ativa or store is None:
            return {}
        mensagens = state.get("mensagens") or []
        if len(mensagens) < 4:  # exige mínimo de troca para extrair fatos
            return {}
        try:
            trecho = _trecho_para_llm(mensagens[-8:])
            resp = com_retry(lambda: llm.invoke([
                SystemMessage(extrair_memoria()),
                HumanMessage(f"Diálogo:\n{trecho}"),
            ]))
            fatos = _parsear_json_fatos(resp.content)
            if fatos:
                ns = namespace_perfil()
                item = store.get(ns, "perfil")
                dados = dict(item.value) if item and item.value else {}
                dados.update(fatos)
                store.put(ns, "perfil", dados)
        except Exception:  # noqa: BLE001 — memória falha sem derrubar o fluxo
            if cfg.dev:
                import traceback
                traceback.print_exc()
        return {}

    return {
        "no_agente": no_agente,
        "no_ferramentas": no_ferramentas,
        "no_reflexao_auto_correcao": no_reflexao_auto_correcao,
        "no_compressao_contexto": no_compressao_contexto,
        "no_memoria": no_memoria,
    }