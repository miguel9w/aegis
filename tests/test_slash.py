"""
Testes dos comandos de barra (aegis/slash.py) — parser, executores,
ações de app e integração com a TUI (headless, sem LLM).
"""

from __future__ import annotations

import asyncio

import pytest

from aegis import slash as sl
from aegis.config import config


@pytest.fixture
def estado_tmp(tmp_path, monkeypatch):
    """Aponta os arquivos de estado para o tmp_path (isolamento total)."""
    for attr in ("papel_ativo_path", "tarefa_atual_path", "pensamento_path",
                 "plano_tarefas_path", "notas_path", "memoria_camel_path"):
        monkeypatch.setattr(config, attr, tmp_path / f"{attr}.json")
    monkeypatch.setattr(config, "obsidian_dir", tmp_path / "vault")
    return tmp_path


# ---- parser ---------------------------------------------------------------

def test_parser_slash_basico():
    assert sl.parsear_slash("/ajuda") == ("ajuda", "")
    assert sl.parsear_slash("/anotar comprar leite") == ("anotar", "comprar leite")
    assert sl.parsear_slash("pergunta normal") is None
    assert sl.parsear_slash("") is None


# ---- registros ------------------------------------------------------------

def test_registro_tem_os_20_base():
    base = {"ajuda", "sair", "limpar", "novo", "status", "config", "papel",
            "papeis", "definir_papel", "tarefa", "planejar", "plano",
            "marcar", "anotar", "notas", "memoria", "salvar_memoria",
            "esquecer", "ferramentas"}
    implementados = set(sl.IMPLEMENTADOS)
    assert base <= implementados


def test_registro_cientifico_e_vault():
    for nome in ("criar_nota", "ver_nota", "buscar_nota", "tag",
                 "buscar_paper", "salvar_paper", "bibtex", "revisar", "obsidian"):
        assert nome in sl.IMPLEMENTADOS


# ---- executores -----------------------------------------------------------

def test_executar_ajuda(estado_tmp):
    saida = sl.executar_slash("ajuda")
    assert "/papeis" in saida
    assert "/criar_nota" in saida


def test_executar_desconhecido():
    assert "desconhecido" in sl.executar_slash("zzz")


def test_executar_app_acoes():
    assert sl.executar_slash("sair") == "@@ACAO:sair"
    assert sl.executar_slash("limpar") == "@@ACAO:limpar"
    assert sl.executar_slash("novo") == "@@ACAO:novo"


def test_status_mostra_papeis_e_ferramentas(estado_tmp):
    saida = sl.executar_slash("status")
    assert "ferramentas registradas" in saida


def test_config_mostra_caminhos(estado_tmp):
    saida = sl.executar_slash("config")
    assert "vault obsidian" in saida
    assert "sqlite" in saida


def test_papel_e_papeis(estado_tmp):
    saida = sl.executar_slash("papeis")
    assert "assistente" in saida
    sl.executar_slash("definir_papel", "redator")
    assert "redator" in sl.executar_slash("papel")


def test_planejar_grava_tarefa(estado_tmp):
    saida = sl.executar_slash("planejar", "lançar versão; sem rede; critérios de teste")
    assert "- objetivo: lançar versão" in saida
    assert "sem rede" in sl.executar_slash("tarefa")


def test_plano_marcar_pensar(estado_tmp):
    sl.executar_slash("plano")  # sem plano → (nenhum plano...)
    sl.executar_slash("anotar", "preciso de café")
    saida = sl.executar_slash("notas")
    assert "preciso de café" in saida


def test_memoria_salvar_e_consultar(estado_tmp):
    sl.executar_slash("salvar_memoria", "deploy às 18h>=8")
    saida = sl.executar_slash("memoria")
    assert "deploy" in saida
    top = sl.executar_slash("memoria", "deploy")
    assert "(nenhum" not in top


def test_esquecer_memoria(estado_tmp):
    sl.executar_slash("salvar_memoria", "fato temporário")
    saida = sl.executar_slash("memoria")
    # pega o primeiro id [..] da lista
    import re
    m = re.search(r"\[([a-f0-9]+)\]", saida)
    assert m
    sl.executar_slash("esquecer", m.group(1))
    assert "fato temporário" not in sl.executar_slash("memoria")


def test_ferramentas_lista(estado_tmp):
    saida = sl.executar_slash("ferramentas")
    assert "tarefas" in saida or "Tarefas" in saida


def test_criar_e_ler_nota_vault(estado_tmp):
    sl.executar_slash("criar_nota", "Ideia Genial > primeira ideia")
    saida = sl.executar_slash("ver_nota", "Ideia Genial")
    assert "primeira ideia" in saida
    assert "Ideia Genial" in sl.executar_slash("buscar_nota", "primeira")


def test_tag_no_vault(estado_tmp):
    sl.executar_slash("criar_nota", "Nota IA > conteúdo sobre #ia")
    saida = sl.executar_slash("tag", "ia")
    assert "Nota IA" in saida


def test_obsidian_lista(estado_tmp):
    assert "vault vazio" in sl.executar_slash("obsidian")


def test_buscar_paper_rede_falha(estado_tmp, monkeypatch):
    """Sem rede → mensagem amigável, sem crash do slash."""
    import requests
    from aegis import cientificas

    def _explode(*a, **k):
        raise requests.ConnectionError

    monkeypatch.setattr(cientificas.requests, "get", _explode)
    saida = sl.executar_slash("buscar_paper", "agentes")
    assert "nenhum resultado" in saida


# ---- TUI ------------------------------------------------------------------

def test_tui_intercepta_slash_sem_llm():
    """enviar('/ajuda') responde localmente (nenhum produtor é chamado)."""
    from textual.widgets import Markdown

    from aegis.tui import TuiAegis

    chamou = {"prod": False}

    async def produtor():
        chamou["prod"] = True
        yield {"tipo": "token", "texto": "nunca"}

    class CfgFake:
        thread_id = "teste"
        modelo = "fake"

    app = TuiAegis(app=None, ferramentas=[], cfg=CfgFake(), produtor_eventos=produtor)

    async def main():
        async with app.run_test() as pilot:
            app.enviar("/ajuda")
            for _ in range(10):
                await pilot.pause()
            # asserts dentro do contexto run_test — a DOM é desmontada ao sair
            from textual.widgets import Markdown
            marcacoes = list(app.chat.query(Markdown))
            assert len(marcacoes) >= 2  # "Você: /ajuda" + resposta local do Aegis
            assert "nunca" not in app.ultima_resposta  # produtor não foi usado

    asyncio.run(main())
    assert chamou["prod"] is False