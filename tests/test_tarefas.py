"""Testes do planejamento de tarefas (paridade Hermes todo_tool)."""

from aegis.tarefas import LIMITE_CONTEUDO, TarefasStore, tarefas


def test_escrever_e_listar():
    store = TarefasStore()
    saida = store.escrever([
        {"id": "a1", "conteudo": "Implementar recall", "status": "executando"},
        {"id": "a2", "conteudo": "Escrever testes"},
    ])
    assert len(saida) == 2
    assert saida[0]["status"] == "executando"
    assert saida[1]["status"] == "pendente"  # sem status → pendente


def test_merge_preserva_existentes():
    store = TarefasStore()
    store.escrever([{"id": "a1", "conteudo": "um"}])
    store.escrever([{"id": "a2", "conteudo": "dois"}], merge=True)
    ids = {i["id"] for i in store.listar()}
    assert ids == {"a1", "a2"}


def test_substituicao_sem_merge():
    store = TarefasStore()
    store.escrever([{"id": "a1", "conteudo": "um"}])
    store.escrever([{"id": "a2", "conteudo": "dois"}])
    assert [i["id"] for i in store.listar()] == ["a2"]


def test_status_invalido_vira_pendente():
    store = TarefasStore()
    store.escrever([{"id": "a1", "conteudo": "x", "status": "naoexiste"}])
    assert store.listar()[0]["status"] == "pendente"


def test_ativas_apenas_pendente_executando():
    store = TarefasStore()
    store.escrever([
        {"id": "a", "conteudo": "pend", "status": "pendente"},
        {"id": "b", "conteudo": "exec", "status": "executando"},
        {"id": "c", "conteudo": "fim", "status": "concluida"},
        {"id": "d", "conteudo": "can", "status": "cancelada"},
    ])
    ativas = {i["id"] for i in store.ativas()}
    assert ativas == {"a", "b"}


def test_reinjecao_vazia_sem_ativas():
    store = TarefasStore()
    assert store.formato_para_reinjecar() == ""


def test_reinjecao_inclui_cabecalho():
    store = TarefasStore()
    store.escrever([{"id": "a", "conteudo": "terminar cron", "status": "executando"}])
    bloco = store.formato_para_reinjecar()
    assert "preservada na compressão" in bloco
    assert "terminar cron" in bloco


def test_persistencia_em_arquivo(tmp_path):
    caminho = str(tmp_path / "tarefas.json")
    store = TarefasStore(caminho)
    store.escrever([{"id": "a", "conteudo": "persistir"}])
    outro = TarefasStore(caminho)  # nova instância lê do disco
    assert outro.listar()[0]["conteudo"] == "persistir"


def test_trunca_conteudo_longo():
    store = TarefasStore()
    grande = "x" * (LIMITE_CONTEUDO + 100)
    store.escrever([{"id": "a", "conteudo": grande}])
    assert len(store.listar()[0]["conteudo"]) == LIMITE_CONTEUDO


def test_ferramenta_escreve_e_le():
    assert tarefas.name == "tarefas"
    saida = tarefas.invoke({"tarefas": [{"id": "f", "conteudo": "exemplo único"}]})
    assert "exemplo único" in saida
    lida = tarefas.invoke({})
    assert "exemplo único" in lida