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

from langchain_core.callbacks import BaseCallbackHandler
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
from .memoria import namespace_licoes, namespace_perfil
from .prompts import extrair_memoria, reflexao_auto_correcao, reflexao_pos_turno, resumir_historico, sistema

# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

class _CapturaRaciocinio(BaseCallbackHandler):
    """Coleta o `reasoning_content` dos chunks do stream (DeepSeek/Zen).

    O DeepSeek em modo thinking EMITE o raciocínio nos chunks, mas o
    agregador do langchain DESCARTÁ-O ao montar a AIMessage final. Quando há
    tool_calls, o provider exige o campo de volta no passo seguinte —
    sem ele, HTTP 400 ("reasoning_content must be passed back to the API").
    O `no_agente` injeta o texto coletado nos additional_kwargs da mensagem.

    O gancho é o `on_llm_new_token`: o langchain-openai chama-o por chunk
    passando o ChatGenerationChunk em `chunk=` — o reasoning vem em
    `chunk.message.additional_kwargs` e é re-coletado daqui (o agregador da
    AIMessage final o perde).
    """

    def __init__(self, caixa: dict[str, str]) -> None:
        self.caixa = caixa

    def on_llm_new_token(self, token: str, *, chunk: Any = None, **kwargs: Any) -> None:
        if chunk is None:
            return
        msg = getattr(chunk, "message", None)
        if msg is None:
            return
        razao = (getattr(msg, "additional_kwargs", None) or {}).get("reasoning_content")
        if isinstance(razao, str) and razao:
            self.caixa["texto"] += razao

from .config_json import carregar_config_json as _cfg_json

_LIMITES = _cfg_json("limites.json", {
    "limite_resultado": 8000,
    "limite_trecho_llm": 4000,
})
_LIMITE_RESULTADO = int(_LIMITES["limite_resultado"])   # truncamento de resultados no estado
_LIMITE_TRECHO_LLM = int(_LIMITES["limite_trecho_llm"])  # trecho re-injetado ao LLM


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


def _trecho_para_llm(mensagens: list[BaseMessage], limite: int | None = None) -> str:
    limite = limite or _LIMITE_TRECHO_LLM
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


def _parsear_licoes(texto: str) -> list[tuple[str, str]]:
    """Parse tolerante do JSON de lições: [(texto, prioridade)] (máx. 3)."""
    import re

    texto = texto.strip()
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if not m:
        return []
    try:
        dados = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    licoes = dados.get("licoes", []) if isinstance(dados, dict) else []
    saida: list[tuple[str, str]] = []
    for item in licoes[:3]:
        if isinstance(item, str) and item.strip():
            saida.append((item.strip(), "media"))
        elif isinstance(item, dict):
            texto_licao = str(item.get("texto", "")).strip()
            if texto_licao:
                pr = str(item.get("prioridade", "media")).lower()
                if pr not in ("alta", "media", "baixa"):
                    pr = "media"
                saida.append((texto_licao, pr))
    return saida


