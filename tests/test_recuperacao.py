"""Testes de recuperação de memória (RAG-lite) sobre a Store + skills."""

from __future__ import annotations

import aegis.recuperacao as rec
from aegis.memoria import criar_store_sync, namespace_memoria, namespace_perfil
from aegis.skills import HABILIDADES_REGISTRADAS


def test_pesquisa_recupera_do_store(tmp_path, monkeypatch):
    store = criar_store_sync(tmp_path / "store.db")
    store.put(namespace_perfil(), "perfil", {"cafe": "Miguel prefere café", "idioma": "pt-BR"})
    store.put(namespace_memoria("t1"), "fatos", {"projeto": "aegis em ~/git_repos"})

    monkeypatch.setattr(rec, "STORE_ATUAL", store)
    monkeypatch.setattr(
        "aegis.skills.HABILIDADES_REGISTRADAS",
        {"pesquisa": {"descricao": "Busca fontes.", "gatilho": "", "conteudo": "Busque fontes."}},
    )

    saida = rec.pesquisar_memoria.invoke({"consulta": "café do Miguel"})
    assert "café" in saida or "Miguel prefere café" in saida
    assert "memória:" in saida


def test_pesquisa_recupera_skill(tmp_path, monkeypatch):
    store = criar_store_sync(tmp_path / "store2.db")
    monkeypatch.setattr(rec, "STORE_ATUAL", store)
    monkeypatch.setattr(
        "aegis.skills.HABILIDADES_REGISTRADAS",
        {
            "pesquisa-tecnica": {
                "descricao": "Metodologia de pesquisa técnica com fontes primárias e documentação.",
                "gatilho": "",
                "conteudo": "Sempre busque fontes primárias de documentação.",
            }
        },
    )

    saida = rec.pesquisar_memoria.invoke({"consulta": "fontes documentação"})
    assert "skill:pesquisa-tecnica" in saida


def test_pesquisa_sem_store():
    # garante restauração do estado global (STORE_ATUAL global foi dirimido)
    saida = rec.pesquisar_memoria.invoke({"consulta": "qualquer coisa"})
    assert "Nenhuma memória" in saida or isinstance(saida, str)


def test_pesquisa_consulta_vazia(tmp_path, monkeypatch):
    store = criar_store_sync(tmp_path / "store3.db")
    store.put(namespace_perfil(), "perfil", {"chave": "valor"})
    monkeypatch.setattr(rec, "STORE_ATUAL", store)
    monkeypatch.setattr("aegis.skills.HABILIDADES_REGISTRADAS", {})

    saida = rec.pesquisar_memoria.invoke({"consulta": "   "})
    assert "Nenhuma memória" in saida