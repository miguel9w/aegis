"""
Estado global do grafo LangGraph.

Um `TypedDict` que estende o padrão do LangGraph. O histórico de mensagens
é um campo obrigatório e usa o reducer `add_messages`, que mescla mensagens
por ID e garante a correta intercalação de AIMessage/ToolMessage.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, NotRequired, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


def _merge_dict(atual: dict | None, novo: dict | None) -> dict:
    """Reducer de merge para dicionários escritos por nós em paralelo.

    Cada especialista grava a SUA chave (slot) no dict; o reducer combina as
    escritas concorrentes em uma única visão sem sobrescrever slots alheios.
    """
    base = dict(atual or {})
    if novo:
        base.update(novo)
    return base


class EstadoAegis(TypedDict):
    # Histórico de conversa (reducer padrão add_messages)
    mensagens: Annotated[list[BaseMessage], add_messages]

    # Metadados de sessão (thread, timestamp, etc.)
    metadados_sessao: NotRequired[dict[str, Any]]

    # Registro (log) de ferramentas executadas: [{nome, args, resultado, erro}]
    registros_ferramentas: NotRequired[list[dict[str, Any]]]

    # Perfil / contexto do usuário (carregado da Store de longo prazo)
    perfil_usuario: NotRequired[dict[str, Any]]

    # Resumo de histórico antigo comprimido (janela de contexto)
    contexto_comprimido: NotRequired[str]

    # Mensagens de erro de ferramentas (para o nó de reflexão). Usa reducer de
    # SOMA para ACUMULAR erros ao longo do loop de auto-correção (caso outra
    # ferramenta sobrescreva o estado após o erro, o registro é preservado).
    erros_ferramenta: NotRequired[Annotated[list[str], operator.add]]

    # Contador de tentativas do loop de auto-correção (limita o ciclo)
    tentativas_correcao: NotRequired[int]

    # --- Orquestração multiagente (nós especialistas em paralelo) ---
    # Domínio ativo no turno ("" = fluxo de agente único)
    dominio: NotRequired[str]

    # Divisão da tarefa em slots: [{slot, tarefa, estrategia, status}]
    divisao: NotRequired[list[dict[str, Any]]]

    # Saída por especialista: rascunhos[slot] -> conteúdo. Reducer de merge:
    # nós paralelos escrevem chaves DIFERENTES do dict e o LangGraph precisa de
    # um reducer para a chave em si (senão INVALID_CONCURRENT_GRAPH_UPDATE).
    rascunhos: NotRequired[Annotated[dict[str, Any], _merge_dict]]

    # Vereditos do avaliador (append-only — lição de fan-out: chave agregada
    # com operator.add, síntese no nó a jusante)
    vereditos: NotRequired[Annotated[list[dict[str, Any]], operator.add]]

    # Artefato consolidado pelo integrador/orquestrador (resposta final multiagente)
    orquestracao_final: NotRequired[str]

    # Modo conservador (provider free: comandos curtos, estratégia rebaixada)
    modo_conservador: NotRequired[bool]

    # --- Reflexão pós-turno (C1) ---
    # Lições extraídas no fim do turno (memória procedimental)
    licoes_turno: NotRequired[list[str]]

    # --- Plan-and-execute (C2) ---
    # Plano ativo: [{"passo", "objetivo", "status": pendente|executando|concluido|falhou}]
    plano: NotRequired[list[dict[str, str]]]
    # True quando o planejamento já foi considerado neste turno (evita re-LLM)
    plano_considerado: NotRequired[bool]

    # --- Verify-then-answer (C3) ---
    # Evidências da verificação: [{"fonte", "conferida", "observacao"}]
    evidencias: NotRequired[list[dict[str, Any]]]
    # Veredito da última verificação: "ok" | "divergencia"
    verificacao_veredito: NotRequired[str]
    # Nº de correções por verificação no turno (limita o loop corrigir→verificar)
    verificacoes_realizadas: NotRequired[int]

    # --- Memória estrutural (C4) ---
    # Último resumo incremental da sessão (também persistido na Store)
    resumo_sessao: NotRequired[str]
    # Decisões-chave extraídas no fim do turno
    decisoes_sessao: NotRequired[list[str]]

    # --- Modo entrega (G1) ---
    # Ciclo GSD ativo: {"fase": discuss|plan|execute|verify|ship, "plano": [...],
    # "criterios": [{"texto", "verificado", "evidencia"}], "ship": {...},
    # "feedback": str, "correcoes": int, "pergunta": str|None}
    fluxo_trabalho: NotRequired[dict[str, Any]]
    # Commits atômicos emitidos a cada wave do execute (append-only)
    commits_entrega: NotRequired[Annotated[list[dict[str, Any]], operator.add]]

    # --- UAT conversacional (G2) ---
    # Julgamentos por critério: [{criterio, resultado, evidencia}]
    uat: NotRequired[list[dict[str, Any]]]
    # Critérios reprovados (viram contexto do próximo ciclo de entrega)
    gaps: NotRequired[list[str]]