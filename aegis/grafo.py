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
    from .multiagente import montar_multiagente, obter_subgrafo
    from .nos import _eh_erro  # detecção de erro de ferramenta
    from .recuperacao import definir_store
    from .subagentes import configurar_subagentes
    cfg = cfg or _config_global
    if store is not None:
        definir_store(store)  # vincula a Store à ferramenta pesquisar_memoria
        from .memoria_tool import definir_store as definir_store_memoria
        definir_store_memoria(store)  # e à ferramenta gerenciar_memoria
    if cfg.subagentes_ativos:
        configurar_subagentes(llm, cfg)  # constrói os subagentes (agent-as-tool)

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
        return "verificar"  # C3: resposta final passa pela verificação

    def rota_apos_verificacao(state: EstadoAegis) -> str:
        """Divergência confirmada (1ª vez) volta ao agente para correção."""
        if (
            state.get("verificacao_veredito") == "divergencia"
            and (state.get("verificacoes_realizadas") or 0) <= 1
        ):
            return "agente"
        return "memoria"

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
        # C2: a reflexão desistiu sem corrigir e o plano ainda tem pendências?
        # Replanejar (marca passo como falho + LLM reformula) antes de voltar
        # ao agente. Sem plano ativo → fluxo legado byte-idêntico.
        if any(
            p.get("status", "pendente") in ("pendente", "executando")
            for p in (state.get("plano") or [])
        ):
            return "replanejar"
        return "agente"

    # --- Montagem -------------------------------------------------
    grafo = StateGraph(EstadoAegis)
    grafo.add_node("no_agente", nos["no_agente"])
    grafo.add_node("no_ferramentas", nos["no_ferramentas"])
    grafo.add_node("no_reflexao_auto_correcao", nos["no_reflexao_auto_correcao"])
    grafo.add_node("no_compressao_contexto", nos["no_compressao_contexto"])
    grafo.add_node("no_memoria", nos["no_memoria"])
    grafo.add_node("no_reflexao_pos_turno", nos["no_reflexao_pos_turno"])
    grafo.add_node("no_planejamento", nos["no_planejamento"])
    grafo.add_node("no_replanejamento", nos["no_replanejamento"])
    grafo.add_node("no_verificar", nos["no_verificar"])

    # --- Multiagente (F2): orquestrador na entrada, subgrafo por domínio ----
    if cfg.multiagente_ativos:
        multi = montar_multiagente(cfg)
        grafo.add_node("no_orquestrador", multi["no_orquestrador"])
        grafo.add_edge(START, "no_orquestrador")
        mapeamento: dict[str, str] = {"legado": "no_planejamento"}
        for dominio in multi["dominios"]:
            no_sub = f"sub_{dominio}"
            grafo.add_node(no_sub, obter_subgrafo(dominio, llm, ferramentas, cfg))
            mapeamento[no_sub] = no_sub
            grafo.add_edge(no_sub, "no_memoria")
        grafo.add_conditional_edges(
            "no_orquestrador",
            multi["rota_apos_orquestrador"],
            mapeamento,
        )
    else:
        # Fluxo legado (byte-idêntico): START → no_planejamento (heurística
        # zero-LLM) → no_agente. No modo multiagente QUEM decide a entrada é o
        # orquestrador (START → no_orquestrador); a rota dele encaminha PARA
        # no_planejamento quando não há domínio. As arestas duplas rodariam o
        # agente principal EM PARALELO com o subgrafo — bug de execução dupla.
        grafo.add_edge(START, "no_planejamento")

    # C2: planejamento decide-entra no agente; replan volta para o agente
    grafo.add_edge("no_planejamento", "no_agente")
    grafo.add_edge("no_replanejamento", "no_agente")

    grafo.add_conditional_edges(
        "no_agente",
        rota_apos_agente,
        {
            "ferramentas": "no_ferramentas",
            "comprimir": "no_compressao_contexto",
            "verificar": "no_verificar",
        },
    )
    grafo.add_conditional_edges(
        "no_verificar",
        rota_apos_verificacao,
        {"agente": "no_agente", "memoria": "no_memoria"},
    )
    grafo.add_conditional_edges(
        "no_ferramentas",
        rota_apos_ferramentas,
        {"agente": "no_agente", "reflexao": "no_reflexao_auto_correcao"},
    )
    grafo.add_conditional_edges(
        "no_reflexao_auto_correcao",
        rota_apos_reflexao,
        {"ferramentas": "no_ferramentas", "agente": "no_agente", "replanejar": "no_replanejamento"},
    )

    grafo.add_edge("no_compressao_contexto", "no_memoria")
    grafo.add_edge("no_memoria", "no_reflexao_pos_turno")
    grafo.add_edge("no_reflexao_pos_turno", END)

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