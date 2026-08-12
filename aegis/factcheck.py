"""
Fact-checking com fontes (X3 — paridade web-deep-research).

Quando o turno consultou a web (`buscar_web`), o nó `no_fact_check` roda antes
do fim: extrai as fontes estruturadas `{url, titulo, trecho}` dos registros da
ferramenta, agrupa por consulta e classifica cada afirmação:

- **afirmado** — ≥2 fontes da mesma consulta com trechos consistentes
  (sobreposição lexical acima do limiar e sem negação oposta).
- **divergencia** — fontes da mesma consulta dizem coisas diferentes
  (trechos disjuntos) ou se contradizem (negação oposta) → a resposta cita as
  duas.
- **fonte_unica** — só uma fonte respondeu; não afirma nem diverge.

Tudo determinístico (zero LLM) e auditável: o estado ganha
`fontes: list[{afirmacao, urls, status}]` e a resposta final anexa um bloco
"Fontes verificadas". Turno sem web → o nó não roda (zero custo).
"""

from __future__ import annotations

import json
import re
from typing import Any

from .estado import EstadoAegis

# Limiar de sobreposição (Jaccard de shingles de 3 tokens) para considerar
# dois trechos consistentes entre si.
_LIMIAR_CONSISTENCIA = 0.25

# Marcadores de negação que, presentes em UM trecho e ausentes no outro,
# indicam contradição mesmo com alta sobreposição lexical.
_NEGACOES = re.compile(
    r"\b(não|nao|nenhum|nenhuma|nunca|jamais|sem|nada|impossível|impossivel|inexistente)\b",
    re.IGNORECASE,
)

_RE_JSON_FONTES = re.compile(r"\[\s*\{.*?\}\s*\]", re.DOTALL)


