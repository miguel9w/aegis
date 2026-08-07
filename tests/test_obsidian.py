"""
Testes do banco estilo Obsidian (aegis/obsidian.py) — vault markdown com
[[wikilinks]], backlinks, tags e subpastas, tudo determinístico em tmp_path.
"""

from __future__ import annotations

import pytest

from aegis import obsidian as ob
from aegis.config import config


@pytest.fixture
def vault(tmp_path, monkeypatch):
    """Aponta o vault do config para um diretório temporário."""
    alvo = tmp_path / "vault"
    monkeypatch.setattr(config, "obsidian_dir", alvo)
    return alvo


def test_extrair_links():
    texto = "Veja [[Nota A]] e [[Nota B|alias]] e [[Nota A]] de novo"
    assert ob.extrair_links(texto) == ["Nota A", "Nota B"]


def test_extrair_tags():
    texto = "Falando de #ia e #lare e de novo #ia"
    assert ob.extrair_tags(texto) == ["ia", "lare"]


def test_criar_e_ler_nota(vault):
    ob.criar_nota.invoke({"nome": "Ideia 1", "conteudo": "fazer algo legal"})
    saida = ob.ler_nota.invoke({"nome": "Ideia 1"})
    assert "# Ideia 1" in saida
    assert "fazer algo legal" in saida


def test_criar_nota_duplicada_erro(vault):
    ob.criar_nota_obsidian("A", "x")
    with pytest.raises(ValueError, match="já existe"):
        ob.criar_nota_obsidian("A", "y")


def test_nota_em_subpasta(vault):
    ob.criar_nota_obsidian("Paper X", "resumo", pasta="papers")
    assert (vault / "papers" / "Paper_X.md").is_file()
    saida = ob.ler_nota.invoke({"nome": "Paper X"})
    assert "resumo" in saida


def test_ler_nota_inexistente(vault):
    with pytest.raises(ValueError, match="não encontrada"):
        ob.ler_nota_obsidian("Fantasma")


def test_ligar_nota_bidirecional(vault):
    ob.criar_nota_obsidian("Origem", "fala de")
    ob.criar_nota_obsidian("Destino", "alvo")
    ob.ligar_nota_obsidian("Origem", "Destino")
    indice = ob.recalcular_indice(vault)
    assert indice["notas"]["Origem"]["links"] == ["Destino"]
    assert indice["notas"]["Destino"]["backlinks"] == ["Origem"]


def test_ligar_para_inexistente_erro(vault):
    ob.criar_nota_obsidian("A", "x")
    with pytest.raises(ValueError, match="não existe"):
        ob.ligar_nota_obsidian("A", "Fantasma")


def test_ligar_idempotente(vault):
    ob.criar_nota_obsidian("A", "x")
    ob.criar_nota_obsidian("B", "y")
    ob.ligar_nota_obsidian("A", "B")
    texto = (vault / "A.md").read_text(encoding="utf-8")
    assert texto.count("[[B]]") == 1


def test_buscar_fulltext(vault):
    ob.criar_nota_obsidian("Projeto Alfa", "implementar o motor")
    ob.criar_nota_obsidian("Outra", "nada a ver")
    saida = ob.buscar_nota_obsidian("motor")
    assert "implementar o motor" in saida
    assert "Outra" not in saida


def test_notas_por_tag(vault):
    ob.criar_nota_obsidian("N1", "texto #ia e #go")
    ob.criar_nota_obsidian("N2", "sem tags aqui")
    saida = ob.notas_por_tag_obsidian("ia")
    assert "N1" in saida and "N2" not in saida


def test_notas_conectadas_vazio(vault):
    ob.criar_nota_obsidian("A", "nota isolada")
    saida = ob.notas_conectadas_obsidian("A")
    assert "sai para: (nenhum)" in saida
    assert "recebe de: (nenhum)" in saida


def test_listar_vault_arvore(vault):
    ob.criar_nota_obsidian("Raiz", "x")
    ob.criar_nota_obsidian("Em Pasta", "y", pasta="sub")
    ob.criar_nota_obsidian("B", "z")
    ob.ligar_nota_obsidian("B", "Raiz")
    saida = ob.listar_obsidian_vault()
    assert "Vault Obsidian" in saida
    assert "1 backlink" in saida
    assert "sub/" in saida


def test_limpar_exige_confirmacao(vault):
    ob.criar_nota_obsidian("A", "x")
    with pytest.raises(ValueError, match="confirmar=True"):
        ob.limpar_vault()
    assert (vault / "A.md").exists()


def test_limpar_vault_com_confirmacao(vault):
    ob.criar_nota_obsidian("A", "x")
    ob.criar_nota_obsidian("B", "y", pasta="sub")
    ob.limpar_vault(confirmar=True)
    assert list(vault.rglob("*.md")) == []


def test_indice_nunca_obsoleto(vault):
    """Índice corrompido/antigo é recalculado na leitura."""
    ob.criar_nota_obsidian("A", "x [[B]]")
    ob.criar_nota_obsidian("B", "y")
    (vault / "indice.json").write_text("{lixo", encoding="utf-8")
    indice = ob._carregar_indice(vault)
    assert indice["notas"]["B"]["backlinks"] == ["A"]


def test_registro_das_ferramentas_obsidian():
    from aegis.ferramentas import carregar_ferramentas
    nomes = {f.name for f in carregar_ferramentas()}
    for esperado in ("criar_nota", "ler_nota", "ligar_nota", "buscar_notas",
                     "notas_por_tag", "notas_conectadas", "listar_obsidian",
                     "limpar_obsidian"):
        assert esperado in nomes