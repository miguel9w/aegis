"""
Subagentes avançados do Aegis — delegação via "agent-as-tool".

Arquitetura: cada subagente é um SUBGRAFO LangGraph compilado (stateless, sem
checkpointer) que reusa o mesmo loop cognitivo do núcleo — agente → ferramentas
→ reflexão (auto-correção) — mas com prompt de sistema ESPECIALISTA (persona)
e um SUBconjunto de ferramentas.

O agente principal expõe ferramentas `delegar_pesquisa` / `delegar_redacao` que
invocam esses subgrafos de forma síncrona e devolvem a resposta final. Em
grafos async, os subgrafos rodam em um worker thread (padrão do ToolNode).

Os subagentes são construídos em `configurar_subagentes(llm, cfg)` (chamada por
`montar_grafo`) e registrados no dicionário global `SUBAGENTES_ATUAIS`; as
ferramentas de delegação os leem em tempo de chamada.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.graph import END, START, StateGraph

from .config import Config
from .estado import EstadoAegis
from .nos import _eh_erro, fabricar_nos
from .prompts import sistema_pesquisador, sistema_redator

# Registrador global de subagentes (injetado por configurar_subagentes).
SUBAGENTES_ATUAIS: dict[str, Any] = {}


def _resposta_final(resultado: dict) -> str:
    """Extrai a última AIMessage com conteúdo (a resposta final do subagente)."""
    for m in reversed(resultado.get("mensagens") or []):
        if isinstance(m, AIMessage) and m.content:
            return str(m.content)
    return "(subagente não produziu resposta)"


def criar_subagente(
    nome: str,
    prompt: str,
    ferramentas: list,
    cfg: Config,
    llm,
) -> Any:
    """Compila um subagente (subgrafo stateless) com o loop cognitivo do núcleo."""
    nos = fabricar_nos(
        llm, ferramentas, store=None, cfg=cfg,
        prompt_fn=lambda *_args: prompt,
    )

    def rota_apos_agente(state: EstadoAegis) -> str:
        ultima = state["mensagens"][-1]
        if isinstance(ultima, AIMessage) and ultima.tool_calls:
            return "ferramentas"
        return END

    def rota_apos_ferramentas(state: EstadoAegis) -> str:
        ultima = state["mensagens"][-1]
        erro = isinstance(ultima, BaseMessage) and _eh_erro(ultima)
        tentativas = state.get("tentativas_correcao") or 0
        if erro and tentativas < cfg.max_tentativas_correcao:
            return "reflexao"
        return "agente"

    def rota_apos_reflexao(state: EstadoAegis) -> str:
        ultima = state["mensagens"][-1]
        if isinstance(ultima, AIMessage) and ultima.tool_calls:
            return "ferramentas"
        return END

    grafo = StateGraph(EstadoAegis)
    grafo.add_node("no_agente", nos["no_agente"])
    grafo.add_node("no_ferramentas", nos["no_ferramentas"])
    grafo.add_node("no_reflexao", nos["no_reflexao_auto_correcao"])

    grafo.add_edge(START, "no_agente")
    grafo.add_conditional_edges(
        "no_agente", rota_apos_agente, {"ferramentas": "no_ferramentas", END: END}
    )
    grafo.add_conditional_edges(
        "no_ferramentas", rota_apos_ferramentas,
        {"agente": "no_agente", "reflexao": "no_reflexao"},
    )
    grafo.add_conditional_edges(
        "no_reflexao", rota_apos_reflexao,
        {"ferramentas": "no_ferramentas", END: END},
    )
    return grafo.compile()


def _ferramentas_pesquisador() -> list:
    """Subconjunto de ferramentas do pesquisador: busca + cálculo + memória."""
    from .ferramentas.basicas import ferramentas_basicas
    from .recuperacao import pesquisar_memoria

    by_nome = {f.name: f for f in ferramentas_basicas()}
    return [
        by_nome["buscar_web"],
        by_nome["calculadora"],
        pesquisar_memoria,
    ]


def configurar_subagentes(llm, cfg: Config) -> None:
    """Constrói e registra os subagentes especialistas no registrador global."""
    SUBAGENTES_ATUAIS.clear()
    SUBAGENTES_ATUAIS.update(
        {
            "pesquisador": criar_subagente(
                "pesquisador", sistema_pesquisador(), _ferramentas_pesquisador(), cfg, llm
            ),
            "redator": criar_subagente(
                "redator", sistema_redator(), [], cfg, llm
            ),
        }
    )


def _executar(nome: str, pergunta: str, contexto: str | None) -> str:
    """Invoca um subagente registrado com a pergunta (e contexto opcional)."""
    grafo = SUBAGENTES_ATUAIS.get(nome)
    if grafo is None:
        return f"ERRO_FERRAMENTA: subagente '{nome}' não configurado."
    pergunta_final = pergunta
    if contexto:
        pergunta_final = f"{pergunta}\n\nContexto adicional:\n{contexto}"
    resultado = grafo.invoke({"mensagens": [HumanMessage(pergunta_final)]})
    return _resposta_final(resultado)


@tool
def delegar_pesquisa(pergunta: str, contexto: str | None = None) -> str:
    """Delega uma pesquisa profunda ao subagente PESQUISADOR.

    Use para perguntas complexas que exigem buscas na web, cruzamento de
    fontes ou raciocínio numérico com evidências. Retorna uma resposta já
    sintetizada em português (pt-BR).
    """
    return _executar("pesquisador", pergunta, contexto)


@tool
def delegar_redacao(tarefa: str) -> str:
    """Delega a produção de um texto longo ao subagente REDATOR.

    Use para escrever/reescrever conteúdo estruturado (artigos, relatórios,
    seções, comunicados) em pt-BR, quando a tarefa pedir texto extenso.
    """
    return _executar("redator", tarefa, None)