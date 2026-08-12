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
from . import factcheck as _factcheck
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
        # C6 PRIMEIRO: orçamento estourado → resumo parcial, NADA mais executa
        # (nem tools pendentes, nem verify, nem memória — o turno termina)
        if state.get("orcamento_estourado"):
            return "fim_corte"
        ultima = state["mensagens"][-1]
        if isinstance(ultima, AIMessage) and ultima.tool_calls:
            return "ferramentas"
        # G1: no ciclo de entrega, resposta final (sem tool_calls) vai ao
        # verify goal-backward — NUNCA ao fim direto.
        ft = state.get("fluxo_trabalho")
        if ft and ft.get("fase") in ("execute", "verify"):
            return "verify_entrega"
        if len(state["mensagens"]) >= cfg.limiar_compressao:
            return "comprimir"
        return "verificar"  # C3: resposta final passa pela verificação

    def rota_apos_verify_entrega(state: EstadoAegis) -> str:
        """Tudo verificado → revisão por pares (G3); reprovou → execute."""
        ft = state.get("fluxo_trabalho") or {}
        return "revisar" if ft.get("fase") == "ship" else "agente"

    def rota_apos_revisar(state: EstadoAegis) -> str:
        """G3: revisão aprovada → ship; apontamento bloqueante → execute."""
        ft = state.get("fluxo_trabalho") or {}
        return "ship" if ft.get("fase") == "ship" else "agente"

    def rota_apos_classificador(state: EstadoAegis) -> str:
        """G1: pedido de entrega → ciclo; senão fluxo legado byte-idêntico."""
        ft = state.get("fluxo_trabalho")
        return "ciclo" if ft and ft.get("fase") else "legado"

    def rota_apos_verificacao(state: EstadoAegis) -> str:
        """Divergência confirmada (1ª vez) volta ao agente para correção.

        X3: turno que consultou a web passa pelo `no_fact_check` antes da
        memória; sem web → direto (zero custo).
        """
        if (
            state.get("verificacao_veredito") == "divergencia"
            and (state.get("verificacoes_realizadas") or 0) <= 1
        ):
            return "agente"
        if _factcheck.turno_usou_busca_web(state):
            return "fact_check"
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
    grafo.add_node("no_memoria_estrutural", nos["no_memoria_estrutural"])
    grafo.add_node("no_reflexao_pos_turno", nos["no_reflexao_pos_turno"])
    grafo.add_node("no_planejamento", nos["no_planejamento"])
    grafo.add_node("no_replanejamento", nos["no_replanejamento"])
    grafo.add_node("no_verificar", nos["no_verificar"])
    grafo.add_node("no_fact_check", _factcheck.no_fact_check)  # X3: zero-LLM
    grafo.add_node("no_classificador_entrega", nos["no_classificador_entrega"])
    grafo.add_node("no_discuss", nos["no_discuss"])
    grafo.add_node("no_plan_entrega", nos["no_plan_entrega"])
    grafo.add_node("no_verify_entrega", nos["no_verify_entrega"])
    grafo.add_node("no_revisar", nos["no_revisar"])
    grafo.add_node("no_ship", nos["no_ship"])
    grafo.add_node("no_uat_apos_ship", nos["no_uat_apos_ship"])

    # --- Multiagente (F2): orquestrador na entrada, subgrafo por domínio ----
    # G1: o classificador de entrega roda em AMBAS as entradas — o ramo
    # "legado" do orquestrador (multi) e o START (mono) — para que o ciclo
    # GSD funcione nos dois modos.
    grafo.add_node("no_classificador_entrega_legado", nos["no_classificador_entrega"])
    # G1: o classificador roteia ciclo GSD vs. fluxo legado em AMBOS os modos
    # (mono: START → classificador; multi: orquestrador → legado → classificador).
    grafo.add_conditional_edges(
        "no_classificador_entrega_legado",
        rota_apos_classificador,
        {"ciclo": "no_discuss", "legado": "no_planejamento"},
    )
    if cfg.multiagente_ativos:
        multi = montar_multiagente(cfg)
        grafo.add_node("no_orquestrador", multi["no_orquestrador"])
        grafo.add_edge(START, "no_orquestrador")
        mapeamento: dict[str, str] = {
            "legado": "no_classificador_entrega_legado",
        }
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
        # Fluxo legado (byte-idêntico): START → no_classificador_entrega
        # (zero-LLM, G1) → fluxo legado (no_planejamento heurístico → agente)
        # ou ciclo GSD (no_discuss → plan → execute → verify → ship).
        grafo.add_edge(START, "no_classificador_entrega_legado")

    # C2: planejamento decide-entra no agente; replan volta para o agente
    grafo.add_edge("no_planejamento", "no_agente")
    grafo.add_edge("no_replanejamento", "no_agente")

    # G1: ciclo de entrega — discuss → plan → execute(agente/ferramentas)
    grafo.add_edge("no_discuss", "no_plan_entrega")
    grafo.add_edge("no_plan_entrega", "no_agente")

    grafo.add_conditional_edges(
        "no_agente",
        rota_apos_agente,
        {
            "ferramentas": "no_ferramentas",
            "comprimir": "no_compressao_contexto",
            "verificar": "no_verificar",
            "verify_entrega": "no_verify_entrega",
            "fim_corte": END,
        },
    )
    grafo.add_conditional_edges(
        "no_verify_entrega",
        rota_apos_verify_entrega,
        {"revisar": "no_revisar", "agente": "no_agente"},
    )
    # G3: revisão por pares (segunda opinião obrigatória) entre verify e ship
    grafo.add_conditional_edges(
        "no_revisar",
        rota_apos_revisar,
        {"ship": "no_ship", "agente": "no_agente"},
    )
    grafo.add_edge("no_ship", "no_uat_apos_ship")
    # G2: UAT pergunta critério a critério (interrupt); quando todos julgados,
    # segue para a memória estrutural (C4). Sem critérios/sem ship → memória.
    def rota_apos_uat(state: EstadoAegis) -> str:
        ft = state.get("fluxo_trabalho") or {}
        criterios = [c.get("texto") for c in (ft.get("criterios") or [])]
        if not criterios:
            return "fact_check" if _factcheck.turno_usou_busca_web(state) else "memoria"
        julgados = {u.get("criterio") for u in (state.get("uat") or [])}
        if any(c not in julgados for c in criterios):
            return "uat"
        return "fact_check" if _factcheck.turno_usou_busca_web(state) else "memoria"

    grafo.add_conditional_edges(
        "no_uat_apos_ship",
        rota_apos_uat,
        {"uat": "no_uat_apos_ship", "fact_check": "no_fact_check", "memoria": "no_memoria"},
    )
    grafo.add_conditional_edges(
        "no_verificar",
        rota_apos_verificacao,
        {"agente": "no_agente", "fact_check": "no_fact_check", "memoria": "no_memoria"},
    )
    # X3: fact-check (zero-LLM) roda antes da memória quando o turno buscou web
    grafo.add_edge("no_fact_check", "no_memoria")
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
    grafo.add_edge("no_memoria", "no_memoria_estrutural")
    grafo.add_edge("no_memoria_estrutural", "no_reflexao_pos_turno")
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