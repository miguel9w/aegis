"""
Sistema de Habilidades Auto-Evolutivo (padrão aberto agentskills.io).

Lê diretórios `extensions/skills/<nome>/SKILL.md` (frontmatter YAML: name, description;
corpo: instruções) e expõe cada habilidade como uma ferramenta `usar_skill:<nome>`.

O agente também pode **criar novas habilidades em runtime** através da
ferramenta `criar_skill`, que valida e grava um novo `SKILL.md`, tornando
o repositório de habilidades auto-evolutivo.
"""

from __future__ import annotations

import re
from pathlib import Path

from langchain_core.tools import StructuredTool, tool

# ---------------------------------------------------------------------
# Leitura de frontmatter YAML minimalista (sem dependência de pyyaml)
# ---------------------------------------------------------------------

_REG_SLUG = re.compile(r"[^a-z0-9_-]+")

_FRONT_PATTERN = re.compile(r"^---\s*\n(.*?)\n---\s*\n?", re.DOTALL)


def _slugificar(nome: str) -> str:
    return _REG_SLUG.sub("-", nome.strip().lower()).strip("-")


def _parsear_frontmatter(texto: str) -> tuple[dict, str]:
    """Extrai frontmatter (name/description) e o corpo do SKILL.md."""
    metadados: dict[str, str] = {}
    corpo = texto
    m = _FRONT_PATTERN.match(texto)
    if m:
        bloco = m.group(1)
        for linha in bloco.splitlines():
            if ":" in linha:
                chave, _, valor = linha.partition(":")
                metadados[chave.strip().lower()] = valor.strip().strip("\"'")
        corpo = texto[m.end():].strip()
    return metadados, corpo


# ---------------------------------------------------------------------
# Carregador de habilidades
# ---------------------------------------------------------------------

def carregar_skills(diretorio: str | Path) -> dict[str, dict]:
    """
    Varre `<diretorio>/**/SKILL.md` e retorna
    {nome_registrado: {"descricao", "conteudo", "caminho"}}.
    """
    base = Path(diretorio)
    habilidades: dict[str, dict] = {}
    if not base.is_dir():
        return habilidades

    for sk in sorted(base.rglob("SKILL.md")):
        try:
            texto = sk.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, corpo = _parsear_frontmatter(texto)
        nome = _slugificar(meta.get("name") or sk.parent.name)
        if not nome:
            continue
        habilidades[nome] = {
            "descricao": meta.get("description") or f"Habilidade {nome}.",
            "conteudo": corpo or texto,
            "caminho": str(sk),
        }
    return habilidades


def criar_skill_path(diretorio: str | Path, nome: str, descricao: str, conteudo: str) -> Path:
    """Valida e grava uma habilidade no padrão agentskills.io. Retorna o caminho."""
    slug = _slugificar(nome)
    if not slug:
        raise ValueError("nome de habilidade inválido")
    destino = Path(diretorio) / slug / "SKILL.md"
    destino.parent.mkdir(parents=True, exist_ok=True)
    sk = (
        "---\n"
        f"name: {slug}\n"
        f"description: {descricao or f'Habilidade {slug}'}\n"
        "---\n\n"
        f"{conteudo}\n"
    )
    destino.write_text(sk, encoding="utf-8")
    return destino


# ---------------------------------------------------------------------
# Exposição como ferramentas
# ---------------------------------------------------------------------

def ferramentas_skills(habilidades: dict[str, dict]) -> list:
    """Cria ferramentas `usar_skill:<nome>` para cada habilidade carregada."""
    resultado: list = []

    def _fazer(nome: str, descricao: str, conteudo: str):
        def _ler() -> str:
            """Carrega o conteúdo de uma habilidade registrada para guiar a execução."""
            return f"# Habilidade: {nome}\n\n{conteudo}"

        # StructuredTool.from_function aceita nome arbitrário (o decorator @tool
        # exige função pré-definida e rejeita o kwarg `name` em langchain-core 1.x).
        return StructuredTool.from_function(
            func=_ler,
            name=f"usar_skill:{nome}",
            description=f"{descricao} [habilidade registrada: {nome}]",
        )

    for nome in sorted(habilidades):
        info = habilidades[nome]
        resultado.append(_fazer(nome, info["descricao"], info["conteudo"]))

    @tool
    def criar_skill(nome: str, descricao: str, conteudo: str) -> str:
        """
        Cria ou atualiza uma habilidade no repositório extensions/skills/ (padrão
        agentskills.io). Valida e grava um SKILL.md; vale na próxima execução.
        """
        from ..config import config

        try:
            destino = criar_skill_path(config.skills_dir, nome, descricao, conteudo)
        except ValueError as exc:
            return f"ERRO_FERRAMENTA: {exc}"
        except OSError as exc:
            return f"ERRO_FERRAMENTA: não foi possível gravar habilidade: {exc}"
        # Recarrega o registro em memória para efeito imediato
        novas = carregar_skills(config.skills_dir)
        HABILIDADES_REGISTRADAS.clear()
        HABILIDADES_REGISTRADAS.update(novas)
        return f"Habilidade '{destino.stem}' salva em {destino} e recarregada com sucesso."

    resultado.append(criar_skill)
    return resultado


# Registro global (preenchido na inicialização, atualizado por criar_skill)
HABILIDADES_REGISTRADAS: dict[str, dict] = {}


def carregar_e_expor(diretorio: str | Path) -> list:
    """Le as habilidades e devolve as ferramentas correspondentes."""
    global HABILIDADES_REGISTRADAS
    HABILIDADES_REGISTRADAS = carregar_skills(diretorio)
    return ferramentas_skills(HABILIDADES_REGISTRADAS)