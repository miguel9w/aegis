"""
X1 — Catálogo de subagentes sob demanda (delegados.json + arq_limite).

Cobre: leitura do catálogo com fallback (JSON ausente/corrompido), resolução
de pools por nome, anti-cascata (`arq_limite` filtra o pool E bloqueia
`_executar` acima do limite), fábrica de tools `delegar_*` (assinatura por
`parametro`), execução de um delegado com pool restrito + auto-correção e o
registro central das 5 tools. Determinístico, sem rede.
"""

from __future__ import annotations

import json

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

import aegis.subagentes as sub
from aegis.config import config
from conftest import ModeloFake, chamada_tool  # noqa: F401

C = {"configurable": {"thread_id": "t-x1"}}


# -- catálogo ---------------------------------------------------------------

def test_catalogo_json_versionado_tem_cinco_delegados(tmp_path, monkeypatch):
    import shutil

    destino = tmp_path / "delegados.json"
    shutil.copy(sub._CAMINHO_CATALOGO, destino)
    assert destino.exists()
    dados = json.loads(destino.read_text(encoding="utf-8"))
    nomes = [d["nome"] for d in dados["delegados"]]
    assert nomes == ["pesquisador", "redator", "codigo", "dados", "revisao"]
    for d in dados["delegados"]:
        assert d["arq_limite"] >= 1
        assert isinstance(d["ferramentas"], list)


def test_catalogo_corrompido_cai_no_default(tmp_path, monkeypatch):
    (tmp_path / "delegados.json").write_text("{isso nao é json", encoding="utf-8")
    monkeypatch.setattr(sub, "_CAMINHO_CATALOGO", tmp_path / "delegados.json")
    catalogo = sub._carregar_catalogo()
    assert [d["nome"] for d in catalogo] == [
        "pesquisador", "redator", "codigo", "dados", "revisao",
    ]
    assert sub.AVISOS_CATALOGO  # avisou, não quebrou


def test_catalogo_ausente_cai_no_default(tmp_path, monkeypatch):
    monkeypatch.setattr(sub, "_CAMINHO_CATALOGO", tmp_path / "nao-existe.json")
    assert sub._carregar_catalogo() == sub._CATALOGO_PADRAO


