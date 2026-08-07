"""
Testes da memória pontuada estilo CAMEL (aegis/memoria_camel.py).

Cobrem a heurística de pontuação (recência × importância × overlap), a
persistência JSON, o ranking top-k e as três ferramentas.
"""

from __future__ import annotations

import json
import time

import pytest

from aegis import memoria_camel as mc
from aegis.config import config

AGORA = 1_800_000_000.0


def test_pontuacao_recencia_decai_com_o_tempo():
    """Registro recente pontua mais que antigo (mesmo conteúdo)."""
    agora = AGORA
    velho = mc.pontuacao("gosto de café", set(), 5.0, agora - 30 * 86400, agora)
    recente = mc.pontuacao("gosto de café", set(), 5.0, agora - 60, agora)
    assert recente > velho


def test_pontuacao_meia_vida_configuravel():
    """Com meia-vida pequena, um registro antigo perde quase toda a recência."""
    agora = AGORA
    d = mc.pontuacao("x", set(), 0.0, agora - 50, agora, meia_vida=5.0)
    assert d < 0.01


def test_pontuacao_importancia_maior_vence():
    agora = AGORA
    alta = mc.pontuacao("alpha", set(), 10.0, agora - 5000, agora)
    baixa = mc.pontuacao("alpha", set(), 1.0, agora - 60, agora)
    assert alta > baixa


def test_pontuacao_overlap_lexical():
    tokens = mc._tokenizar("inteligência artificial")
    com_overlap = mc.pontuacao("estudo de inteligência artificial", tokens, 0.0, 1.0, 2.0)
    sem_overlap = mc.pontuacao("receita de bolo", tokens, 0.0, 1.0, 2.0)
    assert com_overlap > sem_overlap


def test_roundtrip_persistencia(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "memoria_camel_path", tmp_path / "memoria_camel.json")
    mc.registrar_memoria_camel.invoke({"conteudo": "prefere terminal", "importancia": 8.0})
    registros = mc.carregar_memoria()
    assert len(registros) == 1
    assert registros[0].conteudo == "prefere terminal"
    assert registros[0].importancia == 8.0
    # recarrega do disco
    registros2 = mc.carregar_memoria()
    assert registros2[0].id == registros[0].id


def test_topk_ranqueia_pelo_mais_relevante(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "memoria_camel_path", tmp_path / "memoria_camel.json")
    agora = time.time()
    mc.registrar_memoria_camel.invoke({"conteudo": "o deploy é às 18h"})
    mc.registrar_memoria_camel.invoke({"conteudo": "o gato se chama Teo"})

    top = mc.consultar_topk("deploy", k=1, agora=agora)
    assert len(top) == 1
    assert "deploy" in top[0][0].conteudo


def test_topk_respeita_k():
    registros = [
        mc.RegistroMemoria(conteudo=f"fato {i}", importancia=10.0) for i in range(5)
    ]
    top = mc.consultar_topk("fato", registros, k=2)
    assert len(top) == 2


def test_esquecer_registro(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "memoria_camel_path", tmp_path / "memoria_camel.json")
    mc.registrar_memoria_camel.invoke({"conteudo": "temporário"})
    id_ = mc.carregar_memoria()[0].id
    saida = mc.esquecer_memoria_camel.invoke({"id_registro": id_})
    assert "esquecido" in saida
    assert mc.carregar_memoria() == []


def test_esquecer_desconhecido_erro(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "memoria_camel_path", tmp_path / "memoria_camel.json")
    with pytest.raises(ValueError, match="não encontrado"):
        mc.esquecer_memoria_camel.invoke({"id_registro": "zzz"})


def test_n_max_limitado(tmp_path):
    registros = [mc.RegistroMemoria(conteudo=f"fato {i}") for i in range(5)]
    mc.salvar_memoria(registros, caminho=tmp_path / "m.json", n_max=3)
    carregados = mc.carregar_memoria(tmp_path / "m.json")
    assert len(carregados) == 3


def test_tokenizar_ignora_stopwords():
    tokens = mc._tokenizar("o dia do livro e a caneta")
    assert "dia" in tokens
    assert "o" not in tokens and "do" not in tokens


def test_registro_camel_registrada():
    from aegis.ferramentas import carregar_ferramentas
    nomes = {f.name for f in carregar_ferramentas()}
    for esperado in ("registrar_memoria_camel", "consultar_memoria_camel", "esquecer_memoria_camel"):
        assert esperado in nomes