"""C6 — medição de uso, custo estimado e orçamento (billing guard).

Contabilidade de tokens (entrada/saída/reasoning) extraída das respostas
OpenAI-compat, custo estimado por tabela configurável (R$ por 1M tokens em
`config/dados/limites.json`) e corte de execução quando o orçamento estoura.
Paridade caveman-stats: `estatisticas` na superfície do agente.

Sem imports de runtime (config entra por parâmetro) — módulo puro, testável.
"""

from __future__ import annotations

from typing import Any

# Padrão de mercado quando o config não traz a tabela (R$ por 1M tokens)
PRECOS_PADRAO: dict[str, float] = {"entrada": 0.55, "saida": 2.2, "reasoning": 3.0}

_CHAVES = ("entrada", "saida", "reasoning")


def extrair_uso(resposta: Any) -> dict[str, int]:
    """Uso de uma resposta OpenAI-compat → {entrada, saida, reasoning}.

    Lê `response_metadata.token_usage` (prompt_tokens/completion_tokens com
    `completion_tokens_details.reasoning_tokens`). Omissões contam como 0.
    """
    md = getattr(resposta, "response_metadata", None) or {}
    tu = md.get("token_usage") or {}
    detalhes = tu.get("completion_tokens_details") or {}
    return {
        "entrada": int(tu.get("prompt_tokens") or 0),
        "saida": int(tu.get("completion_tokens") or 0),
        "reasoning": int(detalhes.get("reasoning_tokens") or 0),
    }


def somar_uso(acumulado: dict[str, int] | None, novo: dict[str, int] | None) -> dict[str, int]:
    """Reducer de soma por chave — `uso_tokens` acumula no estado (sessão)."""
    base = dict(acumulado or {})
    for chave, valor in (novo or {}).items():
        base[chave] = int(base.get(chave, 0)) + int(valor or 0)
    return base


def total_tokens(uso: dict[str, int] | None) -> int:
    """Entrada + saída + reasoning de uma contabilidade."""
    uso = uso or {}
    return sum(int(uso.get(chave, 0)) for chave in _CHAVES)


def custo_estimado(uso: dict[str, int] | None, precos: dict[str, float] | None = None) -> float:
    """Custo em R$ estimado pela tabela de preços (R$ por 1M de tokens)."""
    precos = precos or PRECOS_PADRAO
    uso = uso or {}
    return round(
        sum(int(uso.get(chave, 0)) * float(precos.get(chave, 0)) / 1_000_000
            for chave in _CHAVES),
        4,
    )


def verificar_orcamento(
    uso_turno: dict[str, int],
    uso_sessao: dict[str, int],
    orcamento_turno: dict[str, float] | None = None,
    orcamento_sessao: dict[str, float] | None = None,
    precos: dict[str, float] | None = None,
) -> dict[str, Any] | None:
    """Corte? Estouro de tokens OU reais (turno ou sessão) → detalhes do corte.

    Orçamento vazio/ausente = sem teto. Retorna None quando tudo dentro.
    """
    orcamento_turno = orcamento_turno or {}
    orcamento_sessao = orcamento_sessao or {}
    teto_turno = float(orcamento_turno.get("tokens", float("inf")))
    teto_sessao = float(orcamento_sessao.get("tokens", float("inf")))
    teto_reais_turno = float(orcamento_turno.get("reais", float("inf")))
    teto_reais_sessao = float(orcamento_sessao.get("reais", float("inf")))

    if total_tokens(uso_turno) > teto_turno:
        return {"escopo": "turno", "metrica": "tokens",
                "teto": teto_turno, "usado": total_tokens(uso_turno)}
    if custo_estimado(uso_turno, precos) > teto_reais_turno:
        return {"escopo": "turno", "metrica": "reais",
                "teto": teto_reais_turno, "usado": custo_estimado(uso_turno, precos)}
    if total_tokens(uso_sessao) > teto_sessao:
        return {"escopo": "sessao", "metrica": "tokens",
                "teto": teto_sessao, "usado": total_tokens(uso_sessao)}
    if custo_estimado(uso_sessao, precos) > teto_reais_sessao:
        return {"escopo": "sessao", "metrica": "reais",
                "teto": teto_reais_sessao, "usado": custo_estimado(uso_sessao, precos)}
    return None