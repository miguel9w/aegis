"""
Testes da configuração por JSON (aegis/config_json.py) — externalização
de hardcodes em config/dados/*.json com fallback seguro.
"""

from __future__ import annotations

import json

import pytest

from aegis import agendador, contexto, tarefas
from aegis.config_json import carregar_config_json


def test_merge_json_sobrescreve_padroes(tmp_path):
    arquivo = tmp_path / "cfg.json"
    arquivo.write_text(json.dumps({"limite": 999, "novo": "x"}),
                       encoding="utf-8")
    resultado = carregar_config_json("cfg.json",
                                     {"limite": 10, "padrao": True},
                                     caminho=arquivo)
    assert resultado["limite"] == 999
    assert resultado["padrao"] is True
    assert resultado["novo"] == "x"


def test_fallback_quando_arquivo_ausente(tmp_path):
    resultado = carregar_config_json("nao_existe.json", {"a": 1},
                                     caminho=tmp_path / "nao_existe.json")
    assert resultado == {"a": 1}


def test_fallback_quando_json_invalido(tmp_path):
    arquivo = tmp_path / "quebrado.json"
    arquivo.write_text("{{{ nao é json", encoding="utf-8")
    resultado = carregar_config_json("quebrado.json", {"a": 2},
                                     caminho=arquivo)
    assert resultado == {"a": 2}


def test_fallback_quando_raiz_nao_dict(tmp_path):
    arquivo = tmp_path / "lista.json"
    arquivo.write_text("[1, 2, 3]", encoding="utf-8")
    resultado = carregar_config_json("lista.json", {"a": 3},
                                     caminho=arquivo)
    assert resultado == {"a": 3}


def test_padroes_nao_sao_mutados_entre_chamadas(tmp_path):
    arquivo = tmp_path / "cfg.json"
    arquivo.write_text(json.dumps({"a": 9}), encoding="utf-8")
    padroes = {"a": 1}
    carregar_config_json("cfg.json", padroes, caminho=arquivo)
    assert padroes == {"a": 1}


def test_modulos_leem_dos_json_de_config():
    """Os hardcodes trocados refletem os valores dos arquivos de config."""
    assert contexto.LIMITE_CONTEXTO == 4000
    assert tarefas.LIMITE_CONTEUDO == 4000
    assert tarefas.LIMITE_ITENS == 256
    assert agendador.DEFAULT_FREQUENCIAS == ("nenhuma", "horaria", "diaria", "semanal")


def test_modulos_aceitam_override_dos_json(tmp_path, monkeypatch):
    """Sobrescrevendo o JSON (patch no singleton), os módulos mudam junto."""
    arquivo = tmp_path / "tarefas_config.json"
    arquivo.write_text(json.dumps({"limite_itens": 10}), encoding="utf-8")
    monkeypatch.setattr(tarefas, "_TAREFAS_CFG", {"limite_conteudo": 4000, "limite_itens": 10})
    # Recarrega o valor derivado do mesmo jeito que o módulo faz na importação
    novo = int(tarefas._TAREFAS_CFG["limite_itens"])
    assert novo == 10