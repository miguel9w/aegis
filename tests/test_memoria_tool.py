"""Testes da memória explícita (paridade Hermes memory_tool)."""

from langgraph.store.memory import InMemoryStore

from aegis.memoria_tool import NS_FATOS, NS_PERFIL, definir_store, gerenciar_memoria


def _store_novo():
    return InMemoryStore()


def test_salvar_grava_na_store():
    store = _store_novo()
    definir_store(store)
    gerenciar_memoria.invoke({"acao": "salvar", "conteudo": "Miguel usa CachyOS", "chave": "os"})
    itens = list(store.search(NS_FATOS))
    assert len(itens) == 1
    assert itens[0].key == "os"
    assert itens[0].value["conteudo"] == "Miguel usa CachyOS"


def test_salvar_sem_conteudo_rejeita():
    definir_store(_store_novo())
    saida = gerenciar_memoria.invoke({"acao": "salvar"})
    assert "Nada a salvar" in saida


def test_listar_mostra_fatos():
    store = _store_novo()
    definir_store(store)
    gerenciar_memoria.invoke({"acao": "salvar", "conteudo": "fato um", "chave": "a"})
    gerenciar_memoria.invoke({"acao": "salvar", "conteudo": "fato dois", "chave": "b"})
    saida = gerenciar_memoria.invoke({"acao": "listar"})
    assert "fato um" in saida and "fato dois" in saida


def test_esquecer_por_chave():
    store = _store_novo()
    definir_store(store)
    gerenciar_memoria.invoke({"acao": "salvar", "conteudo": "x", "chave": "abc"})
    saida = gerenciar_memoria.invoke({"acao": "esquecer", "chave": "ab"})  # substring
    assert "1 fato" in saida
    assert not list(store.search(NS_FATOS))


def test_esquecer_por_conteudo():
    store = _store_novo()
    definir_store(store)
    gerenciar_memoria.invoke({"acao": "salvar", "conteudo": "preferência antiga", "chave": "p"})
    saida = gerenciar_memoria.invoke({"acao": "esquecer", "conteudo": "preferência"})
    assert "1 fato" in saida


def test_perfil_funde_dict():
    store = _store_novo()
    definir_store(store)
    gerenciar_memoria.invoke({"acao": "salvar", "alvo": "perfil", "chave": "idioma", "conteudo": "pt-BR"})
    gerenciar_memoria.invoke({"acao": "salvar", "alvo": "perfil", "chave": "stack", "conteudo": "Next.js"})
    item = store.get(NS_PERFIL, "perfil")
    assert item.value == {"idioma": "pt-BR", "stack": "Next.js"}


def test_listar_perfil():
    definir_store(_store_novo())
    gerenciar_memoria.invoke({"acao": "salvar", "alvo": "perfil", "chave": "cidade", "conteudo": "Brasil"})
    saida = gerenciar_memoria.invoke({"acao": "listar", "alvo": "perfil"})
    assert "cidade: Brasil" in saida


def test_acao_invalida():
    definir_store(_store_novo())
    saida = gerenciar_memoria.invoke({"acao": "explodir"})
    assert "Ação inválida" in saida


def test_sem_store_avisa():
    from aegis import memoria_tool as mod

    anterior = mod.STORE_ATUAL
    mod.STORE_ATUAL = None
    try:
        saida = gerenciar_memoria.invoke({"acao": "salvar", "conteudo": "x"})
        assert "indisponível" in saida
    finally:
        mod.STORE_ATUAL = anterior