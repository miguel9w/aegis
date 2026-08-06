"""Testes do contexto do projeto (paridade Hermes AGENTS.md/CLAUDE.md)."""

import pytest

from aegis.contexto import LIMITE_CONTEXTO, ler_contexto
from aegis.prompts import sistema


def test_arquivo_inexistente_retorna_vazio(tmp_path):
    assert ler_contexto(tmp_path / "nao_existe.md") == ""


def test_arquivo_vazio_retorna_vazio(tmp_path):
    arquivo = tmp_path / "AGENTS.md"
    arquivo.write_text("   \n  ", encoding="utf-8")
    assert ler_contexto(arquivo) == ""


def test_le_conteudo(tmp_path):
    arquivo = tmp_path / "AGENTS.md"
    arquivo.write_text("Use pytest. Não use pip.", encoding="utf-8")
    assert ler_contexto(arquivo) == "Use pytest. Não use pip."


def test_trunca_no_limite(tmp_path):
    arquivo = tmp_path / "AGENTS.md"
    arquivo.write_text("x" * (LIMITE_CONTEXTO + 500), encoding="utf-8")
    assert len(ler_contexto(arquivo)) == LIMITE_CONTEXTO


def test_sistema_injeta_contexto(monkeypatch):
    def fake():
        return "REGRAS DO REPO: pt-BR, TDD, commits por feature."
    monkeypatch.setattr("aegis.contexto.contexto_do_projeto", fake)
    saida = sistema({}, "", [])
    assert "## Contexto do projeto" in saida
    assert "REGRAS DO REPO" in saida


def test_sistema_sem_contexto_nao_cria_secao(monkeypatch):
    monkeypatch.setattr("aegis.contexto.contexto_do_projeto", lambda: "")
    saida = sistema({}, "", [])
    assert "## Contexto do projeto" not in saida


def test_contexto_do_projeto_usou_config(monkeypatch, tmp_path):
    from aegis import contexto as mod

    arquivo = tmp_path / "CONTEXTO.md"
    arquivo.write_text("convenção local", encoding="utf-8")
    monkeypatch.setattr(mod.config, "contexto_path", arquivo)
    assert mod.contexto_do_projeto() == "convenção local"