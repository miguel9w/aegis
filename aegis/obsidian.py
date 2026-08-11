"""
Banco estilo Obsidian — vault de notas Markdown com `[[wikilinks]]`.

NÃO é um banco relacional: é um diretório de arquivos `.md` (subpastas,
tags `#tag`, wikilinks bidirecionais), espelhando o formato do Obsidian.
O índice `indice.json` é sempre recalculado dos próprios arquivos (nunca
fica dessincronizado).

Raiz padrão: `config/dados/obsidian/` (env `AEGIS_OBSIDIAN_DIR`).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from .config import config

_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_TAG = re.compile(r"(?:^|\s)#([A-Za-z0-9_/\-]+)")


# --------------------------------------------------------------------------
# Núcleo (funções puras — recebem o caminho do vault)
# --------------------------------------------------------------------------

def extrair_links(texto: str) -> list[str]:
    """Destinos dos [[wikilinks]] (alias após '|' é descartado)."""
    vistos: list[str] = []
    for alvo in _WIKILINK.findall(texto or ""):
        chave = alvo.split("|", 1)[0].strip()
        if chave and chave not in vistos:
            vistos.append(chave)
    return vistos


def extrair_tags(texto: str) -> list[str]:
    """Tags `#tag` (sem duplicatas, fora de links)."""
    vistos: list[str] = []
    for tag in _TAG.findall(texto or ""):
        if tag not in vistos:
            vistos.append(tag)
    return vistos


def _notas_no_vault(vault: Path) -> dict[str, Path]:
    """{nome_da_nota: caminho} — varre todos os .md do vault (recursivo)."""
    if not vault.is_dir():
        return {}
    mapa: dict[str, Path] = {}
    for arquivo in sorted(vault.rglob("*.md")):
        rel = arquivo.relative_to(vault)
        mapa[str(rel.with_suffix(""))] = arquivo
    return mapa


def _conteudo_nota(arquivo: Path) -> str:
    try:
        return arquivo.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return ""


def _titulo(texto: str, padrao: str) -> str:
    """Título exibido: primeiro '# Título' do arquivo, senão o nome base."""
    for linha in (texto or "").splitlines():
        if linha.startswith("# "):
            return linha[2:].strip()
    return padrao


def recalcular_indice(vault: Path) -> dict[str, Any]:
    """Índice: por nota — links emitidos, tags e backlinks (derivados)."""
    notas = _notas_no_vault(vault)
    links: dict[str, list[str]] = {}
    tags: dict[str, list[str]] = {}
    for nome, arquivo in notas.items():
        texto = _conteudo_nota(arquivo)
        links[nome] = extrair_links(texto)
        tags[nome] = extrair_tags(texto)
    backlinks: dict[str, list[str]] = {nome: [] for nome in notas}
    for origem, destinos in links.items():
        for alvo in destinos:
            if alvo in backlinks and origem not in backlinks[alvo]:
                backlinks[alvo].append(origem)
    return {
        "notas": {
            nome: {
                "arquivo": str(arquivo.relative_to(vault)),
                "titulo": _titulo(_conteudo_nota(arquivo), nome),
                "links": sorted(links[nome]),
                "tags": sorted(tags[nome]),
                "backlinks": sorted(backlinks[nome]),
            }
            for nome, arquivo in notas.items()
        }
    }


def _carregar_indice(vault: Path) -> dict[str, Any]:
    """Lê indice.json se possível; senão recalcula (nunca fica obsoleto)."""
    alvo = vault / "indice.json"
    if alvo.is_file():
        try:
            return json.loads(alvo.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            pass
    return recalcular_indice(vault)


def _salvar_indice(vault: Path) -> None:
    vault.mkdir(parents=True, exist_ok=True)
    (vault / "indice.json").write_text(
        json.dumps(recalcular_indice(vault), ensure_ascii=False, indent=2),
        encoding="utf-8")


def _nome_arquivo(nome: str) -> str:
    """Nome amigável → nome de arquivo seguro (espaços viram _, sem barras)."""
    return re.sub(r"[\\/:*?\"<>|]", "_", nome).strip().replace(" ", "_")


def _caminho_nota(nome: str, vault: Path) -> Path | None:
    """Localiza em qualquer subpasta (nome exato ou nome de arquivo seguro)."""
    notas = _notas_no_vault(vault)
    if nome in notas:
        return notas[nome]
    alvo = _nome_arquivo(nome)
    for rel, arquivo in notas.items():
        if rel.rsplit("/", 1)[-1] == alvo:
            return arquivo
    return None


def _escrever(nome: str, pasta: str, conteudo: str, vault: Path) -> Path:
    alvo = vault / (Path(pasta) if pasta else Path(".")) / (_nome_arquivo(nome) + ".md")
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text(str(conteudo or "").strip() + "\n", encoding="utf-8")
    _salvar_indice(vault)
    return alvo


# --------------------------------------------------------------------------
# Operações (lêem o vault do config)
# --------------------------------------------------------------------------

def criar_nota_obsidian(nome: str, conteudo: str, pasta: str = "") -> str:
    vault = Path(config.obsidian_dir)
    if _caminho_nota(nome, vault) is not None:
        raise ValueError(f"a nota '{nome}' já existe no vault")
    if not conteudo.lstrip().startswith("#"):
        conteudo = f"# {nome}\n\n{conteudo}"  # H1 vira o título exibido (índice)
    alvo = _escrever(nome, pasta, conteudo, vault)
    return f"Nota criada: {alvo}"


def ler_nota_obsidian(nome: str) -> str:
    vault = Path(config.obsidian_dir)
    alvo = _caminho_nota(nome, vault)
    if alvo is None:
        raise ValueError(f"nota '{nome}' não encontrada no vault")
    return f"# {nome}\n\n{_conteudo_nota(alvo).strip()}"


def ligar_nota_obsidian(de: str, para: str) -> str:
    vault = Path(config.obsidian_dir)
    alvo = _caminho_nota(de, vault)
    if alvo is None:
        raise ValueError(f"nota '{de}' não encontrada no vault")
    if _caminho_nota(para, vault) is None:
        raise ValueError(f"destino '{para}' não existe no vault — crie-o antes")
    link = f"[[{para}]]"
    texto = _conteudo_nota(alvo)
    if link not in texto:
        alvo.write_text(texto.rstrip() + "\n\n" + link + "\n", encoding="utf-8")
        _salvar_indice(vault)
    return f"⬅ {de} → {para} linkado"


def buscar_nota_obsidian(palavra: str) -> str:
    vault = Path(config.obsidian_dir)
    indice = _carregar_indice(vault).get("notas", {})
    achados: list[str] = []
    for nome, arquivo in sorted(_notas_no_vault(vault).items()):
        texto = _conteudo_nota(arquivo)
        if palavra.lower() in nome.lower() or palavra.lower() in texto.lower():
            pos = texto.lower().find(palavra.lower())
            trecho = texto[max(0, pos - 40): pos + 60].replace("\n", " ")
            titulo = indice.get(nome, {}).get("titulo", nome)
            achados.append(f"- {titulo}: …{trecho}…")
    return "\n".join(achados) if achados else "(nenhuma nota encontrada)"


def notas_por_tag_obsidian(tag: str) -> str:
    vault = Path(config.obsidian_dir)
    tag = tag.lstrip("#")
    indice = _carregar_indice(vault).get("notas", {})
    achadas = [nome for nome, meta in indice.items() if tag in meta.get("tags", [])]
    if not achadas:
        return f"(nenhuma nota com a tag #{tag})"
    linhas = [f"Notas com #{tag}:"]
    for nome in sorted(achadas):
        titulo = indice.get(nome, {}).get("titulo", nome)
        linhas.append(f"- {titulo}")
    return "\n".join(linhas)


def notas_conectadas_obsidian(nome: str) -> str:
    vault = Path(config.obsidian_dir)
    indice = _carregar_indice(vault).get("notas", {})
    meta = indice.get(nome)
    if meta is None:
        raise ValueError(f"nota '{nome}' não encontrada no vault")
    linhas = [f"🌐 {nome}"]
    linhas.append("  sai para: " + (", ".join(meta.get("links", [])) or "(nenhum)"))
    linhas.append("  recebe de: " + (", ".join(meta.get("backlinks", [])) or "(nenhum)"))
    linhas.append("  tags: " + (", ".join(meta.get("tags", [])) or "(nenhuma)"))
    return "\n".join(linhas)


def listar_obsidian_vault() -> str:
    vault = Path(config.obsidian_dir)
    indice = _carregar_indice(vault).get("notas", {})
    if not indice:
        return "(vault vazio — use criar_nota para começar)"
    por_pasta: dict[str, list[tuple[str, Any]]] = {}
    for nome, meta in sorted(indice.items()):
        pasta = str(Path(meta["arquivo"]).parent)
        por_pasta.setdefault(pasta, []).append((nome, meta))
    linhas = ["🏛 Vault Obsidian"]
    for pasta, itens in por_pasta.items():
        linhas.append(f"📁 {pasta}/")
        for nome, meta in itens:
            back = len(meta.get("backlinks", []))
            linhas.append(f"   - {nome} ({back} backlink{'s' if back != 1 else ''})")
    return "\n".join(linhas)


def limpar_vault(confirmar: bool = False) -> str:
    """Apaga as notas do vault (exige confirmar=True)."""
    if not confirmar:
        raise ValueError("operação destrutiva — chame com confirmar=True")
    vault = Path(config.obsidian_dir)
    if vault.is_dir():
        for item in vault.rglob("*"):
            if item.is_file() and item.name != "indice.json":
                item.unlink()
        _salvar_indice(vault)
    return "Vault limpo."


# --------------------------------------------------------------------------
# Ferramentas (wrappers @tool para o agente)
# --------------------------------------------------------------------------

@tool
def criar_nota(nome: str, conteudo: str, pasta: str = "") -> str:
    """Cria uma nota markdown no vault Obsidian do Aegis."""
    return criar_nota_obsidian(nome, conteudo, pasta)


@tool
def ler_nota(nome: str) -> str:
    """Lê o conteúdo de uma nota do vault Obsidian."""
    # C5: conteúdo de nota é DADO não confiável — marcado com a classificação
    from .seguranca import marcar_conteudo
    return marcar_conteudo(ler_nota_obsidian(nome), fonte=f"nota: {nome}")


@tool
def ligar_nota(de: str, para: str) -> str:
    """Cria um [[wikilink]] bidirecional entre duas notas do vault."""
    return ligar_nota_obsidian(de, para)


@tool
def buscar_notas(palavra: str) -> str:
    """Busca full-text no vault Obsidian e lista as notas que contêm a palavra."""
    # C5: trechos de notas são DADO não confiável — marcados com a classificação
    from .seguranca import marcar_conteudo
    return marcar_conteudo(buscar_nota_obsidian(palavra), fonte=f"busca: {palavra}")


@tool
def notas_por_tag(tag: str) -> str:
    """Lista as notas do vault que têm uma determinada tag (#tag)."""
    return notas_por_tag_obsidian(tag)


@tool
def notas_conectadas(nome: str) -> str:
    """Mostra o grafo local da nota: links emitidos, backlinks e tags."""
    return notas_conectadas_obsidian(nome)


@tool
def listar_obsidian() -> str:
    """Lista todas as notas do vault Obsidian em árvore por subpasta."""
    return listar_obsidian_vault()


@tool
def limpar_obsidian(confirmar: bool) -> str:
    """Apaga todas as notas do vault Obsidian (exige confirmar=True)."""
    return limpar_vault(confirmar)