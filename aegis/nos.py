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
from langgraph.types import interrupt

from .config import Config
from .estado import EstadoAegis
from .llm import com_retry
from .memoria import namespace_decisoes, namespace_licoes, namespace_perfil, namespace_resumos, namespace_uat
from .prompts import (
    extrair_memoria,
    planejar_tarefa,
    reflexao_auto_correcao,
    reflexao_pos_turno,
    replanejar_tarefa,
    resumir_historico,
    resumir_sessao,
    sistema,
    verificar_entrega,
    verificar_resposta,
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


# ---------------------------------------------------------------------
# G1 — Modo entrega (ciclo GSD): classificador zero-LLM
# ---------------------------------------------------------------------

_VERBOS_ENTREGA_G1 = (
    "adicione", "adicionar", "implemente", "implementar", "refatore", "refatorar",
    "corrija", "corrigir", "construa", "construir", "desenvolva", "desenvolver",
    "gerencie", "gerenciar", "configure", "configurar", "instale", "instalar",
    "crie", "criar", "escreva", "escrever", "gere", "gerar", "monte", "montar",
    "implementa", "adiciona",
)

_SINAIS_REPO_G1 = (
    "ferramenta", "tool", "arquivo", "codigo", "código", "funcao", "função",
    "classe", "modulo", "módulo", "script", "teste", "testes", "push", "commit",
    " pr ", "repo", "repositorio", "repositório", "api", "endpoint", "pacote",
    "dependencia", "dependência", "projeto",
)

_PREFIXOS_INFORMATIVOS = (
    "como ", "o que ", "o que é", "o que e", "quais ", "qual ", "por que ",
    "quando ", "onde ", "quem ", "explique", "me explique", "liste", "listar",
    "mostre", "descreva", "resuma", "diferenca", "diferença",
)


def _normalizar(texto: str) -> str:
    texto = texto.lower()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"),
                 ("ã", "a"), ("õ", "o"), ("ç", "c"), ("ê", "e"), ("ô", "o")):
        texto = texto.replace(a, b)
    return texto


def _eh_pedido_entrega(pergunta: str) -> bool:
    """Zero-LLM: pedido de ENTREGA (código/artefato/documento) vs. pergunta
    informativa. Verbo de entrega + sinal de repo; prefixos informativos
    ('como', 'explique'…) sempre ganham (pergunta ≠ ordem)."""
    if not pergunta:
        return False
    t = _normalizar(pergunta)
    if any(t.startswith(p) for p in _PREFIXOS_INFORMATIVOS):
        return False
    verbos = [v for v in _VERBOS_ENTREGA_G1 if v in t]
    if not verbos:
        return False
    sinais = sum(1 for s in _SINAIS_REPO_G1 if s in t)
    fortes = {"adicione", "adicionar", "adiciona", "implemente", "implementar",
              "implementa", "refatore", "refatorar", "corrija", "corrigir",
              "construa", "construir", "desenvolva", "desenvolver",
              "gerencie", "gerenciar"}
    return sinais >= 1 or any(v in fortes for v in verbos)


def _eh_ambiguo(pergunta: str) -> bool:
    """Zero-LLM: pedido de entrega sem especificação (detalhes de execução)
    → discuss deve perguntar antes de planejar."""
    t = _normalizar(pergunta)
    espec = (" com ", " que ", " para ", " usando ", " via ", " teste",
             " testes", " push", " commit", " pr ", " arquivo", " funcao",
             " classe", " modulo", " que faz", " pasta ", " em ")
    return not any(e in t for e in espec)


def _parsear_vereditos_entrega(texto: str, total: int) -> list[dict]:
    """Parse tolerante do JSON do verify goal-backward (G1): lista de
    {indice, verificado, evidencia} na ordem dos critérios."""
    import re

    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if not m:
        return []
    try:
        dados = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    lista = dados.get("criterios") if isinstance(dados, dict) else dados
    if not isinstance(lista, list):
        return []
    saida: list[dict] = []
    for item in lista:
        if isinstance(item, dict) and "indice" in item:
            saida.append({
                "indice": int(item.get("indice", 0)),
                "verificado": bool(item.get("verificado", False)),
                "evidencia": str(item.get("evidencia", "")),
            })
    return saida


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