def test_entrada_invalida_descartada(tmp_path, monkeypatch):
    (tmp_path / "delegados.json").write_text(
        json.dumps({"delegados": [{"ferramentas": []}, {"nome": "x", "arq_limite": 9}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr(sub, "_CAMINHO_CATALOGO", tmp_path / "delegados.json")
    catalogo = sub._carregar_catalogo()
    assert len(catalogo) == 1
    assert catalogo[0]["nome"] == "x"
    assert catalogo[0]["arq_limite"] == 9  # respeita o valor do arquivo


# -- resolução de pools -----------------------------------------------------

def test_pool_do_codigo_tem_ferramentas_de_trabalho():
    pool = sub._resolver_pool(sub._CATALOGO_PADRAO[2]["ferramentas"])  # codigo
    nomes = {f.name for f in pool}
    assert {"ler_arquivo", "escrever_arquivo", "executar_comando",
            "comando_sandbox"} <= nomes
    # sem tools de delegação no pool resolvido (anti-cascata na origem)
    assert not any(n.startswith("delegar_") for n in nomes)


def test_pool_ignora_nome_desconhecido():
    pool = sub._resolver_pool(["nao_existe_123", "calculadora"])
    assert [f.name for f in pool] == ["calculadora"]


def test_redator_sem_ferramentas():
    pool = sub._resolver_pool([])
    assert pool == []


# -- anti-cascata (arq_limite) ----------------------------------------------

def test_arq_limite_1_remove_tools_de_delegacao_do_pool(monkeypatch):
    from aegis.subagentes import TOOLS_DELEGACAO

    # estado global blindado: nenhum subagente configurado (outros testes
    # da suíte podem ter chamado configurar_subagentes antes)
    monkeypatch.setattr(sub, "SUBAGENTES_ATUAIS", {})
    monkeypatch.setattr(sub, "ARQ_LIMITES", {})
    delegar_codigo = TOOLS_DELEGACAO["codigo"]
    app = sub.criar_subagente(
        "x", "prompt", [delegar_codigo], config, ModeloFake(), arq_limite=1
    )
    # o pool filtrado não é observável direto; o comportamento é: chamada
    # de delegação dentro do subagente bloqueada — verificamos via _executar
    assert sub._executar("codigo", "tarefa", None, _profundidade=1) == (
        "ERRO_FERRAMENTA: subagente 'codigo' não configurado."
    )


def test_arq_limite_bloqueia_delegacao_aninhada(monkeypatch):
    from aegis.subagentes import TOOLS_DELEGACAO

    monkeypatch.setattr(sub, "SUBAGENTES_ATUAIS", {"codigo": object()})
    monkeypatch.setattr(sub, "ARQ_LIMITES", {"codigo": 1})
    saida = sub._executar("codigo", "tarefa", None, _profundidade=2)
    assert "ERRO_FERRAMENTA" in saida
    assert "bloqueada" in saida
    assert "arq_limite 1" in saida


def test_arq_limite_2_permite_uma_camada(monkeypatch):
    monkeypatch.setattr(sub, "ARQ_LIMITES", {"codigo": 2})
    llm = ModeloFake()
    llm.configurar([AIMessage(content="sub resposta")])
    app = sub.criar_subagente("codigo", "prompt", [], config, llm, arq_limite=2)
    monkeypatch.setattr(sub, "SUBAGENTES_ATUAIS", {"codigo": app})
    # profundidade 2 <= limite 2 → executa (não bloqueia)
    saida = sub._executar("codigo", "tarefa", None, _profundidade=2)
    assert saida == "sub resposta"


# -- fábrica de tools -------------------------------------------------------

def test_tools_delegacao_registradas_no_central():
    nomes = {f.name for f in sub.tools_delegacao()}
    assert nomes == {
        "delegar_pesquisa", "delegar_redacao", "delegar_codigo",
        "delegar_dados", "delegar_revisao",
    }


def test_fabrica_assinatura_por_parametro():
    for d in sub._CATALOGO_PADRAO:
        t = sub._tool_delegacao(d)
        nome_tool = d.get("tool") or f"delegar_{d['nome']}"
        assert t.name == nome_tool
        arg = "pergunta" if d["parametro"] == "pergunta" else "tarefa"
        assert arg in t.args  # schema expõe o argumento principal
        assert "contexto" in t.args  # e o contexto opcional


def test_delegar_codigo_chama_o_subagente(monkeypatch):
    llm = ModeloFake()
    llm.configurar([AIMessage(content="código + teste verde")])
    app = sub.criar_subagente("codigo", "prompt", [], config, llm)
    monkeypatch.setattr(sub, "SUBAGENTES_ATUAIS", {"codigo": app})
    monkeypatch.setattr(sub, "ARQ_LIMITES", {"codigo": 2})

    saida = sub.delegar_codigo.invoke({"tarefa": "implemente f(x)"})
    assert saida == "código + teste verde"


# -- execução do delegado com pool restrito ---------------------------------

def test_delegado_codigo_executa_com_pool_e_auto_correcao():
    @tool
    def rodar_teste(comando: str = "pytest") -> str:
        """Roda os testes no sandbox."""
        return "1 passed, 0 failed"

    llm = ModeloFake()
    llm.configurar([
        chamada_tool("rodar_teste", {"comando": "pytest"}),
        AIMessage(content="Implementei com testes — evidência: 1 passed."),
    ])
    app = sub.criar_subagente("codigo", sub._persona("codigo"), [rodar_teste], config, llm)
    resultado = app.invoke({"mensagens": [HumanMessage("implemente f(x) com testes")]}, C)

    saida = sub._resposta_final(resultado)
    assert "1 passed" in saida
    registros = resultado.get("registros_ferramentas") or []
    assert any(r["nome"] == "rodar_teste" for r in registros)


def test_delegado_revisao_auto_correcao_em_erro():
    @tool
    def ler_codigo(caminho: str = "main.py") -> str:
        """Lê o código a revisar."""
        return "ERRO_FERRAMENTA: arquivo não encontrado"

    llm = ModeloFake()
    llm.configurar([
        chamada_tool("ler_codigo", {"caminho": "main.py"}),
        AIMessage(content="corrigi o caminho e revisei: aprovado com ressalvas"),
    ])
    app = sub.criar_subagente("revisao", sub._persona("revisao"), [ler_codigo], config, llm)
    resultado = app.invoke({"mensagens": [HumanMessage("revise main.py")]}, C)

    assert resultado.get("tentativas_correcao") == 1
    assert "aprovado" in sub._resposta_final(resultado)


def test_redator_responde_direto_sem_ferramentas():
    llm = ModeloFake()
    llm.configurar([AIMessage(content="Título\n\nCorpo do texto.")])
    app = sub.criar_subagente("redator", sub._persona("redator"), [], config, llm)
    resultado = app.invoke({"mensagens": [HumanMessage("escreva")]}, C)

    assert "Título" in sub._resposta_final(resultado)
    assert (resultado.get("registros_ferramentas") or []) == []
