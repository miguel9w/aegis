"""
Contexto do projeto (AGENTS.md) — porta da leitura de arquivos de contexto do
Hermes Agent (Nous Research) para o Aegis.

O Hermes injeta no prompt de sistema as regras e convenções de um repositório
guardadas em `AGENTS.md` (e variantes `CLAUDE.md`/`.cursorrules`), para que o
agente opere dentro da convenção do projeto onde está rodando.

Aqui, o `sistema()` do Aegis anexa o conteúdo de `AGENTS.md` (ou o arquivo
apontado por `config.contexto_path`) como a seção "Contexto do projeto" —
desde que o arquivo exista e caiba em um limite para não estourar o prompt.
"""

from __future__ import annotations

from pathlib import Path

from .config import config

LIMITE_CONTEXTO = 4000  # chars (teto prudente para não inflar o prompt)


def ler_contexto(caminho: str | Path) -> str:
    """Lê um arquivo de contexto textual de forma segura.

    Retorna "" se o arquivo não existir, for ilegível ou ficar acima do limite.
    Nunca lança exceção — contexto é otimização, não pode quebrar o build do prompt.
    """
    try:
        caminho = Path(caminho)
        if not caminho.is_file():
            return ""
        texto = caminho.read_text(encoding="utf-8").strip()
        if not texto:
            return ""
        # corta no horizonte para não inflar o prompt indefinidamente
        return texto[:LIMITE_CONTEXTO]
    except Exception:  # noqa: BLE001 — nunca derruba a montagem do prompt
        return ""


def contexto_do_projeto() -> str:
    """Lê o contexto ativo do projeto conforme config.contexto_path."""
    return ler_contexto(config.contexto_path)