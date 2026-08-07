"""
Testes de papéis (roles estilo CAMEL) — aegis/papeis.py.

Cobrem: catálogo (padrões/JSON/override), resolução, persistência do papel
ativo e da tarefa especificada, ferramentas e injeção no prompt de sistema.
"""

from __future__ import annotations

import json

import pytest
from types import SimpleNamespace

from aegis import papeis
from aegis.config import config
from aegis.ferramentas import carregar_ferramentas
from aegis.prompts import sistema


def _papeis_json(papeis_lista, substituir=False):
    return {"substituir_padrao": substituir, "papeis": papeis_lista}


@pytest.fixture
def catalogo_tmp(tmp_path, monkeypatch):
    """Aponta config.papeis_config_path para um arquivo temporário."""
    arquivo = tmp_path / "papeis.json"
    monkeypatch.setattr(config, "papeis_config_path", arquivo)
    return arquivo


def test_padrao_quando_sem_json(catalogo_tmp):
    """Sem arquivo → os 4 papéis padrão."""
    papeis_lidos = papeis.carregar_papeis()
    assert [p.nome for p in papeis_lidos] == [
        "assistente", "pesquisador", "redator", "planejador"]


def test_override_e_extensao_pelo_json(catalogo_tmp):
    catalogo_tmp.write_text(json.dumps(_papeis_json([
        {"nome": "assistente", "descricao": "novo assistente", "identidade": "X"},
        {"nome": "cientista", "descricao": "Papel científico", "identidade": "Y"},
    ])), encoding="utf-8")
    pape = papeis.carregar_papeis()
    nomes = [p.nome for p in pape]
    assert nomes == ["pesquisador", "redator", "planejador", "assistente", "cientista"]
    ass = papeis.resolver_papel("assistente", pape)
    assert ass.identidade == "X"
    assert ass.descricao == "novo assistente"


def test_substituir_padrao_true(catalogo_tmp):
    catalogo_tmp.write_text(json.dumps(_papeis_json(
        [{"nome": "sozinho", "identidade": "Z"}], substituir=True)), encoding="utf-8")
    pape = papeis.carregar_papeis()
    assert [p.nome for p in pape] == ["sozinho"]


def test_resolver_papel_case_insensitive(catalogo_tmp):
    p = papeis.resolver_papel("PESQUISADOR")
    assert p.nome == "pesquisador"


def test_resolver_papel_desconhecido(catalogo_tmp):
    with pytest.raises(ValueError, match="não encontrado"):
        papeis.resolver_papel("ninja")


def test_ferramenta_definir_e_ver_papel(catalogo_tmp, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "papel_ativo_path", tmp_path / "papel_ativo.json")
    saida = papeis.definir_papel.invoke({"nome": "redator"})
    assert "redator" in saida
    assert papeis.ler_papel_ativo() == "redator"
    saida_ver = papeis.ver_papel.invoke({})
    assert "REDATOR" in saida_ver or "redator" in saida_ver


def test_listar_papeis(catalogo_tmp):
    saida = papeis.listar_papeis.invoke({})
    assert "assistente" in saida
    assert "pesquisador" in saida
    assert "planejador" in saida


def test_especificar_tarefa_persistida(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "tarefa_atual_path", tmp_path / "tarefa_atual.json")
    saida = papeis.especificar_tarefa.invoke({
        "objetivo": "Revisar o código", "restricoes": "sem quebrar testes",
        "criterios": "suíte verde",
    })
    assert "Revisar o código" in saida
    tarefa = papeis.ler_tarefa_atual()
    assert tarefa["objetivo"] == "Revisar o código"
    assert tarefa["criterios"] == "suíte verde"


def test_estruturar_tarefa_heuristica():
    obj, rest, crit = papeis._parsear_texto_tarefa(
        "Publicar v0.11.0; restrição: testes verdes; critério: push ok")
    assert obj == "Publicar v0.11.0"
    assert "testes verdes" in rest
    assert "push ok" in crit


def test_estruturar_tarefa_com_marcadores():
    obj, rest, crit = papeis._parsear_texto_tarefa(
        "Montar relatório\n- restrição: PDF\n- critério: < 10 páginas\n- usar dados de 2026")
    assert obj == "Montar relatório"
    assert "PDF" in rest
    assert "< 10 páginas" in crit


def test_montar_bloco_personalidade_sem_estado(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "papel_ativo_path", tmp_path / "ausente.json")
    monkeypatch.setattr(config, "tarefa_atual_path", tmp_path / "ausente2.json")
    assert papeis.montar_bloco_personalidade() == ""


def test_montar_bloco_personalidade_com_papel_e_tarefa(tmp_path, monkeypatch):
    papeis_path = tmp_path / "papeis.json"
    monkeypatch.setattr(config, "papeis_config_path", papeis_path)
    monkeypatch.setattr(config, "papel_ativo_path", tmp_path / "papel_ativo.json")
    monkeypatch.setattr(config, "tarefa_atual_path", tmp_path / "tarefa_atual.json")
    (tmp_path / "papel_ativo.json").write_text(json.dumps({"nome": "planejador"}), encoding="utf-8")
    (tmp_path / "tarefa_atual.json").write_text(json.dumps({"objetivo": "Planejar o evento"}), encoding="utf-8")
    bloco = papeis.montar_bloco_personalidade()
    assert "## Papel ativo" in bloco
    assert "planejador" in bloco
    assert "## Tarefa especificada" in bloco
    assert "Planejar o evento" in bloco


def test_injecao_no_sistema_contem_papel_ativado(tmp_path, monkeypatch):
    """sistema() anexa o bloco de personalidade quando há papel/tarefa."""
    monkeypatch.setattr(config, "papel_ativo_path", tmp_path / "papel_ativo.json")
    monkeypatch.setattr(config, "tarefa_atual_path", tmp_path / "tarefa_atual.json")
    (tmp_path / "papel_ativo.json").write_text(json.dumps({"nome": "pesquisador"}), encoding="utf-8")
    fake_tool = SimpleNamespace(name="buscar_web", description="pesquisa na web")
    prompt = sistema({}, "", [fake_tool])
    assert "## Papel ativo" in prompt
    assert "pesquisador" in prompt


def test_registro_das_ferramentas_de_papel():
    """definir_papel/ver_papel/listar_papeis/especificar_tarefa registradas."""
    nomes = {f.name for f in carregar_ferramentas()}
    for esperado in ("definir_papel", "ver_papel", "listar_papeis", "especificar_tarefa"):
        assert esperado in nomes