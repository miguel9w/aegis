"""Testes das ferramentas de arquivo — sandbox de caminho + diff unified."""

from pathlib import Path

from aegis.config import config
from aegis.ferramentas.sistema import (
    editar_arquivo, ler_arquivo, listar_arquivos, escrever_arquivo,
)


def _montar(tmp_path, monkeypatch):
    artefatos = tmp_path / "artefatos"
    artefatos.mkdir()
    monkeypatch.setattr(config, "artefatos_dir", artefatos)
    return artefatos


def test_escrever_arquivo_cria_com_diff(tmp_path, monkeypatch):
    artefatos = _montar(tmp_path, monkeypatch)
    alvo = artefatos / "novo.txt"
    saida = escrever_arquivo.invoke({"caminho": str(alvo), "conteudo": "linha1\nlinha2\n"})
    assert "ok" in saida.lower()
    assert "+linha1" in saida
    assert "+linha2" in saida
    assert alvo.read_text() == "linha1\nlinha2\n"


def test_escrever_arquivo_sobrescreve_com_diff(tmp_path, monkeypatch):
    artefatos = _montar(tmp_path, monkeypatch)
    alvo = artefatos / "edits.txt"
    alvo.write_text("a\nb\nc\n")
    saida = escrever_arquivo.invoke({"caminho": str(alvo), "conteudo": "a\nB\nc\n"})
    assert "-b" in saida
    assert "+B" in saida
    # conteúdo idêntico → sem diff, sem mudança
    saida2 = escrever_arquivo.invoke({"caminho": str(alvo), "conteudo": "a\nB\nc\n"})
    assert "inalterado" in saida2.lower()


def test_editar_arquivo_sucesso(tmp_path, monkeypatch):
    artefatos = _montar(tmp_path, monkeypatch)
    alvo = artefatos / "codigo.py"
    alvo.write_text("def oi():\n    return 1\n")
    saida = editar_arquivo.invoke({
        "caminho": str(alvo),
        "trecho_antigo": "return 1",
        "trecho_novo": "return 2",
    })
    assert "1 ocorrência" in saida
    assert "-    return 1" in saida
    assert "+    return 2" in saida
    assert alvo.read_text() == "def oi():\n    return 2\n"


def test_editar_arquivo_trecho_ausente_erro_controlado(tmp_path, monkeypatch):
    artefatos = _montar(tmp_path, monkeypatch)
    alvo = artefatos / "x.txt"
    alvo.write_text("abc")
    saida = editar_arquivo.invoke({
        "caminho": str(alvo), "trecho_antigo": "nao-existe", "trecho_novo": "z",
    })
    assert "não encontrado" in saida
    assert alvo.read_text() == "abc"  # nada foi alterado


def test_editar_arquivo_ambiguo_exige_contexto(tmp_path, monkeypatch):
    artefatos = _montar(tmp_path, monkeypatch)
    alvo = artefatos / "x.txt"
    alvo.write_text("aa")
    saida = editar_arquivo.invoke({"caminho": str(alvo), "trecho_antigo": "a", "trecho_novo": "b"})
    assert "ambíguo" in saida


def test_path_traversal_relativo_bloqueado(tmp_path, monkeypatch):
    _montar(tmp_path, monkeypatch)
    saida = escrever_arquivo.invoke({"caminho": "../../../../etc/malvado.txt", "conteudo": "x"})
    assert "fora do permitido" in saida


def test_path_absoluto_fora_bloqueado(tmp_path, monkeypatch):
    _montar(tmp_path, monkeypatch)
    saida = escrever_arquivo.invoke({"caminho": "/etc/passwd", "conteudo": "x"})
    assert "fora do permitido" in saida


def test_symlink_escape_bloqueado(tmp_path, monkeypatch):
    artefatos = _montar(tmp_path, monkeypatch)
    alvo_fora = tmp_path / "fora.txt"
    alvo_fora.write_text("segredo")
    link = artefatos / "escapa"
    link.symlink_to(alvo_fora)
    saida = ler_arquivo.invoke({"caminho": str(link)})
    assert "fora do permitido" in saida


def test_ler_arquivo_trunca(tmp_path, monkeypatch):
    artefatos = _montar(tmp_path, monkeypatch)
    alvo = artefatos / "grande.txt"
    alvo.write_text("x" * 9000)
    saida = ler_arquivo.invoke({"caminho": str(alvo), "limite": 100})
    assert "truncado" in saida
    assert len(saida) < 500


def test_ler_arquivo_projeto_permitido():
    # raiz do projeto é permitida (leitura de arquivo real, sem escrever)
    saida = ler_arquivo.invoke({"caminho": "pixi.toml", "limite": 300})
    assert "pixi.toml" in saida


def test_listar_arquivos():
    saida = listar_arquivos.invoke({"diretorio": "tests", "limite": 5})
    assert "test_" in saida or "conftest" in saida


def test_listar_diretorio_inexistente():
    saida = listar_arquivos.invoke({"diretorio": "nao-existe-xyz"})
    assert "não encontrado" in saida