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
from .prompts import (
    extrair_memoria,
    planejar_tarefa,
    reflexao_auto_correcao,
    reflexao_pos_turno,
    replanejar_tarefa,
    resumir_historico,
    sistema,
)

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
# C2 — Plan-and-execute
# ---------------------------------------------------------------------

_VERBOS_ENTREGA = (
    "crie", "criar", "implemente", "implementar", "configure", "configurar",
    "refatore", "refatorar", "gerencie", "gerenciar", "construa", "construir",
    "monte", "montar", "organize", "organizar", "escreva", "escrever",
    "gere", "gerar", "instale", "instalar", "deploy", "publique", "publicar",
    "analise", "analisar", "liste", "listar", "sugira", "sugerir", "avalie",
    "avaliar", "verifique", "verificar", "corrija", "corrigir", "arrume",
)

_MARCADORES_MULTI = (
    "e depois", "em seguida", "por fim", "primeiro", "segundo", "terceiro",
    "então", "também", "além de", "depois de", "antes de", ";", "1)", "2)",
    "3)", "passo 1", "passo 2", "na sequência",
)


def _precisa_plano(pergunta: str) -> bool:
    """Heurística barata (zero LLM) de complexidade da tarefa.

    Ativa planejamento quando a pergunta pede uma ENTREGA multi-passo:
    comprimento ≥ 120 chars, múltiplos marcadores de sequência ou verbo de
    entrega + contexto de repo. Perguntas simples/curtas ficam no fluxo
    legado (byte-idêntico).
    """
    if not pergunta:
        return False
    texto = pergunta.strip().lower()
    if len(texto) >= 120:
        return True
    marcadores = sum(1 for m in _MARCADORES_MULTI if m in texto)
    if marcadores >= 2:
        return True
    verbo_entrega = any(v in texto for v in _VERBOS_ENTREGA)
    if verbo_entrega and ("repo" in texto or "projeto" in texto or "arquivo" in texto
                          or "código" in texto or "codigo" in texto or "teste" in texto
                          or "ferramenta" in texto):
        return True
    return False


def _parsear_plano(texto: str) -> list[dict[str, str]]:
    """Parse tolerante do JSON do plano: lista de {passo, objetivo} (máx. 6)."""
    import re

    texto = texto.strip()
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if not m:
        return []
    try:
        dados = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    passos = dados.get("plano", []) if isinstance(dados, dict) else dados
    if not isinstance(passos, list):
        return []
    plano: list[dict[str, str]] = []
    for item in passos[:6]:
        if isinstance(item, str) and item.strip():
            plano.append({"passo": item.strip(), "objetivo": "", "status": "pendente"})
        elif isinstance(item, dict):
            passo = str(item.get("passo", "")).strip() or str(item.get("objetivo", "")).strip()
            if passo:
                plano.append({
                    "passo": passo,
                    "objetivo": str(item.get("objetivo", "")).strip(),
                    "status": "pendente",
                })
    return plano


def _bloco_plano(plano: list[dict]) -> str:
    """Renderiza o plano ativo com progresso para injeção no system."""
    linhas = []
    for i, p in enumerate(plano, 1):
        status = p.get("status", "pendente")
        marcador = {"concluido": "✔", "falhou": "✘", "executando": "▶"}.get(status, "·")
        objetivo = f" — {p['objetivo']}" if p.get("objetivo") else ""
        linhas.append(f"{marcador} {i}. {p['passo']}{objetivo} [{status}]")
    return "## Plano ativo (progrida passo a passo; atualize o plano se uma etapa falhar)\n" + "\n".join(linhas)


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

        # Plano ativo (C2) — guia o modelo passo a passo, com progresso
        if state.get("plano"):
            texto_sistema = texto_sistema + "\n\n" + _bloco_plano(state["plano"])

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

    # ---- 6. Plan-and-execute (C2) ---------------------------------------
    def no_planejamento(state: EstadoAegis) -> dict:
        """Planeja tarefas complexas: gera plano ordenado via LLM.

        A heurística `_precisa_plano` decide SEM chamar a LLM (custo zero
        para perguntas simples — fluxo legado byte-idêntico). O plano fica no
        estado e é injetado no system do agente como guia passo a passo.
        """
        if state.get("plano_considerado"):
            return {}
        ultima = next(
            (m for m in reversed(state.get("mensagens") or [])
             if getattr(m, "type", "") == "human"),
            None,
        )
        pergunta = str(getattr(ultima, "content", "") or "")[:500]
        if not _precisa_plano(pergunta):
            return {"plano_considerado": True}
        try:
            resp = com_retry(lambda: llm.invoke([
                SystemMessage(planejar_tarefa()),
                HumanMessage(f"Tarefa do usuário:\n{pergunta}"),
            ]))
            plano = _parsear_plano(resp.content)
            saida: dict = {"plano_considerado": True}
            if plano:
                saida["plano"] = plano
            return saida
        except Exception:  # noqa: BLE001 — planejamento falha sem derrubar o fluxo
            if cfg.dev:
                import traceback
                traceback.print_exc()
            return {"plano_considerado": True}

    def no_replanejamento(state: EstadoAegis) -> dict:
        """Reajusta o plano após falha de etapa (erro de ferramenta).

        Marca o passo mais antigo 'pendente' como FALHOU e pede à LLM uma
        reformulação do restante (atalho/abordagem alternativa). Mantém o que
        já foi concluído fora do plano.
        """
        plano = list(state.get("plano") or [])
        if not plano:
            return {}
        erros = state.get("erros_ferramenta") or []
        contexto = "\n".join(str(e) for e in erros[-2:]) or "erro de ferramenta"
        # marca o primeiro passo pendente/executando como falho
        for p in plano:
            if p.get("status") in ("pendente", "executando"):
                p["status"] = "falhou"
                break
        try:
            resp = com_retry(lambda: llm.invoke([
                SystemMessage(replanejar_tarefa()),
                HumanMessage(
                    f"Erro ocorrido:\n{contexto}\n\n"
                    f"Plano atual:\n{_bloco_plano(plano)}"
                ),
            ]))
            novos = _parsear_plano(resp.content)
            if novos:
                return {"plano": novos}
            return {"plano": plano}
        except Exception:  # noqa: BLE001 — replan falha e mantém o plano marcado
            if cfg.dev:
                import traceback
                traceback.print_exc()
            return {"plano": plano}

    # ---- 7. Reflexão pós-turno (C1) ---------------------------------------
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
        "no_planejamento": no_planejamento,
        "no_replanejamento": no_replanejamento,
    }