def _prioridade_por_repeticao(registros: list[dict]) -> bool:
    """True se a MESMA ferramenta falhou ≥2× com o mesmo erro no turno.

    Repetição de falha é o sinal mais forte de lição durável — eleva a
    prioridade independente do que a reflexão LLM sugerir.
    """
    contagem: dict[str, int] = {}
    for r in registros:
        if r.get("erro"):
            chave = f"{r.get('nome')}|{str(r.get('resultado'))[:60]}"
            contagem[chave] = contagem.get(chave, 0) + 1
    return any(n >= 2 for n in contagem.values())


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

        # Lições aprendidas relevantes à pergunta (C1 — memória procedimental).
        # Recall barato (IDF, sem LLM); só injeta quando há conteúdo relevante,
        # mantendo o system byte-idêntico nos demais casos.
        if store is not None:
            try:
                from .recuperacao import recuperar_licoes
                consulta = " ".join(
                    str(getattr(m, "content", ""))[:200]
                    for m in state["mensagens"][-3:]
                )
                bloco_licoes = recuperar_licoes(store, consulta)
                if bloco_licoes:
                    texto_sistema = texto_sistema + "\n\n" + bloco_licoes
            except Exception:  # noqa: BLE001 — recall é otimização, nunca bloqueia
                pass

        system = SystemMessage(texto_sistema)
        mensagens = [system, *state["mensagens"]]
        # tag "resposta" → a TUI filtra apenas os tokens desta chamada no streaming
        # callbacks: captura o reasoning_content dos chunks — o provider
        # DeepSeek/Zen exige devolvê-lo quando há tool_calls (senão HTTP 400)
        caixa_raciocinio: dict[str, str] = {"texto": ""}

        def invocar() -> Any:
            caixa_raciocinio["texto"] = ""  # retry = tentativa limpa
            return llm_com_ferramentas.with_config(
                tags=["resposta"], callbacks=[_CapturaRaciocinio(caixa_raciocinio)]
            ).invoke(mensagens)

        resposta = com_retry(invocar)
        razao = caixa_raciocinio["texto"]
        if razao and getattr(resposta, "tool_calls", None):
            resposta.additional_kwargs = {
                **resposta.additional_kwargs, "reasoning_content": razao,
            }
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
        trecho = _trecho_para_llm(mensagens_antigas, limite=_LIMITE_TRECHO_LLM + 2000)
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

        # Re-injeção das tarefas ativas após a compressão (paridade Hermes todo_tool)
        try:
            from .tarefas import resumo_ativo_para_reinjecao
            tarefas_ativas = resumo_ativo_para_reinjecao()
            if tarefas_ativas:
                novo_resumo = novo_resumo.rstrip() + "\n\n" + tarefas_ativas
        except Exception:  # noqa: BLE001 — nunca deixa a compressão quebrar
            pass

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

    # ---- 6. Reflexão pós-turno (C1) ---------------------------------------
    def no_reflexao_pos_turno(state: EstadoAegis) -> dict:
        """Extrai lições duráveis da trajetória do turno e grava na Store.

        Roda no fim do grafo (após no_memoria), só quando o turno usou
        ferramentas. Sem ferramentas → zero custo, nada gravado. Erro repetido
        (mesma ferramenta + mesmo erro ≥2×) eleva a prioridade da lição.
        """
        if not cfg.memoria_ativa or store is None:
            return {"licoes_turno": []}
        registros = state.get("registros_ferramentas") or []
        if not registros:
            return {"licoes_turno": []}
        try:
            trajetoria = "\n".join(
                f"- {r.get('nome')}: {_truncar(r.get('resultado', ''), 300)}"
                for r in registros[-8:]
            )
            resp = com_retry(lambda: llm.invoke([
                SystemMessage(reflexao_pos_turno()),
                HumanMessage(f"Trajetória do turno:\n{trajetoria}"),
            ]))
            licoes = _parsear_licoes(resp.content)
            repetiu_erro = _prioridade_por_repeticao(registros)
            gravadas: list[str] = []
            ns = namespace_licoes()
            for texto, prioridade in licoes:
                if repetiu_erro or prioridade == "alta":
                    prioridade_efetiva = "alta"
                else:
                    prioridade_efetiva = prioridade
                store.put(
                    ns,
                    f"licao_{int(time.time_ns())}_{len(gravadas)}",
                    {
                        "texto": texto,
                        "prioridade": prioridade_efetiva,
                        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
                    },
                )
                gravadas.append(texto)
            return {"licoes_turno": gravadas}
        except Exception:  # noqa: BLE001 — reflexão falha sem derrubar o fluxo
            if cfg.dev:
                import traceback
                traceback.print_exc()
            return {"licoes_turno": []}

    return {
        "no_agente": no_agente,
        "no_ferramentas": no_ferramentas,
        "no_reflexao_auto_correcao": no_reflexao_auto_correcao,
        "no_compressao_contexto": no_compressao_contexto,
        "no_memoria": no_memoria,
        "no_reflexao_pos_turno": no_reflexao_pos_turno,
    }