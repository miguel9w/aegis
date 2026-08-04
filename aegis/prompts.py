"""
Construção do Prompt de Sistema e dos prompts auxiliares (pt-BR).

O sistema injeta dinamicamente: identidade, perfil do usuário (memória
de longo prazo), resumo de contexto comprimido e o catálogo de ferramentas
disponíveis.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool

IDENTIDADE = (
    "Você é o **Aegis**, um agente pessoal autônomo de última geração. "
    "Você é proativo, preciso e determinístico. Responda em português do Brasil."
)


def _json_perfil(perfil: dict[str, Any] | None) -> str:
    if not perfil:
        return "(sem perfil cadastrado. pergunte ao usuário se necessário.)"
    try:
        return json.dumps(perfil, ensure_ascii=False, indent=2)
    except TypeError:
        return str(perfil)


def _catalogo_ferramentas(ferramentas: list[BaseTool]) -> str:
    if not ferramentas:
        return "(nenhuma ferramenta disponível)"
    linhas = []
    for f in ferramentas:
        desc = (f.description or "").splitlines()[0][:120]
        linhas.append(f"- `{f.name}`: {desc}")
    return "\n".join(linhas)


def sistema(perfil: dict[str, Any] | None,
            resumo: str,
            ferramentas: list[BaseTool],
            metadados: dict[str, Any] | None = None) -> str:
    """Monta o prompt de sistema completo (identidade + contexto + ferramentas)."""
    partes = [IDENTIDADE]

    # Regras de uso de ferramentas — essenciais para Function Calling disciplinado
    regras = (
        "## Como usar ferramentas\n"
        "- Sempre que precisar de informação externa (web, hora, cálculo, comandos), "
        "chame a ferramenta apropriada — não invente dados.\n"
        "- Se uma ferramenta falhar, avalie a mensagem de erro e tente reformular "
        "a chamada (o sistema já reexecuta automaticamente).\n"
        "- Quando a tarefa estiver concluída, dê a resposta final clara e objetiva."
    )
    partes.append(regras)

    if perfil:
        partes.append(f"## Perfil do usuário (memória de longo prazo)\n{_json_perfil(perfil)}")

    if resumo:
        partes.append(f"## Resumo de conversa anterior (contexto comprimido)\n{resumo}")

    partes.append(f"## Ferramentas disponíveis\n{_catalogo_ferramentas(ferramentas)}")

    if metadados:
        partes.append(f"## Metadados de sessão\n{json.dumps(metadados, ensure_ascii=False)}")

    partes.append(
        "Responda de forma útil, coerente e factual. Seja explícito ao pedir "
        "confirmações quando faltar informação crucial."
    )
    return "\n\n".join(partes)


# ---------------------------------------------------------------------
# Prompts auxiliares dos nós
# ---------------------------------------------------------------------

def reflexao_auto_correcao() -> str:
    """Prompt do nó de reflexão: analisar erro de ferramenta e reformular."""
    return (
        "Uma chamada de ferramenta falhou com o erro abaixo. Sua tarefa:\n"
        "1. Analise o erro (mensagem de erro precedida de 'ERRO_FERRAMENTA:').\n"
        "2. Se o erro for corrigível (argumentos inválidos, comando mal formado, "
        "ferramenta alternativa), reformule a chamada e invoque a ferramenta de novo.\n"
        "3. Se o erro for irrecuperável, explique o ocorrido ao usuário em um texto "
        "FINAL (não invoque ferramenta) e sugira uma alternativa.\n"
        "Nunca minta sobre ter executado algo que falhou."
    )


def resumir_historico() -> str:
    """Prompt do nó de compressão: resumir mensagens antigas."""
    return (
        "Você é um resumidor de contexto de um agente conversacional. Receba um "
        "trecho de diálogo e produza um RESUMO CONCISO em português, preservando:\n"
        "- fatos e preferências do usuário,\n"
        "- intenções, decisões tomadas e pendências,\n"
        "- informações factuais relevantes.\n"
        "Saída: apenas o resumo, sem preâmbulos."
    )


def extrair_memoria() -> str:
    """Prompt do nó de memória: extrair fatos duráveis do perfil do usuário."""
    return (
        "A partir do diálogo, extraia fatos duráveis e estáveis sobre o usuário "
        "para memória de longo prazo (nome, profissão, preferências, idioma, "
        "projetos, hábitos). Retorne apenas um JSON válido com a estrutura:\n"
        '{"fatos": {"chave": "valor"}} . Objetos temporários (ex.: "o usuário '
        'perguntou às 14h") NÃO devem ser gravados. Se nada for durável, '
        'retorne {"fatos": {}}.'
    )