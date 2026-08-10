"""Autorizações de comandos da web UI (janela de perguntas)."""

from aegis.autorizacoes import (
    aprovar_comando, aprovados, comando_aprovado, limpar,
)


def test_aprovar_e_verificar():
    limpar()
    assert not comando_aprovado("touch x.txt")
    assert aprovar_comando("touch x.txt")
    assert comando_aprovado("touch x.txt")
    assert "touch x.txt" in aprovados()


def test_aprovar_vazio_falha():
    limpar()
    assert not aprovar_comando("")
    assert not aprovar_comando("   ")


def test_aprovacao_e_exata():
    limpar()
    aprovar_comando("git status")
    # comando parecido NÃO é aprovado (apenas o exato)
    assert not comando_aprovado("git status --porcelain")
    assert not comando_aprovado(" git status")
