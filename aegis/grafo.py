"""
Montagem do grafo LangGraph cíclico do Aegis.

Fluxo:
    START → no_agente
      ├─ tem tool_calls?  → no_ferramentas
      ├─ histórico longo? → no_compressao_contexto → no_memoria → END
      └─ senão            → no_memoria → END

    no_ferramentas:
      ├─ erro detectado (e ¬limite) → no_reflexao_auto_correcao
      │      └─ corrigiu com tool_calls? → no_ferramentas
      │         └─ senão (resposta final) → no_agente
      └─ sucesso → no_agente

Checkpointer + Store são injetados na compilação para persistência.
"""

from __future__ import annotations

from typing import Any, Callable

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from .config import Config
from .estado import EstadoAegis
from .nos import fabricar_nos


def montar_grafo(
    llm,
    ferramentas: list,
    *,
    checkpointer: Any = None,
    store: Any = None,
    cfg: Config | None = None,
):
    """
    Compila o grafo completo do Aegis.

    Args:
        llm: modelo ChatOpenAI (provedor cognitivo)
        ferramentas: ferramentas registradas (built-in + skills + plugins)
        checkpointer: SqliteSaver para checkpoints por passo
        store: SqliteStore para memória de longo prazo
        cfg: Configuração (.env)
    """
    from .config import config as _config_global
    from .nos import _eh_erro  # detecção de erro de ferramenta
    cfg = cfg or _config_global

    def _ultima_ferramenta_erro(mensagens) -> bool:
        """Verifica se a ÚLTIMA execução de ferramenta terminou em erro.

        Consulta o ToolMessage mais recente do lote; se foi erro, a auto-correção
        pode disparar novamente (respeitando o limite de tentativas).
        """
        for m in reversed(mensagens):
            if isinstance(m, AIMessage):
                break  # fim do lote de ferramentas (não olhar além)
            if isinstance(m, ToolMessage):
                return _eh_erro(m)
        return False

    nos = fabricar_nos(llm, ferramentas, store, cfg)

    # --- Roteadores condicionais ----------------------------------
    def rota_apos_agente(state: EstadoAegis) -> str:
        ultima = state["mensagens"][-1]
        if isinstance(ultima, AIMessage) and ultima.tool_calls:
            return "ferramentas"
        if len(state["mensagens"]) >= cfg.limiar_compressao:
            return "comprimir"
        return "fim"

    def rota_apos_ferramentas(state: EstadoAegis) -> str:
        erro_na_ultima_execucao = _ultima_ferramenta_erro(state["mensagens"])
        tentativas = state.get("tentativas_correcao") or 0
        if erro_na_ultima_execucao and tentativas < cfg.max_tentativas_correcao:
            return "reflexao"
        return "agente"

    def rota_apos_reflexao(state: EstadoAegis) -> str:
        ultima = state["mensagens"][-1]
        if isinstance(ultima, AIMessage) and ultima.tool_calls:
            return "ferramentas"
        return "agente"

    # --- Montagem -------------------------------------------------
    grafo = StateGraph(EstadoAegis)
    grafo.add_node("no_agente", nos["no_agente"])
    grafo.add_node("no_ferramentas", nos["no_ferramentas"])
    grafo.add_node("no_reflexao_auto_correcao", nos["no_reflexao_auto_correcao"])
    grafo.add_node("no_compressao_contexto", nos["no_compressao_contexto"])
    grafo.add_node("no_memoria", nos["no_memoria"])

    grafo.add_edge(START, "no_agente")

    grafo.add_conditional_edges(
        "no_agente",
        rota_apos_agente,
        {
            "ferramentas": "no_ferramentas",
            "comprimir": "no_compressao_contexto",
            "fim": "no_memoria",
        },
    )
    grafo.add_conditional_edges(
        "no_ferramentas",
        rota_apos_ferramentas,
        {"agente": "no_agente", "reflexao": "no_reflexao_auto_correcao"},
    )
    grafo.add_conditional_edges(
        "no_reflexao_auto_correcao",
        rota_apos_reflexao,
        {"ferramentas": "no_ferramentas", "agente": "no_agente"},
    )

    grafo.add_edge("no_compressao_contexto", "no_memoria")
    grafo.add_edge("no_memoria", END)

    return grafo.compile(checkpointer=checkpointer, store=store)


# ---------------------------------------------------------------------
# Helpers de execução (API pública usada pelo CLI/TUI)
# ---------------------------------------------------------------------

def mk_config(thread_id: str) -> dict:
    """Config de execução padrão (thread_id)."""
    return {"configurable": {"thread_id": thread_id}}


def executar_headless(app, pergunta: str, thread_id: str) -> dict:
    """Executa uma pergunta de forma síncrona (automação/testes)."""
    config = mk_config(thread_id)
    entrada = {
        "mensagens": [HumanMessage(pergunta)],
        "metadados_sessao": {"thread_id": thread_id},
    }
    return app.invoke(entrada, config=config)