"""
Recuperação de memória (RAG-lite) — busca factual sobre a Store de longo prazo
e o repositório de habilidades (.skills/), sem dependências pesadas.

Estratégia: ranqueamento por sobreposição de tokens (peso IDF calculado sobre
o próprio corpus), determinístico e testável sem LLM.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.tools import tool

from . import skills as _skills  # leitura dinâmica (criar_skill recarrega o registro)

# Store de longo prazo injetada em montar_grafo (runtime)
STORE_ATUAL: Any = None

_REG_TOKEN = re.compile(r"[a-z0-9à-ÿ]+", re.IGNORECASE)


def definir_store(store: Any) -> None:
    """Vincula a Store de longo prazo à ferramenta de recuperação."""
    global STORE_ATUAL
    STORE_ATUAL = store


def _tokenizar(texto: str) -> list[str]:
    return [t.lower() for t in _REG_TOKEN.findall(texto or "")]


# ---------------------------------------------------------------------
# Corpus e ranqueamento
# ---------------------------------------------------------------------

def _itens_do_store() -> list[tuple[str, str]]:
    """Recupera textos da Store (perfil global + memórias por tópico)."""
    if STORE_ATUAL is None:
        return []
    textos: list[tuple[str, str]] = []
    try:
        for item in STORE_ATUAL.search(("aegis",), limit=50):
            valor = item.value
            texto = ""
            if isinstance(valor, str):
                texto = valor
            elif isinstance(valor, dict):
                texto = " ".join(str(v) for v in valor.values())
            if texto:
                textos.append((f"memória:{item.key}", texto))
    except Exception:  # noqa: BLE001 — busca nunca derruba o agente
        return []
    return textos


def _itens_das_skills() -> list[tuple[str, str]]:
    """Extrai nome + conteúdo das habilidades registradas."""
    textos: list[tuple[str, str]] = []
    for nome, info in _skills.HABILIDADES_REGISTRADAS.items():
        textos.append((f"skill:{nome}", info.get("conteudo", "")))
    return textos


def _idf(corpus: list[list[str]]) -> dict[str, float]:
    """Inverso de frequência documental — destaca termos raros/distintivos."""
    n = max(1, len(corpus))
    df: dict[str, int] = {}
    for doc in corpus:
        for tok in set(doc):
            df[tok] = df.get(tok, 0) + 1
    return {tok: 1.0 + (n / (freq + 1.0)) for tok, freq in df.items()}


def _pontuar(consulta: list[str], doc: list[str], idf: dict[str, float]) -> float:
    """Soma IDF dos tokens da consulta presentes no documento."""
    ocorrencias = set(doc) & set(consulta)
    return sum(idf.get(tok, 1.0) for tok in ocorrencias)


@tool
def pesquisar_memoria(consulta: str, limite: int = 5) -> str:
    """
    Busca fatos e preferências do usuário na memória de longo prazo (Store)
    e nos resumos de habilidades (.skills/). Use ANTES de responder quando a
    resposta depender de informações que o agente já conheceu em outras
    sessões. Retorna trechos ranqueados por relevância.
    """
    itens = _itens_do_store() + _itens_das_skills()
    if not itens or not consulta.strip():
        return "Nenhuma memória ou habilidade disponível para recuperação."

    corpus = [_tokenizar(texto) for _, texto in itens]
    idf = _idf(corpus)
    consulta_tok = _tokenizar(consulta)

    ranqueados = sorted(
        (
            (i, _pontuar(consulta_tok, doc, idf))
            for i, doc in enumerate(corpus)
        ),
        key=lambda par: par[1],
        reverse=True,
    )

    trechos: list[str] = []
    for indice, score in ranqueados:
        if score <= 0:
            break
        origem, texto = itens[indice]
        trecho = texto.strip().replace("\n", " ")[:300]
        trechos.append(f"[{origem}] {trecho}")
        if len(trechos) >= min(max(1, limite), 8):
            break

    if not trechos:
        return "Nenhum resultado relevante na memória para essa consulta."
    return "Memória recuperada:\n" + "\n".join(f"- {t}" for t in trechos)
