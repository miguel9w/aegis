"""
Sistema de Habilidades Auto-Evolutivo (padrão aberto agentskills.io).

Lê diretórios `extensions/skills/<nome>/SKILL.md` (frontmatter YAML: name,
description, gatilho; corpo: instruções) e expõe UMA ferramenta
`carregar_skill` (X2): o agente vê o catálogo (nome + descrição + gatilho) e
carrega o corpo da skill escolhida no contexto, respeitando o teto de tokens
(`AEGIS_SKILL_TETO_TOKENS`). Skills novas ficam disponíveis sem reiniciar
(varredura por diretório no carregamento); frontmatter inválido é ignorado
com aviso — nunca quebra o grafo.

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
    """Extrai frontmatter (name/description/gatilho) e o corpo do SKILL.md."""
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

def carregar_skills(diretorio: str | Path, avisos: list[str] | None = None) -> dict[str, dict]:
    """
    Varre `<diretorio>/**/SKILL.md` e retorna
    {nome_registrado: {"descricao", "gatilho", "conteudo", "caminho"}}.

    X2: skill com frontmatter inválido é ignorada e o problema é anotado em
    `avisos` (quando fornecido) — a varredura nunca levanta exceção.
    """
    base = Path(diretorio)
    habilidades: dict[str, dict] = {}
    if not base.is_dir():
        return habilidades

    for sk in sorted(base.rglob("SKILL.md")):
        try:
            texto = sk.read_text(encoding="utf-8")
        except OSError as exc:
            if avisos is not None:
                avisos.append(f"{sk}: não foi possível ler ({exc})")
            continue
        if texto.lstrip().startswith("---") and not _FRONT_PATTERN.match(texto):
            if avisos is not None:
                avisos.append(f"{sk}: frontmatter inválido (bloco '---' sem fechamento) — ignorada")
            continue
        meta, corpo = _parsear_frontmatter(texto)
        nome = _slugificar(meta.get("name") or sk.parent.name)
        if not nome:
            if avisos is not None:
                avisos.append(f"{sk}: sem nome válido no frontmatter — ignorada")
            continue
        habilidades[nome] = {
            "descricao": meta.get("description") or f"Habilidade {nome}.",
            "gatilho": meta.get("gatilho") or "",
            "conteudo": corpo or texto,
            "caminho": str(sk),
        }
    return habilidades


def criar_skill_path(
    diretorio: str | Path, nome: str, descricao: str, conteudo: str, gatilho: str = ""
) -> Path:
    """Valida e grava uma habilidade no padrão agentskills.io. Retorna o caminho."""
    slug = _slugificar(nome)
    if not slug:
        raise ValueError("nome de habilidade inválido")
    destino = Path(diretorio) / slug / "SKILL.md"
    destino.parent.mkdir(parents=True, exist_ok=True)
    linhas_fm = ["---", f"name: {slug}", f"description: {descricao or f'Habilidade {slug}'}"]
    if gatilho:
        linhas_fm.append(f"gatilho: {gatilho}")
    linhas_fm.append("---")
    sk = "\n".join(linhas_fm) + "\n\n" + f"{conteudo}\n"
    destino.write_text(sk, encoding="utf-8")
    return destino


# ---------------------------------------------------------------------
# Exposição como ferramentas
# ---------------------------------------------------------------------

def _estimar_tokens(texto: str) -> int:
    """Heurística simples: ~4 caracteres por token (média pt-BR/inglês)."""
    return max(1, len(texto) // 4)


def _truncar_por_teto(texto: str, teto_tokens: int) -> tuple[str, bool]:
    """Corta o texto no teto de tokens (heurística); True se truncou."""
    if teto_tokens <= 0 or _estimar_tokens(texto) <= teto_tokens:
        return texto, False
    limite_chars = teto_tokens * 4
    corte = texto[:limite_chars]
    ultimo = corte.rfind(" ")
    if ultimo > limite_chars // 2:
        corte = corte[:ultimo]
    return corte.rstrip() + "\n…(truncado pelo teto de tokens)", True


def _formato_catalogo(habilidades: dict[str, dict]) -> str:
    """Lista as skills registradas: nome — descrição [gatilho]."""
    linhas = []
    for nome in sorted(habilidades):
        info = habilidades[nome]
        gatilho = f" (gatilho: {info['gatilho']})" if info.get("gatilho") else ""
        linhas.append(f"- {nome}: {info['descricao']}{gatilho}")
    if not linhas:
        return "(nenhuma skill registrada)"
    return "\n".join(linhas)


def ferramentas_skills(habilidades: dict[str, dict]) -> list:
    """Cria as ferramentas de skill: `carregar_skill` + `criar_skill` (X2)."""

    def _carregar(nome: str = "") -> str:
        from .config import config

        if not nome.strip():
            return (
                "Skills disponíveis (chame carregar_skill com o nome para injetar "
                "o corpo no contexto):\n"
                + _formato_catalogo(habilidades)
            )
        slug = _slugificar(nome)
        info = habilidades.get(slug)
        if info is None:
            return (
                f"Não encontrei a skill '{nome}'. Skills disponíveis:\n"
                + _formato_catalogo(habilidades)
            )
        corpo, truncou = _truncar_por_teto(info["conteudo"], config.skill_teto_tokens)
        cabecalho = f"# Habilidade: {slug}\n\n{corpo}"
        if truncou:
            cabecalho += (
                f"\n\n(aviso: skill truncada para {config.skill_teto_tokens} tokens)"
            )
        return cabecalho

    carregar_skill = StructuredTool.from_function(
        func=_carregar,
        name="carregar_skill",
        description=(
            "Lista as habilidades (skills) registradas em extensions/skills/ ou carrega o "
            "corpo de uma delas no contexto. Sem argumento, devolve o catálogo (nome, "
            "descrição, gatilho) — use para decidir qual carregar. Com 'nome', injeta as "
            "instruções da skill (respeitando o teto de tokens). Skills contêm procedimentos "
            "reutilizáveis que o agente deve SEGUIR quando a tarefa casa com a descrição/gatilho."
        ),
    )

    @tool
    def criar_skill(nome: str, descricao: str, conteudo: str, gatilho: str = "") -> str:
        """
        Cria ou atualiza uma habilidade no repositório extensions/skills/ (padrão
        agentskills.io). Valida e grava um SKILL.md; vale na próxima execução.
        """
        from .config import config

        try:
            destino = criar_skill_path(config.skills_dir, nome, descricao, conteudo, gatilho)
        except ValueError as exc:
            return f"ERRO_FERRAMENTA: {exc}"
        except OSError as exc:
            return f"ERRO_FERRAMENTA: não foi possível gravar habilidade: {exc}"
        # Recarrega o registro em memória para efeito imediato
        novas = carregar_skills(config.skills_dir)
        HABILIDADES_REGISTRADAS.clear()
        HABILIDADES_REGISTRADAS.update(novas)
        return f"Habilidade '{destino.stem}' salva em {destino} e recarregada com sucesso."

    return [carregar_skill, criar_skill]


# Registro global (preenchido na inicialização, atualizado por criar_skill)
HABILIDADES_REGISTRADAS: dict[str, dict] = {}


def carregar_e_expor(diretorio: str | Path) -> list:
    """Le as habilidades e devolve as ferramentas correspondentes."""
    global HABILIDADES_REGISTRADAS
    HABILIDADES_REGISTRADAS = carregar_skills(diretorio)
    return ferramentas_skills(HABILIDADES_REGISTRADAS)