def _parsear_verificacao(texto: str) -> dict | None:
    """Parse tolerante do JSON de verificação: {"veredito", "evidencias"}.

    Retorna None quando não há JSON válido — o fluxo trata como "sem veredito"
    (segue sem loop e sem evidência extra).
    """
    import re

    texto = texto.strip()
    m = re.search(r"\{.*\}", texto, re.DOTALL)
    if not m:
        return None
    try:
        dados = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(dados, dict):
        return None
    veredito = str(dados.get("veredito", "ok")).lower()
    if veredito not in ("ok", "divergencia"):
        veredito = "ok"
    evidencias = dados.get("evidencias", [])
    limpas: list[dict[str, Any]] = []
    for e in evidencias if isinstance(evidencias, list) else []:
        if isinstance(e, dict) and str(e.get("fonte", "")).strip():
            limpas.append({
                "fonte": str(e["fonte"]).strip()[:120],
                "conferida": bool(e.get("conferida", True)),
                "observacao": str(e.get("observacao", "")).strip()[:300],
            })
    return {"veredito": veredito, "evidencias": limpas}


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
    executor = ToolNode(ferramentas, messages_key="mensagens", handle_tool_errors=True)

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
        # Plano do ciclo GSD (G1) — injeta fase + critérios de aceite
        ft = state.get("fluxo_trabalho")
        if not state.get("plano") and ft and ft.get("plano"):
            texto_sistema = (
                texto_sistema
                + "\n\n## Entrega em andamento (GSD)\nFase: "
                + str(ft.get("fase", ""))
                + "\n"
                + _bloco_plano(list(ft.get("plano") or []))
            )
        if ft and ft.get("feedback"):
            texto_sistema = (
                texto_sistema + "\n\nFeedback da verificação anterior:\n" + str(ft["feedback"])
            )

        # Recall hierárquico (C4): perfil → lições → resumo → decisões.
        # Barato (IDF, sem LLM); só injeta quando há conteúdo, mantendo o
        # system byte-idêntico nos demais casos.
        if store is not None:
            try:
                from .recuperacao import (
                    definir_thread,
                    recuperar_contexto_para_system,
                )
                definir_thread(str((state.get("metadados_sessao") or {}).get("thread_id", "")))
                consulta = " ".join(
                    str(getattr(m, "content", ""))[:200]
                    for m in state["mensagens"][-3:]
                )
                bloco_contexto = recuperar_contexto_para_system(
                    store,
                    str((state.get("metadados_sessao") or {}).get("thread_id", "")),
                    consulta,
                    teto=cfg.teto_bloco_contexto,
                )
                if bloco_contexto:
                    texto_sistema = texto_sistema + "\n\n" + bloco_contexto
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
        saida_node: dict[str, Any] = {
            "mensagens": saida.get("mensagens", saida.get("messages", [])),
            "registros_ferramentas": registros,
            "erros_ferramenta": erros,
        }
        # G1: no execute, cada wave é auditada (fase no registro) e emite um
        # commit atômico simbólico na lista de auditoria (replayável).
        fluxo = state.get("fluxo_trabalho")
        if fluxo and fluxo.get("fase") == "execute" and registros:
            wave = len(state.get("commits_entrega") or []) + 1
            for r in registros:
                r["fase"] = "execute"
            saida_node["commits_entrega"] = [{
                "wave": wave,
                "ts": time.strftime("%H:%M:%S"),
                "resumo": f"wave {wave} — {registros[0].get('nome', 'ferramenta')}",
            }]
        return saida_node

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

    # ---- 7. Verify-then-answer (C3) ---------------------------------------
    def no_verificar(state: EstadoAegis) -> dict:
        """Conferir a resposta final contra as evidências da execução.

        Só atua quando o turno usou ferramentas E não corrigiu ainda (limite
        de 1 correção evita loop). Sem ferramentas ou modo estrito desligado →
        custo zero. A divergência volta ao agente para correção.
        """
        if not cfg.verificacao_estrita:
            return {}
        if not (state.get("registros_ferramentas") or []):
            return {}
        if (state.get("verificacoes_realizadas") or 0) >= 1:
            # verificação já ocorreu (e corrigiu se preciso) → zera o veredito
            # para a rota não reentrar no loop corrigir→verificar
            return {"verificacao_veredito": "ok"}
        try:
            ultima_resposta = next(
                (m for m in reversed(state.get("mensagens") or [])
                 if getattr(m, "type", "") == "ai"),
                None,
            )
            registros = state.get("registros_ferramentas") or []
            trajetoria = "\n".join(
                f"- {r.get('nome')}: {_truncar(r.get('resultado', ''), 300)}"
                for r in registros[-6:]
            )
            resp = com_retry(lambda: llm.invoke([
                SystemMessage(verificar_resposta()),
                HumanMessage(
                    f"Resposta final do agente:\n{str(getattr(ultima_resposta, 'content', '') or '')[:800]}\n\n"
                    f"Execução real (evidências):\n{trajetoria}"
                ),
            ]))
            v = _parsear_verificacao(resp.content)
            if v is None:
                return {}  # sem JSON → segue (sem evidência, sem loop)
            return {
                "evidencias": v["evidencias"],
                "verificacao_veredito": v["veredito"],
                "verificacoes_realizadas": (state.get("verificacoes_realizadas") or 0) + 1,
            }
        except Exception:  # noqa: BLE001 — verificação falha sem derrubar o fluxo
            if cfg.dev:
                import traceback
                traceback.print_exc()
            return {}

    # ---- 7b. Modo entrega (G1): discuss → plan → execute → verify → ship --
    def _ler_uat_store(projeto: str) -> list[dict]:
        """UAT persistido da Store (namespace por PROJETO, não thread)."""
        if store is None:
            return []
        try:
            itens = store.search(namespace_uat(projeto))
            if not itens:
                return []
            dados = json.loads(str((itens[-1].value or {}).get("json", "[]")))
            return dados if isinstance(dados, list) else []
        except Exception:  # noqa: BLE001 — UAT nunca derruba o fluxo
            return []

    def _gravar_uat_store(projeto: str, uat: list[dict]) -> None:
        if store is None:
            return
        try:
            store.put(namespace_uat(projeto), f"uat-{time.time():.0f}",
                      {"json": json.dumps(uat, ensure_ascii=False)})
        except Exception:  # noqa: BLE001
            pass

    def _ler_gaps_projeto(projeto: str) -> list[str]:
        """Critérios reprovados (gaps) do UAT — contexto do próximo ciclo."""
        return [u["criterio"] for u in _ler_uat_store(projeto)
                if u.get("resultado") == "reprovado"]

    def no_classificador_entrega(state: EstadoAegis) -> dict:
        """Zero-LLM: pedido de ENTREGA ativa o ciclo GSD; senão fluxo legado
        (com `fluxo_trabalho: None`, sem custo e sem tocar no system)."""
        if state.get("fluxo_trabalho"):  # ciclo já ativo (retomada de turno)
            return {}
        ultima = next(
            (m for m in reversed(state.get("mensagens") or [])
             if getattr(m, "type", "") == "human"),
            None,
        )
        pergunta = str(getattr(ultima, "content", "") or "")[:500]
        if _eh_pedido_entrega(pergunta):
            projeto = str((state.get("metadados_sessao") or {}).get("projeto", "default"))
            return {"fluxo_trabalho": {
                "fase": "discuss", "plano": [], "criterios": [], "ship": None,
                "feedback": "", "correcoes": 0, "pergunta": None,
                "gaps_anteriores": _ler_gaps_projeto(projeto),
            }}
        return {"fluxo_trabalho": None}

    def no_discuss(state: EstadoAegis) -> dict:
        """Fase discuss: pedido vago → PERGUNTA ao usuário (interrupt, janela
        da ponte); a resposta entra como anotação do plano. Especificado →
        segue o ciclo (custo zero)."""
        ft = dict(state.get("fluxo_trabalho") or {})
        if ft.get("fase") != "discuss":
            return {}
        ultima = next(
            (m for m in reversed(state.get("mensagens") or [])
             if getattr(m, "type", "") == "human"),
            None,
        )
        pergunta = str(getattr(ultima, "content", "") or "")
        if _eh_ambiguo(pergunta):
            resposta = interrupt(
                "Detalhe a entrega: o que exatamente deve ser feito, em qual "
                "arquivo/pasta e quais os critérios de aceite?"
            )
            ft["anotacoes"] = str(resposta or "")[:500]
            ft["fase"] = "plan"
            return {"fluxo_trabalho": ft}
        ft["fase"] = "plan"
        return {"fluxo_trabalho": ft}

    def no_plan_entrega(state: EstadoAegis) -> dict:
        """Fase plan: reusa o prompt de plano (C2); cada passo do plano vira
        um critério de aceite do verify goal-backward."""
        ft = dict(state.get("fluxo_trabalho") or {})
        if ft.get("fase") != "plan":
            return {}
        ultima = next(
            (m for m in reversed(state.get("mensagens") or [])
             if getattr(m, "type", "") == "human"),
            None,
        )
        pergunta = str(getattr(ultima, "content", "") or "")
        anotacoes = ft.get("anotacoes") or ""
        tarefa = pergunta + (f"\nDetalhes do usuário: {anotacoes}" if anotacoes else "")
        gaps = ft.get("gaps_anteriores") or []
        if gaps:
            tarefa += ("\nGaps pendentes do UAT anterior (corrigir junto):\n"
                       + "\n".join(f"- {g}" for g in gaps))
        try:
            resp = com_retry(lambda: llm.invoke([
                SystemMessage(planejar_tarefa()),
                HumanMessage(f"Tarefa do usuário:\n{tarefa}"),
            ]))
            plano = _parsear_plano(resp.content)
        except Exception:  # noqa: BLE001 — plano falha → critério único
            if cfg.dev:
                import traceback
                traceback.print_exc()
            plano = []
        if plano:
            ft["plano"] = plano
            ft["criterios"] = [
                {"texto": str(p.get("passo", ""))[:140], "verificado": False,
                 "evidencia": ""}
                for p in plano
            ]
        else:
            ft["criterios"] = [
                {"texto": tarefa[:140], "verificado": False, "evidencia": ""}
            ]
        ft["fase"] = "execute"
        return {"fluxo_trabalho": ft}

    def no_verify_entrega(state: EstadoAegis) -> dict:
        """Verify goal-backward (G1): cada critério conferido contra as
        evidências reais da execução. Reprovado → feedback + volta a execute
        (sem ship); limite de correções força ship com o que passou (anti-loop)."""
        ft = dict(state.get("fluxo_trabalho") or {})
        criterios = list(ft.get("criterios") or [])
        if not criterios:
            ft["fase"] = "ship"
            return {"fluxo_trabalho": ft}
        try:
            registros = state.get("registros_ferramentas") or []
            trajetoria = "\n".join(
                f"- {r.get('nome')}: {_truncar(r.get('resultado', ''), 250)}"
                for r in registros[-8:]
            ) or "(nenhuma evidência de execução)"
            ultima = next(
                (m for m in reversed(state.get("mensagens") or [])
                 if getattr(m, "type", "") == "ai"),
                None,
            )
            lista = json.dumps(
                [{"indice": i, "texto": c["texto"]} for i, c in enumerate(criterios)],
                ensure_ascii=False,
            )
            resp = com_retry(lambda: llm.invoke([
                SystemMessage(verificar_entrega()),
                HumanMessage(
                    f"Critérios de aceite:\n{lista}\n\n"
                    f"Resposta final:\n{str(getattr(ultima, 'content', '') or '')[:800]}\n\n"
                    f"Evidências reais:\n{trajetoria}"
                ),
            ]))
            vereditos = _parsear_vereditos_entrega(resp.content, len(criterios))
        except Exception:  # noqa: BLE001 — verificação falha → nada ship (fail-safe)
            if cfg.dev:
                import traceback
                traceback.print_exc()
            vereditos = []
        for i, c in enumerate(criterios):
            v = vereditos[i] if i < len(vereditos) else {"verificado": False}
            c["verificado"] = bool(v.get("verificado", False))
            c["evidencia"] = str(v.get("evidencia", ""))[:200]
        reprovados = [c["texto"] for c in criterios if not c["verificado"]]
        correcoes = (ft.get("correcoes") or 0) + 1
        ft["criterios"] = criterios
        ft["correcoes"] = correcoes
        if reprovados and correcoes <= 2:
            ft["feedback"] = "Critérios não atendidos: " + " | ".join(reprovados)[:400]
            ft["fase"] = "execute"
            return {
                "fluxo_trabalho": ft,
                "mensagens": [HumanMessage(
                    content=f"[verificação da entrega] {ft['feedback']} — corrija e refaça.")],
            }
        ft["feedback"] = ""
        ft["fase"] = "ship"
        return {"fluxo_trabalho": ft}

    def no_ship(state: EstadoAegis) -> dict:
        """Fase ship: selo da entrega + resumo + critérios; zero LLM."""
        ft = dict(state.get("fluxo_trabalho") or {})
        criterios = list(ft.get("criterios") or [])
        ultima = next(
            (m for m in reversed(state.get("mensagens") or [])
             if getattr(m, "type", "") == "ai"),
            None,
        )
        verificados = sum(1 for c in criterios if c.get("verificado"))
        ft["ship"] = {
            "resumo": str(getattr(ultima, "content", "") or "")[:400],
            "criterios_verificados": verificados,
            "total_criterios": len(criterios),
            "commits": len(state.get("commits_entrega") or []),
        }
        linhas = "\n".join(
            f"  {'✅' if c.get('verificado') else '⚠️'} {c.get('texto', '')[:100]}"
            for c in criterios
        ) or "  (sem critérios)"
        msg = AIMessage(content=(
            "🛳️ Entrega concluída (ciclo GSD) — fase `ship`.\n"
            f"Critérios verificados: {verificados}/{len(criterios)}\n{linhas}\n"
            f"Commits: {len(state.get('commits_entrega') or [])}\n"
            f"Resumo: {ft['ship']['resumo']}"
        ))
        return {"mensagens": [msg], "fluxo_trabalho": ft}

    def no_uat_apos_ship(state: EstadoAegis) -> dict:
        """UAT conversacional (G2): após o ship, apresenta os critérios de
        aceite UM A UM (interrupt, zero LLM) e registra resultado + evidência.

        Um critério por execução — o grafo volta ao nó até julgar todos
        (padrão loop de nós com interrupt; cada resume reexecuta o nó com o
        `uat` do estado atualizado). Ao julgar o último critério, o nó FECHA
        o UAT na mesma execução: mescla o histórico persistido da Store,
        calcula gaps e emite o selo 🧪. Critérios reprovados viram `gaps`
        persistidos POR PROJETO (sobrevivem a `/clear` e a troca de sessão);
        o próximo ciclo de entrega os retoma como contexto do plano.
        """
        ft = dict(state.get("fluxo_trabalho") or {})
        if ft.get("fase") != "ship":
            return {}
        criterios = list(ft.get("criterios") or [])
        if not criterios:
            return {}
        projeto = str((state.get("metadados_sessao") or {}).get("projeto", "default"))

        def fechar_uat(uat_atual: list[dict]) -> dict:
            antigo = _ler_uat_store(projeto)
            uat_final = antigo + uat_atual
            gaps = [u for u in uat_final if u.get("resultado") == "reprovado"]
            _gravar_uat_store(projeto, uat_final)
            aprovados = len(uat_final) - len(gaps)
            linhas = "\n".join(
                f"  {'✅' if u['resultado'] == 'aprovado' else '⚠️'} {u['criterio']}"
                for u in uat_final
            )
            msg = AIMessage(content=(
                f"🧪 UAT concluído — {aprovados}/{len(uat_final)} critérios aprovados.\n"
                f"{linhas}\n"
                + (("\nGaps (próximo ciclo):\n" + "\n".join(
                    f"  ⚠️ {u['criterio']}: {u.get('evidencia', '')[:120]}" for u in gaps))
                    if gaps else "Sem gaps — entrega aceita.")
            ))
            return {"uat": uat_final, "gaps": [u["criterio"] for u in gaps],
                    "mensagens": [msg]}

        uat_atual = list(state.get("uat") or [])
        julgados = {u.get("criterio") for u in uat_atual}
        pendentes = [c["texto"] for c in criterios if c["texto"] not in julgados]
        if not pendentes:
            return fechar_uat(uat_atual)
        texto = pendentes[0]
        resposta = interrupt(
            f"UAT ({projeto}): o critério \"{texto}\" foi atendido? "
            "Responda 'aprovado' ou 'reprovado: motivo'."
        )
        r = str(resposta or "").strip()
        aprovado = r.lower().startswith(("aprovado", "ok", "sim", "aceito", "passa"))
        uat_atual.append({
            "criterio": texto,
            "resultado": "aprovado" if aprovado else "reprovado",
            "evidencia": r[:300],
        })
        # se este foi o último critério, FECHA o UAT na mesma execução
        restantes = [c["texto"] for c in criterios
                     if c["texto"] not in {u.get("criterio") for u in uat_atual}]
        if not restantes:
            return fechar_uat(uat_atual)
        return {"uat": uat_atual}

    # ---- 8. Memória estrutural (C4) ---------------------------------------
    def no_memoria_estrutural(state: EstadoAegis) -> dict:
        """Resumo incremental + decisões-chave da sessão, persistidos na Store.

        Só consome LLM a cada `intervalo_resumo_sessao` mensagens (default 5) —
        turnos curtos seguem custo zero. O resumo anterior entra como contexto
        (incremento, não repetição).
        """
        if store is None:
            return {}
        thread_id = str((state.get("metadados_sessao") or {}).get("thread_id", ""))
        if not thread_id or len(state.get("mensagens") or []) < cfg.intervalo_resumo_sessao:
            return {}
        try:
            anterior = ""
            item = store.get(namespace_resumos(thread_id), "resumo")
            if item and item.value:
                anterior = str(item.value.get("texto", "")) if isinstance(item.value, dict) else str(item.value)
            recentes = [str(m.content) for m in (state.get("mensagens") or [])[-6:]]
            resp = com_retry(lambda: llm.invoke([
                SystemMessage(resumir_sessao()),
                HumanMessage(
                    f"Resumo anterior (se houver):\n{anterior or '(nenhum)'}\n\n"
                    f"Histórico recente:\n" + "\n".join(f"- {r[:300]}" for r in recentes)
                ),
            ]))
            import re as _re
            m = _re.search(r"\{.*\}", str(resp.content or ""), _re.DOTALL)
            dados = {}
            if m:
                try:
                    dados = json.loads(m.group(0))
                except json.JSONDecodeError:
                    dados = {}
            resumo = str(dados.get("resumo", "") or "").strip()[:1000]
            decisoes = [str(d) for d in (dados.get("decisoes") or []) if str(d).strip()][:4]
            if not resumo:
                return {}
            store.put(namespace_resumos(thread_id), "resumo",
                      {"texto": resumo, "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            store.put(namespace_decisoes(thread_id), "recentes",
                      {"lista": decisoes, "ts": time.strftime("%Y-%m-%d %H:%M:%S")})
            return {"resumo_sessao": resumo, "decisoes_turno": decisoes}
        except Exception:  # noqa: BLE001 — memória estrutural nunca derruba o fluxo
            if cfg.dev:
                import traceback
                traceback.print_exc()
            return {}

    # ---- 9. Reflexão pós-turno (C1) ---------------------------------------
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
        "no_verificar": no_verificar,
        "no_memoria_estrutural": no_memoria_estrutural,
        "no_classificador_entrega": no_classificador_entrega,
        "no_discuss": no_discuss,
        "no_plan_entrega": no_plan_entrega,
        "no_verify_entrega": no_verify_entrega,
        "no_ship": no_ship,
        "no_uat_apos_ship": no_uat_apos_ship,
    }