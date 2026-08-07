"""
Configuração por JSON — externaliza constantes hoje hardcoded nos módulos.

Cada arquivo `config/dados/<nome>.json` sobrescreve os padrões de código com
merge raso (`padroes ← json`). Se o arquivo não existir ou estiver inválido,
os padrões do código continuam valendo — nada quebra (fallback seguro).

Uso típico (no módulo que tem o hardcode):

    _CFG = carregar_config_json("limites.json", {"limite": 4000})
    LIMITE = _CFG["limite"]

O caminho pode ser sobrescrito por `AEGIS_*` em `config.py`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def carregar_config_json(
    nome_arquivo: str,
    padroes: dict[str, Any],
    caminho: str | Path | None = None,
) -> dict[str, Any]:
    """Carrega `nome_arquivo` de `config/dados/` e faz merge sobre `padroes`.

    - Arquivo ausente ou inválido → retorna os padrões (sem exceção).
    - Valores do JSON têm prioridade sobre os padrões (nível raso).
    """
    padroes = dict(padroes)
    try:
        if caminho is None:
            from .config import config
            alvo = Path(config.dados_dir) / nome_arquivo
        else:
            alvo = Path(caminho)
        if not alvo.is_file():
            return padroes
        with alvo.open(encoding="utf-8") as fh:
            dados = json.load(fh)
        if not isinstance(dados, dict):
            return padroes
        padroes.update(dados)
    except Exception:  # noqa: BLE001 — config nunca derruba o módulo
        return padroes
    return padroes