def extrair_fontes(registros_ferramentas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Extrai `{url, titulo, trecho, consulta}` dos registros de `buscar_web`.

    O resultado da ferramenta é JSON estruturado (com a marcação C5 em volta);
    registros sem JSON válido são ignorados (o turno segue sem fact-check).
    """
    fontes: list[dict[str, Any]] = []
    for reg in registros_ferramentas or []:
        if reg.get("nome") != "buscar_web" or reg.get("erro"):
            continue
        consulta = str((reg.get("args") or {}).get("consulta", ""))
        for fonte in _parsear_resultado(str(reg.get("resultado", ""))):
            fonte["consulta"] = consulta
            fontes.append(fonte)
    return fontes


def _parsear_resultado(resultado: str) -> list[dict[str, Any]]:
    """Extrai a lista de fontes do JSON embutido no resultado marcado (C5)."""
    m = _RE_JSON_FONTES.search(resultado)
    if not m:
        return []
    try:
        dados = json.loads(m.group(0))
    except json.JSONDecodeError:
        return []
    if not isinstance(dados, list):
        return []
    fontes: list[dict[str, Any]] = []
    for item in dados:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("href") or "").strip()
        if not url:
            continue
        fontes.append({
            "url": url,
            "titulo": str(item.get("titulo") or item.get("title") or "(sem título)"),
            "trecho": str(item.get("trecho") or item.get("body") or item.get("content") or "").strip(),
        })
    return fontes


def _tokens(texto: str) -> list[str]:
    return re.findall(r"[a-z0-9áéíóúâêôãõçà-ú]+", texto.lower())


def _jaccard(a: str, b: str) -> float:
    """Sobreposição de shingles de 3 tokens (Jaccard) entre dois trechos."""
    def _shingles(tokens: list[str]) -> set[tuple[str, ...]]:
        return {tuple(tokens[i:i + 3]) for i in range(max(0, len(tokens) - 2))}

    sa, sb = _shingles(_tokens(a)), _shingles(_tokens(b))
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _nega_oposto(a: str, b: str) -> bool:
    """Um trecho nega e o outro não (contradição lexical)."""
    na, nb = bool(_NEGACOES.search(a)), bool(_NEGACOES.search(b))
    return na != nb


def classificar_afirmacoes(fontes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Classifica as afirmações por consulta: afirmado / divergencia / fonte_unica."""
    por_consulta: dict[str, list[dict[str, Any]]] = {}
    for f in fontes:
        por_consulta.setdefault(f.get("consulta") or "(sem consulta)", []).append(f)

    afirmacoes: list[dict[str, Any]] = []
    for consulta, grupo in por_consulta.items():
        urls = [f["url"] for f in grupo]
        if len(grupo) < 2:
            afirmacoes.append({"afirmacao": consulta, "urls": urls, "status": "fonte_unica"})
            continue

        # Consistência: existe par com sobreposição alta SEM negação oposta?
        # (len(grupo) >= 2 garante que melhor sempre recebe índices válidos)
        melhor: tuple[float, int, int] = (0.0, 0, 0)
        for i in range(len(grupo)):
            for j in range(i + 1, len(grupo)):
                sim = _jaccard(grupo[i]["trecho"], grupo[j]["trecho"])
                if sim > melhor[0]:
                    melhor = (sim, i, j)

        sim, i, j = melhor
        if sim >= _LIMIAR_CONSISTENCIA and not _nega_oposto(grupo[i]["trecho"], grupo[j]["trecho"]):
            afirmacoes.append({
                "afirmacao": consulta,
                "urls": [grupo[i]["url"], grupo[j]["url"]],
                "status": "afirmado",
            })
        else:
            # Divergência: as duas primeiras fontes da consulta citadas.
            afirmacoes.append({
                "afirmacao": consulta,
                "urls": urls[:2],
                "status": "divergencia",
            })
    return afirmacoes


def _montar_bloco(afirmacoes: list[dict[str, Any]]) -> str:
    """Bloco anexado à resposta final (auditável, em pt-BR)."""
    linhas = ["**Fontes verificadas (X3):**"]
    for a in afirmacoes:
        urls = ", ".join(a["urls"])
        if a["status"] == "afirmado":
            linhas.append(f"- {a['afirmacao']}: {urls}")
        elif a["status"] == "divergencia":
            linhas.append(
                f"- ⚠️ {a['afirmacao']}: fontes divergem — {a['urls'][0]} vs {a['urls'][1] if len(a['urls']) > 1 else ''}"
            )
        else:
            linhas.append(f"- {a['afirmacao']}: fonte única ({urls})")
    return "\n".join(linhas)


def turno_usou_busca_web(state: EstadoAegis | dict[str, Any]) -> bool:
    """True quando o turno executou `buscar_web` com sucesso (fact-check roda)."""
    return any(
        r.get("nome") == "buscar_web" and not r.get("erro")
        for r in (state.get("registros_ferramentas") or [])
    )


def no_fact_check(state: EstadoAegis | dict[str, Any]) -> dict[str, Any]:
    """X3: verifica afirmações contra as fontes e anexa o bloco à resposta.

    Sem fontes → `{}` (zero custo). Com fontes → grava `fontes` no estado
    (auditável) e anexa "Fontes verificadas" à última resposta do agente.
    """
    fontes = extrair_fontes(state.get("registros_ferramentas") or [])
    if not fontes:
        return {}
    afirmacoes = classificar_afirmacoes(fontes)
    if not afirmacoes:
        return {}

    atualizar: dict[str, Any] = {"fontes": afirmacoes}
    # Anexa o bloco à última AIMessage (substitui pelo mesmo id via add_messages)
    ultima_ai = next(
        (m for m in reversed(state.get("mensagens") or []) if getattr(m, "type", "") == "ai"),
        None,
    )
    if ultima_ai is not None:
        bloco = _montar_bloco(afirmacoes)
        if bloco not in (ultima_ai.content or ""):
            atualizar["mensagens"] = [
                ultima_ai.model_copy(update={"content": f"{ultima_ai.content}\n\n{bloco}"})
            ]
    return atualizar
