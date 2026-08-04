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