"""
Ferramenta `relogio` — relógio mundial (data/hora atuais em um ou mais fusos IANA).

Complementa `hora_atual` permitindo consultar VÁRIOS fusos de uma vez
(separados por vírgula), útil para equipes distribuídas e agendamentos
internacionais. Validada com `@tool` (langchain) seguindo o padrão do pacote.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.tools import tool

FUSO_PADRAO = "America/Sao_Paulo"


@tool
def relogio(fusos: str = FUSO_PADRAO) -> str:
    """Mostra a data e hora atuais em um ou mais fusos horários IANA
    (separados por vírgula), como um relógio mundial.

    Args:
        fusos: um ou mais fusos IANA separados por vírgula
            (ex.: "America/Sao_Paulo, UTC, Europe/Lisbon").
    """
    lista = [f.strip() for f in fusos.split(",") if f.strip()] or [FUSO_PADRAO]
    zonas: list[ZoneInfo] = []
    for f in lista:
        try:
            zonas.append(ZoneInfo(f))
        except ZoneInfoNotFoundError:
            return f"ERRO_FERRAMENTA: fuso horário inválido: {f!r}. Use um nome IANA."
    linhas = [f"{datetime.now(z):%d/%m/%Y %H:%M:%S} ({f})" for z, f in zip(zonas, lista)]
    return "\n".join(linhas)
