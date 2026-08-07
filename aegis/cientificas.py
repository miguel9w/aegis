"""
Features científicas — busca na API do arXiv, BibTeX, citações APA.

  - `buscar_papers`: consulta a API do arXiv (HTTP; falha de rede → []).
  - `_parsear_arxiv_xml`: parse puro do feed Atom (sem rede) — testável.
  - `gerar_bibtex` / `citar_apa`: saída determinística (sem LLM).
  - `buscar_paper_por_id`: retorna UM paper pela API (id_list).
  - `salvar_paper`: grava em `config/dados/biblioteca.json` (dedupe por id)
    e cria nota de leitura no vault Obsidian (lazy).

Limite de resultados: `AEGIS_ARXIV_MAX_RESULTADOS` (padrão 5).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import requests
from langchain_core.tools import tool

from .config import config

ARXIV_API_URL = "https://export.arxiv.org/api/query"

# Namespaces do feed Atom do arXiv (xml.etree)
_NS_ATOM = "{http://www.w3.org/2005/Atom}"


def _extrair_arxiv_id(url: str) -> str:
    """'http://arxiv.org/abs/2401.12345v2' → '2401.12345v2'."""
    base = (url or "").rstrip("/").rsplit("/", 1)[-1]
    return base


def _normalizar_paper(entry) -> dict[str, Any]:
    """Constrói o dict normalizado a partir de um <entry> do Atom."""

    def _texto(tag: str) -> str:
        no = entry.find(f"{_NS_ATOM}{tag}")
        return (no.text or "").strip() if no is not None else ""

    id_url = _texto("id")
    link_pdf = ""
    for link in entry.findall(f"{_NS_ATOM}link"):
        if link.get("title") == "pdf":
            link_pdf = link.get("href", "")
    autores = []
    for autor in entry.findall(f"{_NS_ATOM}author"):
        for nome in autor.findall(f"{_NS_ATOM}name"):
            if nome is not None and nome.text and nome.text.strip():
                autores.append(nome.text.strip())
    return {
        "id": _extrair_arxiv_id(id_url),
        "url": id_url,
        "pdf": link_pdf,
        "titulo": _texto("title").replace("\n", " ").strip(),
        "resumo": _texto("summary").replace("\n", " ").strip(),
        "autores": autores,
        "publicado": _texto("published")[:10],
        "comentario": _texto("comment"),
    }


def parsear_arxiv_xml(texto: str) -> list[dict[str, Any]]:
    """Faz parse do feed Atom do arXiv — função pura (sem rede)."""
    import xml.etree.ElementTree as ET

    try:
        raiz = ET.fromstring(texto)
    except Exception:  # noqa: BLE001 — XML malformado vira lista vazia
        return []
    return [_normalizar_paper(e) for e in raiz.findall(f"{_NS_ATOM}entry")]


def _buscar_api(params: dict[str, Any], timeout: int = 20) -> list[dict[str, Any]]:
    try:
        resp = requests.get(ARXIV_API_URL, params=params, timeout=timeout)
        resp.raise_for_status()
        return parsear_arxiv_xml(resp.text)
    except Exception:  # noqa: BLE001 — rede falha nunca derruba o agente
        return []


def buscar_papers(consulta: str, n: int | None = None) -> list[dict[str, Any]]:
    """Busca `n` papers por consulta (all:)."""
    n = n if n is not None else config.arxiv_max_resultados
    return _buscar_api({"search_query": f"all:{consulta}", "max_results": n})


def buscar_paper_por_id(id_arxiv: str) -> dict[str, Any] | None:
    """Busca um único paper pelo id (ex.: '2401.12345v2')."""
    papers = _buscar_api({"id_list": id_arxiv, "max_results": 1})
    return papers[0] if papers else None


def gerar_bibtex(paper: dict[str, Any]) -> str:
    """Entrada BibTeX determinística."""
    autores = paper.get("autores") or []
    sobrenome = autores[0].split()[-1] if autores else "anonimo"
    ano = re.search(r"(\d{4})", str(paper.get("publicado") or ""))
    ano_texto = ano.group(1) if ano else "????"
    chave = sobrenome + ano_texto if sobrenome != "anonimo" else "anonimo"
    author_id = " and ".join(autores) if autores else "anonimo"
    return (
        f"@article{{{chave},\n"
        f"  title = {{{paper.get('titulo', '')}}},\n"
        f"  author = {{{author_id}}},\n"
        f"  year = {{{ano_texto}}},\n"
        f"  journal = {{arXiv preprint arXiv:{paper.get('id', '')}}},\n"
        f"  eprint = {{{paper.get('id', '')}}},\n"
        f"  url = {{{paper.get('url', '')}}}\n"
        f"}}"
    )


def citar_apa(paper: dict[str, Any]) -> str:
    """Citação APA 7 simplificada (determinística)."""
    autores = paper.get("autores") or []
    ano = re.search(r"(\d{4})", str(paper.get("publicado") or ""))
    ano_texto = ano.group(1) if ano else "s.d."
    if not autores:
        parte = f"Anônimo. ({ano_texto})."
    elif len(autores) == 1:
        partes = autores[0].split()
        sobrenome = partes[-1]
        iniciais = " ".join(p[0] + "." for p in partes[:-1])
        parte = f"{sobrenome}, {iniciais} ({ano_texto})."
    else:
        partes = autores[0].split()
        sobrenome = partes[-1]
        iniciais = " ".join(p[0] + "." for p in partes[:-1])
        parte = f"{sobrenome}, {iniciais} et al. ({ano_texto})."
    return f"{parte} {paper.get('titulo', '')}. arXiv preprint arXiv:{paper.get('id', '')}."


def _biblioteca() -> list[dict[str, Any]]:
    try:
        alvo = Path(config.biblioteca_path)
        if not alvo.is_file():
            return []
        with alvo.open(encoding="utf-8") as fh:
            dados = json.load(fh)
        return dados if isinstance(dados, list) else []
    except Exception:  # noqa: BLE001
        return []


def _salvar_paper_biblioteca(paper: dict[str, Any]) -> bool:
    """Adiciona o paper à biblioteca (dedupe por id). Retorna True se novo."""
    alvo = Path(config.biblioteca_path)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    dados = _biblioteca()
    if any(p.get("id") == paper.get("id") for p in dados):
        return False
    dados.append(paper)
    alvo.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def _formata_nota_leitura(paper: dict[str, Any]) -> str:
    autores = ", ".join(paper.get("autores", [])) or "(sem autores)"
    return (
        f"# {paper.get('titulo', '')}\n\n"
        f"- **autores**: {autores}\n"
        f"- **publicado**: {paper.get('publicado', '')}\n"
        f"- **id**: {paper.get('id', '')}\n"
        f"- **url**: {paper.get('url', '')}\n\n"
        f"{paper.get('resumo', '')}"
    )


# --------------------------------------------------------------------------
# Ferramentas — registradas em aegis/ferramentas/__init__.py
# --------------------------------------------------------------------------

@tool
def buscar_papers_arxiv(consulta: str, n: int | None = None) -> str:
    """Busca papers na API do arXiv por consulta e lista título/autores/url."""
    papers = buscar_papers(consulta, n)
    if not papers:
        return "(nenhum resultado do arXiv — verifique a consulta ou a rede)"
    linhas = []
    for p in papers:
        autores = ", ".join(p.get("autores", [])[:3]) or "—"
        linhas.append(f"- **{p.get('titulo')}** ({p.get('publicado')}) — {autores}")
        linhas.append(f"  `{p.get('id')}` · {p.get('url')}")
    return "\n".join(linhas)


@tool
def gerar_citacao_bibtex(id_arxiv: str) -> str:
    """Gera a entrada BibTeX de um paper já salvo na biblioteca (use salvar_paper antes)."""
    paper = next((p for p in _biblioteca() if p.get("id") == id_arxiv), None)
    if not paper:
        raise ValueError(
            f"paper '{id_arxiv}' não está na biblioteca — use salvar_paper antes")
    return "```bibtex\n" + gerar_bibtex(paper) + "\n```"


@tool
def salvar_paper(id_arxiv: str) -> str:
    """Salva um paper (por id) na biblioteca e cria uma nota de leitura no vault."""
    id_limpo = (id_arxiv or "").strip()
    if not id_limpo:
        raise ValueError("id_arxiv é obrigatório")
    paper = next((p for p in _biblioteca() if p.get("id") == id_limpo), None)
    if paper is None:
        papel = buscar_paper_por_id(id_limpo)
        if papel is None:
            raise ValueError(
                f"paper '{id_limpo}' não encontrado no arXiv (rede?) — confira o id")
        paper = papel
    novo = _salvar_paper_biblioteca(paper)
    try:  # vault obsidian é opcional (lazy import evita ciclo)
        from .obsidian import criar_nota_obsidian
        criar_nota_obsidian(f"Paper {paper['id']}", _formata_nota_leitura(paper), "papers")
    except Exception:  # noqa: BLE001
        pass
    status = "salvo (novo)" if novo else "já estava na biblioteca"
    return f"Paper {status}: {paper['titulo']}\n\n{citar_apa(paper)}\n\n{gerar_bibtex(paper)}"


@tool
def revisar_literatura(consulta: str, n: int | None = None) -> str:
    """Busca o arXiv e monta uma revisão de literatura com citações APA e BibTeX."""
    papers = buscar_papers(consulta, n)
    if not papers:
        return "(nenhum resultado do arXiv)"
    linhas = [f"### Revisão de literatura — {consulta}"]
    for p in papers:
        linhas.append(f"- **{p.get('titulo')}** ({p.get('publicado')})")
        linhas.append(f"  {citar_apa(p)}")
        linhas.append(f"  BibTeX: `{gerar_bibtex(p).splitlines()[0]}`")
    return "\n".join(linhas)