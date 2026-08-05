"""Testes de memória (Store de longo prazo), habilidades, plugins e trajetória."""

from __future__ import annotations

import json

from aegis.memoria import criar_store_sync, namespace_memoria, namespace_perfil
from aegis.plugins import carregar_plugins, erros_carregamento, recarregar_plugins
from aegis.skills import carregar_e_expor, carregar_skills, criar_skill_path
from aegis.trajetoria import Trajetoria


# ---------------------------------------------------------------------
# Memória de longo prazo (SqliteStore)
# ---------------------------------------------------------------------

def test_store_put_get(tmp_path):
    store = criar_store_sync(tmp_path / "store.db")
    ns = namespace_perfil()
    store.put(ns, "perfil", {"nome": "Miguel", "idioma": "pt-BR"})

    item = store.get(ns, "perfil")
    assert item is not None
    assert item.value["nome"] == "Miguel"

    # sobrescrever preserva a chave (merge feito pelo nó no_memoria)
    store.put(ns, "perfil", {"nome": "Miguel", "ferramenta": "aegis"})
    item2 = store.get(ns, "perfil")
    assert item2.value["ferramenta"] == "aegis"


def test_store_namespaces_isolados(tmp_path):
    store = criar_store_sync(tmp_path / "store2.db")
    store.put(namespace_perfil(), "perfil", {"global": 1})
    store.put(namespace_memoria("topic-A"), "fatos", {"a": 1})
    store.put(namespace_memoria("topic-B"), "fatos", {"b": 2})

    assert store.get(namespace_perfil(), "perfil").value == {"global": 1}
    assert store.get(namespace_memoria("topic-A"), "fatos").value == {"a": 1}
    assert store.get(namespace_memoria("topic-B"), "fatos").value == {"b": 2}


# ---------------------------------------------------------------------
# Habilidades (agentskills.io)
# ---------------------------------------------------------------------

def test_carregar_skills_lê_skil_md(tmp_path):
    (tmp_path / "pesquisa").mkdir()
    (tmp_path / "pesquisa" / "SKILL.md").write_text(
        "---\nname: pesquisa-tecnica\ndescription: Metodologia de pesquisa.\n---\n\n# Instruções\nBusque primeiro.\n",
        encoding="utf-8",
    )
    habilidades = carregar_skills(tmp_path)
    assert "pesquisa-tecnica" in habilidades
    assert habilidades["pesquisa-tecnica"]["descricao"] == "Metodologia de pesquisa."
    assert "Busque primeiro" in habilidades["pesquisa-tecnica"]["conteudo"]


def test_carregar_e_expor_cria_ferramentas(tmp_path):
    (tmp_path / "skill-x").mkdir()
    (tmp_path / "skill-x" / "SKILL.md").write_text(
        "---\nname: skill-x\ndescription: Faz x.\n---\nConteudo.",
        encoding="utf-8",
    )
    ferramentas = carregar_e_expor(tmp_path)
    nomes = [f.name for f in ferramentas]
    assert "usar_skill:skill-x" in nomes
    assert "criar_skill" in nomes


def test_criar_skill_escreve_e_valida(tmp_path):
    caminho = criar_skill_path(tmp_path, "nova-skill", "desc nova", "corpo")
    assert caminho.exists()
    conteudo = caminho.read_text(encoding="utf-8")
    assert "name: nova-skill" in conteudo
    assert "corpo" in conteudo


# ---------------------------------------------------------------------
# Plugins dinâmicos
# ---------------------------------------------------------------------

def test_carregar_plugins_exemplo():
    ferramentas = carregar_plugins()
    nomes = {f.name for f in ferramentas}
    assert "contar_palavras" in nomes
    assert "reverter_texto" in nomes
    assert not erros_carregamento(), erros_carregamento()


def test_contar_e_reverter():
    ferramentas = {f.name: f for f in carregar_plugins()}
    assert ferramentas["contar_palavras"].invoke({"texto": "olá mundo aegis"}) == "3 palavras, 15 caracteres."
    assert ferramentas["reverter_texto"].invoke({"texto": "abc"}) == "cba"


def test_recarregar_plugins():
    f1 = {f.name for f in recarregar_plugins()}
    f2 = {f.name for f in recarregar_plugins()}
    assert f1 == f2 == {"contar_palavras", "reverter_texto"}


# ---------------------------------------------------------------------
# Trajetória (auditoria JSONL)
# ---------------------------------------------------------------------

def test_trajetoria_registra_jsonl(tmp_path):
    traj = Trajetoria(tmp_path)
    traj.registrar("thread-a", "no", {"no": "no_agente"})
    traj.registrar("thread-a", "ferramenta_inicio", {"nome": "calculadora"})

    arquivo = tmp_path / ("trajetoria_" + json_date() + ".jsonl")
    linhas = arquivo.read_text(encoding="utf-8").strip().splitlines()
    assert len(linhas) == 2
    primeiro = json.loads(linhas[0])
    assert primeiro["tipo"] == "no"
    assert primeiro["thread_id"] == "thread-a"
    segundo = json.loads(linhas[1])
    assert segundo["tipo"] == "ferramenta_inicio"
    assert segundo["dados"]["nome"] == "calculadora"


def json_date() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")