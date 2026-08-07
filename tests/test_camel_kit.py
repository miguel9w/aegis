"""
Testes dos toolkits CAMEL (aegis/camel_kit.py) — thinking, task-planning
e note-taking, todos determinísticos e persistidos em JSON.
"""

from __future__ import annotations

import pytest

from aegis import camel_kit as ck
from aegis.config import config


@pytest.fixture
def estado_tmp(tmp_path, monkeypatch):
    """Aponta todos os arquivos de estado do kit para o tmp_path."""
    monkeypatch.setattr(config, "pensamento_path", tmp_path / "pensamento.json")
    monkeypatch.setattr(config, "plano_tarefas_path", tmp_path / "plano.json")
    monkeypatch.setattr(config, "notas_path", tmp_path / "notas.json")
    return tmp_path


def test_pensar_encadeia_em_numerado(estado_tmp):
    ck.pensar.invoke({"passo_raciocinio": "primeiro analisar"})
    saida = ck.pensar.invoke({"passo_raciocinio": "depois executar"})
    assert "1." in saida and "2." in saida
    assert "primeiro analisar" in saida
    assert "depois executar" in saida


def test_ver_pensamento_vazio(estado_tmp):
    assert "nenhum passo" in ck.ver_pensamento.invoke({})


def test_planejar_tarefa_cria_e_formata(estado_tmp):
    saida = ck.planejar_tarefa.invoke({
        "objetivo": "Lançar v0.11.0",
        "passos": "- rodar testes\n- commitar\n- publicar",
    })
    assert "📋 Plano: Lançar v0.11.0" in saida
    assert "[p1]" in saida and "[p2]" in saida and "[p3]" in saida
    assert "Progresso: 0/3" in saida


def test_planejar_com_numeracao(estado_tmp):
    saida = ck.planejar_tarefa.invoke({
        "objetivo": "X", "passos": "1. um\n2. dois",
    })
    assert "[p1]" in saida and "[p2]" in saida


def test_planejar_sem_passos_erro(estado_tmp):
    with pytest.raises(ValueError, match="passo"):
        ck.planejar_tarefa.invoke({"objetivo": "X", "passos": "   "})


def test_atualizar_plano_status_ok(estado_tmp):
    ck.planejar_tarefa.invoke({"objetivo": "X", "passos": "- a\n- b"})
    saida = ck.atualizar_plano.invoke({"id": "p1", "novo_status": "ok"})
    assert "✅" in saida
    assert "Progresso: 1/2" in saida


def test_atualizar_plano_status_invalido(estado_tmp):
    ck.planejar_tarefa.invoke({"objetivo": "X", "passos": "- a"})
    with pytest.raises(ValueError, match="status inválido"):
        ck.atualizar_plano.invoke({"id": "p1", "novo_status": "zzz"})


def test_atualizar_plano_sem_plano(estado_tmp):
    with pytest.raises(ValueError, match="nenhum plano"):
        ck.atualizar_plano.invoke({"id": "p1", "novo_status": "ok"})


def test_atualizar_passo_desconhecido(estado_tmp):
    ck.planejar_tarefa.invoke({"objetivo": "X", "passos": "- a"})
    with pytest.raises(ValueError, match="não encontrado"):
        ck.atualizar_plano.invoke({"id": "p9", "novo_status": "ok"})


def test_ver_plano_sem_plano(estado_tmp):
    assert "nenhum plano" in ck.ver_plano.invoke({})


def test_anotar_e_ver_notas(estado_tmp):
    ck.anotar.invoke({"nota": "primeira anotação"})
    ck.anotar.invoke({"nota": "segunda anotação"})
    saida = ck.ver_notas.invoke({"qtd": 5})
    assert "primeira anotação" in saida
    assert "segunda anotação" in saida


def test_ver_notas_limitado(estado_tmp):
    for i in range(3):
        ck.anotar.invoke({"nota": f"nota {i}"})
    saida = ck.ver_notas.invoke({"qtd": 2})
    assert "nota 0" not in saida
    assert "nota 1" in saida and "nota 2" in saida


def test_ver_notas_vazio(estado_tmp):
    assert "nenhuma nota" in ck.ver_notas.invoke({"qtd": 5})


def test_registro_do_toolkit_camel():
    from aegis.ferramentas import carregar_ferramentas
    nomes = {f.name for f in carregar_ferramentas()}
    for esperado in ("pensar", "ver_pensamento", "planejar_tarefa",
                     "atualizar_plano", "ver_plano", "anotar", "ver_notas"):
        assert esperado in nomes