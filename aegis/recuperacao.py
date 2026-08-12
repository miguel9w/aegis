"""
Recuperação de memória (RAG-lite) — busca factual sobre a Store de longo prazo
e o repositório de habilidades (extensions/skills/), sem dependências pesadas.

Estratégia: ranqueamento por sobreposição de tokens (peso IDF calculado sobre
o próprio corpus), determinístico e testável sem LLM.
"""

from __future__ import annotations

import re
from typing import Any

from langchain_core.tools import tool

from . import skills as _skills  # leitura dinâmica (criar_skill recarrega o registro)
from .config import config as _cfg  # M1: GraphRAG usa o singleton global

# Store de longo prazo injetada em montar_grafo (runtime)
STORE_ATUAL: Any = None

STORE_ATUAL: Any = None
THREAD_ATUAL: str = ""

_REG_TOKEN = re.compile(r"[a-z0-9à-ÿ]+", re.IGNORECASE)


def definir_store(store: Any) -> None:
    """Vincula a Store de longo prazo às ferramentas de memória."""
    global STORE_ATUAL
    STORE_ATUAL = store


def definir_thread(thread_id: str) -> None:
    """Vincula o thread ativo às ferramentas de memória (C4)."""
    global THREAD_ATUAL
    THREAD_ATUAL = thread_id or ""


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
    """X2: indexa nome + DESCRIÇÃO das habilidades (rank por descrição, sem ler corpos)."""
    textos: list[tuple[str, str]] = []
    for nome, info in _skills.HABILIDADES_REGISTRADAS.items():
        textos.append((f"skill:{nome}", info.get("descricao", "")))
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
    e nos resumos de habilidades (extensions/skills/). Use ANTES de responder quando a
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

    # M1: memória GraphRAG — o grafo universal é a fonte de IMPORTANTE;
    # consulta quando o Neo4j está ativo (None = inativo → segue só RAG-lite)
    from .neografo import consultar_graphrag
    bloco = consultar_graphrag(_cfg, consulta, "universal", limite=3)
    if bloco:
        grafo_itens = [l for l in bloco.splitlines() if l.startswith("- ")]
        trechos = grafo_itens + trechos

    if not trechos:
        return "Nenhum resultado relevante na memória para essa consulta."
    return "Memória recuperada:\n" + "\n".join(f"- {t}" for t in trechos)


# ---------------------------------------------------------------------
# Recall de lições aprendidas (C1 — memória procedimental)
# ---------------------------------------------------------------------

def recuperar_licoes(store: Any, consulta: str, limite: int = 3) -> str:
    """Recupera lições aprendidas relevantes à consulta (mesmo IDF do RAG-lite).

    Retorna um bloco formatado para injeção no prompt de sistema, ou "" quando
    não há lições relevantes (menos de `limite` com score > 0) — o nó do agente
    só acrescenta o bloco se houver conteúdo, mantendo o system byte-idêntico
    nos demais casos.
    """
    if store is None or not consulta.strip():
        return ""
    try:
        itens: list[tuple[str, str]] = []
        for item in store.search(("aegis", "licoes"), limit=40):
            valor = item.value
            texto = ""
            if isinstance(valor, dict):
                texto = str(valor.get("texto", ""))
            elif isinstance(valor, str):
                texto = valor
            if texto and texto.strip():
                itens.append((item.key, texto))
    except Exception:  # noqa: BLE001 — recall nunca derruba o agente
        return ""
    if not itens:
        return ""

    corpus = [_tokenizar(texto) for _, texto in itens]
    idf = _idf(corpus)
    consulta_tok = _tokenizar(consulta)

    ranqueados = sorted(
        ((i, _pontuar(consulta_tok, doc, idf)) for i, doc in enumerate(corpus)),
        key=lambda par: par[1],
        reverse=True,
    )
    trechos: list[str] = []
    for indice, score in ranqueados:
        if score <= 0:
            break
        trechos.append(itens[indice][1].strip().replace("\n", " ")[:300])
        if len(trechos) >= limite:
            break
    if not trechos:
        return ""
    return "## Lições aprendidas (memória procedimental)\n" + "\n".join(
        f"- {t}" for t in trechos
    )


