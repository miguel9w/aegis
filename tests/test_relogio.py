"""Testes da ferramenta relogio (relógio mundial com múltiplos fusos IANA)."""

from __future__ import annotations

from aegis.ferramentas.relogio import FUSO_PADRAO, relogio


def test_relogio_fuso_padrao():
    saida = relogio.invoke({"fusos": FUSO_PADRAO})
    assert saida.endswith(f"({FUSO_PADRAO})")
    assert "/" in saida  # data no formato dd/mm/aaaa


def test_relogio_multiplos_fusos():
    saida = relogio.invoke({"fusos": "America/Sao_Paulo, UTC, Europe/Lisbon"})
    linhas = saida.splitlines()
    assert len(linhas) == 3
    assert linhas[0].endswith("(America/Sao_Paulo)")
    assert linhas[1].endswith("(UTC)")
    assert linhas[2].endswith("(Europe/Lisbon)")


def test_relogio_ignora_espacos():
    saida = relogio.invoke({"fusos": " UTC , America/Sao_Paulo "})
    linhas = saida.splitlines()
    assert len(linhas) == 2
    assert linhas[0].endswith("(UTC)")
    assert linhas[1].endswith("(America/Sao_Paulo)")


def test_relogio_fusos_vazios_usa_padrao():
    saida = relogio.invoke({"fusos": "  ,  "})
    assert saida.endswith(f"({FUSO_PADRAO})")


def test_relogio_fuso_invalido():
    saida = relogio.invoke({"fusos": "Zona/Inexistente"})
    assert saida.startswith("ERRO_FERRAMENTA:")


def test_relogio_mistura_valido_e_invalido_falha_rapido():
    saida = relogio.invoke({"fusos": "UTC, Zona/Inexistente"})
    assert saida.startswith("ERRO_FERRAMENTA:")