# ---------------------------------------------------------------------
# Recall hierárquico (C4 — memória estrutural)
# ---------------------------------------------------------------------

def _nivel(secao: str, conteudo: str, teto: int) -> str:
    """Monta um nível do recall; corta por teto de caracteres quando preciso."""
    conteudo = conteudo.strip()
    if not conteudo:
        return ""
    if len(conteudo) > teto:
        conteudo = conteudo[:teto].rsplit(" ", 1)[0] + "…"
    return f"## {secao}\n{conteudo}"


def recuperar_contexto_para_system(
    store: Any, thread_id: str, consulta: str, teto: int = 600
) -> str:
    """Recall hierárquico para injeção no system: perfil → lições → resumo → decisões.

    Cada nível é cortado pelo teto; a ORDEM reflete a prioridade de memória
    (fatos estáveis primeiro, sessão/detalhes depois). Retorna \"\" quando nada
    há (system byte-idêntico).
    """
    if store is None:
        return ""
    blocos: list[str] = []
    try:
        # 1. perfil (fatos estáveis do usuário)
        item = store.get(("aegis", "perfil"), "perfil")
        if item and item.value:
            fatos = " | ".join(f"{k}: {v}" for k, v in item.value.items())
            blocos.append(_nivel("Perfil do usuário", fatos, teto))
        # 2. lições relevantes (IDF — C1)
        licoes = recuperar_licoes(store, consulta)
        if licoes:
            blocos.append(licoes)
        # 3. resumo incremental da sessão
        item = store.get(("aegis", "resumos", thread_id), "resumo")
        if item and item.value:
            texto = str(item.value.get("texto", "")) if isinstance(item.value, dict) else str(item.value)
            blocos.append(_nivel("Resumo da sessão", texto, teto))
        # 4. decisões recentes da sessão
        item = store.get(("aegis", "decisoes", thread_id), "recentes")
        if item and item.value:
            lista = item.value.get("lista", []) if isinstance(item.value, dict) else []
            if isinstance(lista, list) and lista:
                blocos.append(_nivel("Decisões recentes", "\n".join(f"- {d}" for d in lista), teto))
        # 5. M1: grafo universal (GraphRAG) — importante e durável; None = inativo
        from .neografo import consultar_graphrag
        bloco_grafo = consultar_graphrag(_cfg, consulta, "universal", limite=3)
        if bloco_grafo:
            blocos.append(bloco_grafo)
    except Exception:  # noqa: BLE001 — recall nunca derruba o agente
        return ""
    return "\n\n".join(b for b in blocos if b)


@tool
def recuperar_contexto(
    assunto: str = "",
    escopo_sessao: bool = True,
    limite_por_nivel: int = 600,
) -> str:
    """
    Recupera o contexto estruturado do Aegis para a tarefa atual: perfil do
    usuário → lições aprendidas → resumo da sessão → decisões recentes
    (hierarquia de confiabilidade). Use quando precisar revisar o que é
    estável (fatos, lições) antes de agir, ou para retomar uma sessão depois
    de uma pausa.

    - assunto: o tópico da tarefa (melhora o ranqueamento das lições relevantes).
    - escopo_sessao: inclui resumo + decisões da sessão atual (True por padrão).
    - limite_por_nivel: teto de caracteres por nível.
    """
    if STORE_ATUAL is None:
        return "Memória indisponível (Store não vinculada)."
    thread_id = THREAD_ATUAL or ""
    bloco = recuperar_contexto_para_system(
        STORE_ATUAL, thread_id, assunto, teto=limite_por_nivel
    )
    if not bloco:
        return "Nenhum contexto recuperado (memória vazia para este escopo)."
    return bloco
