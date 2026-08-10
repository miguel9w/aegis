"""Testes do grafo LangGraph: roteamento, auto-correção e persistência."""

from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import PrivateAttr

from aegis.config import Config
from aegis.grafo import montar_grafo
from aegis.memoria import criar_checkpointer_sync, criar_store_sync
from conftest import ModeloFake, basico_tools, chamada_tool


def _cfg(tmp_path) -> Config:
    c = Config()
    c.banco = tmp_path / "teste.db"
    c.thread_id = "t-otest"
    # Evita compressão/memória nos testes de roteamento
    c.limiar_compressao = 100
    c.memoria_ativa = True
    return c


def _app(tmp_path, modelo, store=True):
    cfg = _cfg(tmp_path)
    checkpointer = criar_checkpointer_sync(cfg.banco)
    st = criar_store_sync(cfg.banco) if store else None
    return montar_grafo(modelo, basico_tools(), checkpointer=checkpointer, store=st, cfg=cfg), cfg


# ---------------------------------------------------------------------
# Roteamento: agente → ferramenta → agente
# ---------------------------------------------------------------------

def test_fluxo_ferramenta_sucesso(tmp_path):
    modelo = ModeloFake()
    modelo.configurar([
        chamada_tool("calculadora", {"expressao": "2 + 2"}, id_chamada="call_a"),
        AIMessage(content="O resultado é 4."),
    ])
    app, cfg = _app(tmp_path, modelo)
    resultado = app.invoke(
        {"mensagens": [HumanMessage("calcule")],
         "metadados_sessao": {"thread_id": cfg.thread_id}},
        config={"configurable": {"thread_id": cfg.thread_id}},
    )
    # 1) a ferramenta executou e seu ToolMessage está no histórico
    tipos_tool = [m for m in resultado["mensagens"] if isinstance(m, ToolMessage)]
    assert tipos_tool, "ToolMessage não retornado"
    assert "4" in tipos_tool[0].content
    # 2) a resposta final é a segunda resposta do modelo
    assert resultado["mensagens"][-1].content == "O resultado é 4."
    # 3) logging de ferramenta registrado para a TUI
    registros = resultado["registros_ferramentas"]
    assert registros and registros[0]["nome"] == "calculadora"
    assert registros[0]["erro"] is False


# ---------------------------------------------------------------------
# Auto-correção: erro → reflexão → nova tentativa
# ---------------------------------------------------------------------

def test_fluxo_auto_correcao(tmp_path):
    modelo = ModeloFake()
    modelo.configurar([
        # chamada inicial que falha (comando_sandbox com binário inexistente)
        chamada_tool("comando_sandbox", {"comando": "comando_que_nao_existe_zzz", "timeout": 5},
                     id_chamada="call_1"),
        # reflexão decide reformular com uma ferramenta válida (calculadora)
        chamada_tool("calculadora", {"expressao": "10 - 4"}, id_chamada="call_2"),
        # resposta final após sucesso
        AIMessage(content="Após corrigir, o resultado é 6."),
    ])
    app, cfg = _app(tmp_path, modelo)
    resultado = app.invoke(
        {"mensagens": [HumanMessage("roda algo")],
         "metadados_sessao": {"thread_id": cfg.thread_id}},
        config={"configurable": {"thread_id": cfg.thread_id}},
    )
    # passou pelo reflexão: tentativas incrementadas
    assert resultado["tentativas_correcao"] == 1, "auto-correção não disparou"
    # erros capturados
    assert resultado["erros_ferramenta"], "erro de ferramenta não registrado"
    # as duas ferramentas executaram (a que falhou + a corrigida)
    nomes = {r["nome"] for r in resultado["registros_ferramentas"]}
    # (comando_sandbox erro é contabilizado como ferramenta executada) + calculadora 10-4
    assert any(r["nome"] == "calculadora" and "6" in r["resultado"]
               for r in resultado["registros_ferramentas"])
    # resposta final
    assert resultado["mensagens"][-1].content == "Após corrigir, o resultado é 6."


def test_auto_correcao_respeita_limite(tmp_path):
    """Com modelo sempre falhando, o loop para após max_tentativas."""
    cfg = _cfg(tmp_path)
    cfg.max_tentativas_correcao = 2
    checkpointer = criar_checkpointer_sync(cfg.banco)
    app = montar_grafo(_modelo_sempre_erro(),
                       basico_tools(), checkpointer=checkpointer, store=criar_store_sync(cfg.banco), cfg=cfg)
    resultado = app.invoke(
        {"mensagens": [HumanMessage("falha")],
         "metadados_sessao": {"thread_id": cfg.thread_id}},
        config={"configurable": {"thread_id": cfg.thread_id}},
    )
    assert resultado["tentativas_correcao"] <= cfg.max_tentativas_correcao


def _modelo_sempre_erro():
    m = ModeloFake()
    m.configurar([
        chamada_tool("comando_sandbox", {"comando": "cmdo_x_zzz"}),   # falha
        chamada_tool("comando_sandbox", {"comando": "cmdo_x_zzz"}),   # reflexão tenta de novo (falha)
        chamada_tool("comando_sandbox", {"comando": "cmdo_x_zzz"}),   # ...
        AIMessage(content="Não consegui executar."),                    # desiste
    ])
    return m


# ---------------------------------------------------------------------
# Persistência: checkpointer retoma conversa pelo mesmo thread_id
# ---------------------------------------------------------------------

def test_checkpointer_retoma_conversa(tmp_path):
    cfg = _cfg(tmp_path)
    checkpointer = criar_checkpointer_sync(cfg.banco)
    store = criar_store_sync(cfg.banco)
    cfg.limiar_compressao = 100

    def _nova_app():
        m = ModeloFake()
        m.configurar([AIMessage(content="Primeira resposta.")])
        return montar_grafo(m, basico_tools(), checkpointer=checkpointer, store=store, cfg=cfg)

    entrada = {
        "mensagens": [HumanMessage("oi")],
        "metadados_sessao": {"thread_id": "thread-a"},
    }
    config = {"configurable": {"thread_id": "thread-a"}}

    # Primeiro turno
    r1 = _nova_app().invoke(entrada, config=config)
    # Segundo turno no MESMO app/checkpointer
    m2 = ModeloFake()
    m2.configurar([AIMessage(content="Segunda resposta.")])
    app2 = montar_grafo(m2, basico_tools(), checkpointer=checkpointer, store=store, cfg=cfg)
    entrada2 = {"mensagens": [HumanMessage("e agora?")],
                "metadados_sessao": {"thread_id": "thread-a"}}
    r2 = app2.invoke(entrada2, config=config)

    historico_texto = [str(m.content) for m in r2["mensagens"]]
    assert len(r2["mensagens"]) >= 4  # oi + r1 + e agora? + r2
    assert any("Primeira resposta" in t for t in historico_texto)  # contexto retomado


# ---------------------------------------------------------------------
# Compressão de contexto
# ---------------------------------------------------------------------

def test_compressao_trunca_historico(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.limiar_compressao = 3   # comprime a partir de 3 mensagens
    cfg.manter_apos_compressao = 2
    checkpointer = criar_checkpointer_sync(cfg.banco)
    modelo = ModeloFake()
    modelo.configurar([
        AIMessage(content="resp1"),
        AIMessage(content="resp2"),
        AIMessage(content="resp3"),
    ])
    app = montar_grafo(modelo, basico_tools(), checkpointer=checkpointer,
                       store=criar_store_sync(cfg.banco), cfg=cfg)
    config = {"configurable": {"thread_id": "thread-c"}}
    # turno 1
    app.invoke({"mensagens": [HumanMessage("m1")]}, config=config)
    # turno 2 (histórico longo p/ tocar compressão)
    r = app.invoke({"mensagens": [HumanMessage("m2")]}, config=config)
    # após compressão, o resumo deve ficar no estado
    assert r.get("contexto_comprimido"), "compressão não produziu resumo"


# ---------------------------------------------------------------------
# Reasoning_content (DeepSeek/Zen): devolver o raciocínio quando há tool_calls
# ---------------------------------------------------------------------

class ModeloComRaciocinioFake(BaseChatModel):
    """Emula o DeepSeek/Zen no modo thinking: o `_generate` dispara o
    callback `on_chat_model_stream` com um chunk de reasoning_content (como o
    provider faz no streaming) e devolve tool_calls."""

    @property
    def _llm_type(self) -> str:
        return "fake-raciocinio"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        if run_manager is not None:
            run_manager.on_llm_new_token(
                "",
                chunk=ChatGenerationChunk(
                    message=AIMessageChunk(
                        content="",
                        additional_kwargs={"reasoning_content": "vou ler e listar"},
                    )
                ),
            )
        msg = AIMessage(content="", tool_calls=[
            {"name": "ler_arquivo", "args": {"caminho": "aegis/config.py"},
             "id": "call_1", "type": "tool_call"},
        ])
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs) -> "ModeloComRaciocinioFake":
        return self


def test_no_agente_devolve_reasoning_quando_ha_tool_calls():
    """O provider exige o reasoning_content de volta quando a resposta tem
    tool_calls; o agregador do langchain o descarta — o nó captura nos chunks
    e injeta nos additional_kwargs (fix do HTTP 400 do zen)."""
    from aegis.config import Config as _C
    from aegis.nos import fabricar_nos
    cfg = _C()
    cfg.multiagente_ativos = False
    nos = fabricar_nos(ModeloComRaciocinioFake(), [], None, cfg)
    saida = nos["no_agente"]({
        "mensagens": [HumanMessage(content="leia e liste")],
        "metadados_sessao": {"thread_id": "t-razao"},
    })
    msg = saida["mensagens"][0]
    assert msg.tool_calls  # resposta com chamadas → provider exige o raciocínio
    assert msg.additional_kwargs.get("reasoning_content") == "vou ler e listar"


def test_no_agente_sem_tool_calls_nao_injeta_reasoning():
    """Sem tool_calls o provider não exige o campo — e não deve vazar."""
    from aegis.config import Config as _C
    from aegis.nos import fabricar_nos

    class ModeloRespostaDireta(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "fake-direta"

        def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
            if run_manager is not None:
                run_manager.on_llm_new_token(
                    "",
                    chunk=ChatGenerationChunk(
                        message=AIMessageChunk(
                            content="",
                            additional_kwargs={"reasoning_content": "pensando..."},
                        )
                    ),
                )
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="ok direto"))])

        def bind_tools(self, tools, **kwargs) -> "ModeloRespostaDireta":
            return self

    cfg = _C()
    cfg.multiagente_ativos = False
    nos = fabricar_nos(ModeloRespostaDireta(), [], None, cfg)
    saida = nos["no_agente"]({
        "mensagens": [HumanMessage(content="oi")],
        "metadados_sessao": {"thread_id": "t-direta"},
    })
    msg = saida["mensagens"][0]
    assert not msg.tool_calls
    assert "reasoning_content" not in msg.additional_kwargs

# ---------------------------------------------------------------------
# C1 — Reflexão pós-turno: lições aprendidas na Store + recall
# ---------------------------------------------------------------------

def _resposta_licoes(licoes: list[dict]):
    import json
    return AIMessage(content=json.dumps({"licoes": licoes}, ensure_ascii=False))


class ModeloEspiao(BaseChatModel):
    """Fake que CAPTURA as mensagens recebidas (para inspecionar o system)."""

    _chamadas: list = PrivateAttr(default_factory=list)
    _saida: str = PrivateAttr(default="ok.")

    def __init__(self, saida: str | None = None):
        super().__init__()
        if saida:
            self._saida = saida

    @property
    def _llm_type(self) -> str:
        return "fake-espiao"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs) -> ChatResult:
        self._chamadas.append(messages)
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=self._saida))])

    def bind_tools(self, tools, **kwargs) -> "ModeloEspiao":
        return self

    @property
    def chamadas(self) -> list:
        return self._chamadas


def test_reflexao_pos_turno_grava_licoes(tmp_path):
    """Turno com ferramentas → reflexão extrai e grava lições na Store."""
    from aegis.memoria import namespace_licoes
    modelo = ModeloFake()
    modelo.configurar([
        chamada_tool("calculadora", {"expressao": "2 + 2"}, id_chamada="call_a"),
        AIMessage(content="Resultado: 4."),
        _resposta_licoes([{"texto": "sempre validar a entrada antes de calcular", "prioridade": "media"}]),
    ])
    app, cfg = _app(tmp_path, modelo)
    resultado = app.invoke(
        {"mensagens": [HumanMessage("calcule 2+2")],
         "metadados_sessao": {"thread_id": cfg.thread_id}},
        config={"configurable": {"thread_id": cfg.thread_id}},
    )
    assert resultado.get("licoes_turno"), "lição não registrada no estado do turno"
    itens = list(criar_store_sync(cfg.banco).search(namespace_licoes()))
    assert itens, "lição não gravada na Store"
    valor = itens[0].value
    assert "validar" in valor["texto"]
    assert valor["prioridade"] == "media"


def test_reflexao_sem_ferramentas_nao_grava(tmp_path):
    """Turno sem ferramentas → nenhuma lição (zero custo, nada gravado)."""
    from aegis.memoria import namespace_licoes
    modelo = ModeloFake()
    modelo.configurar([AIMessage(content="oi!")])
    app, cfg = _app(tmp_path, modelo)
    resultado = app.invoke(
        {"mensagens": [HumanMessage("oi")],
         "metadados_sessao": {"thread_id": cfg.thread_id}},
        config={"configurable": {"thread_id": cfg.thread_id}},
    )
    assert not resultado.get("licoes_turno")
    assert not list(criar_store_sync(cfg.banco).search(namespace_licoes()))


def test_no_reflexao_pos_turno_marca_prioridade_alta_na_repeticao(tmp_path):
    """A MESMA ferramenta falhando ≥2× no turno → lição com prioridade alta."""
    from aegis.config import Config as _C
    from aegis.memoria import namespace_licoes
    from aegis.nos import fabricar_nos
    modelo = ModeloFake()
    modelo.configurar([
        _resposta_licoes([{"texto": "não repetir o mesmo comando que falha", "prioridade": "baixa"}]),
    ])
    cfg = _C()
    cfg.memoria_ativa = True
    store = criar_store_sync(tmp_path / "prio.db")
    nos = fabricar_nos(modelo, [], store, cfg)
    nos["no_reflexao_pos_turno"]({
        "registros_ferramentas": [
            {"nome": "comando_sandbox", "resultado": "ERRO_FERRAMENTA: zzz", "erro": True},
            {"nome": "comando_sandbox", "resultado": "ERRO_FERRAMENTA: zzz", "erro": True},
        ],
    })
    itens = list(store.search(namespace_licoes()))
    assert itens and itens[0].value["prioridade"] == "alta", "repetição deveria elevar a prioridade"


def test_recuperar_licoes_por_relevancia(tmp_path):
    """Recall: só lições relevantes à consulta voltam (ranqueamento IDF)."""
    from aegis.memoria import namespace_licoes
    from aegis.recuperacao import recuperar_licoes
    store = criar_store_sync(tmp_path / "rec.db")
    store.put(namespace_licoes(), "l1",
              {"texto": "usar comandos curtos ao gravar arquivos grandes", "prioridade": "alta"})
    store.put(namespace_licoes(), "l2",
              {"texto": "receita de bolo de chocolate com 3 ovos", "prioridade": "baixa"})
    bloco = recuperar_licoes(store, "como gravar um arquivo grande?")
    assert "comandos curtos" in bloco
    assert "bolo" not in bloco


def test_no_agente_injeta_licoes_relevantes_no_system(tmp_path):
    """Lições da Store relevantes à pergunta entram no system do turno."""
    from aegis.memoria import namespace_licoes
    from aegis.nos import fabricar_nos
    store = criar_store_sync(tmp_path / "inj2.db")
    store.put(namespace_licoes(), "l1",
              {"texto": "nunca gerar arquivo inteiro em um único comando", "prioridade": "alta"})
    espiao = ModeloEspiao()
    cfg = _cfg(tmp_path)
    nos = fabricar_nos(espiao, [], store, cfg)
    nos["no_agente"]({
        "mensagens": [HumanMessage("como gerar um arquivo grande?")],
        "metadados_sessao": {"thread_id": "t-esp"},
    })
    system = espiao.chamadas[0][0]
    assert "Lições aprendidas" in system.content
    assert "nunca gerar arquivo inteiro" in system.content


def test_no_agente_sem_licoes_relevantes_nao_injeta_bloco(tmp_path):
    """Pergunta sem relação → nenhum bloco de lições no system (sem ruído)."""
    from aegis.memoria import namespace_licoes
    from aegis.nos import fabricar_nos
    store = criar_store_sync(tmp_path / "inj3.db")
    store.put(namespace_licoes(), "l1",
              {"texto": "receita de bolo de chocolate com 3 ovos", "prioridade": "baixa"})
    espiao = ModeloEspiao()
    cfg = _cfg(tmp_path)
    nos = fabricar_nos(espiao, [], store, cfg)
    nos["no_agente"]({
        "mensagens": [HumanMessage("qual a capital da França?")],
        "metadados_sessao": {"thread_id": "t-esp2"},
    })
    system = espiao.chamadas[0][0]
    assert "Lições aprendidas" not in system.content


# ---------------------------------------------------------------------
# C2 — Plan-and-execute: plano no estado + replan após erro
# ---------------------------------------------------------------------

def _resposta_plano(passos: list[dict]) -> AIMessage:
    import json
    return AIMessage(content=json.dumps({"plano": passos}, ensure_ascii=False))


def test_pergunta_simples_nao_gera_plano(tmp_path):
    """Pergunta curta → fluxo legado: sem plano, sem chamada extra ao LLM."""
    from aegis.nos import _precisa_plano
    assert _precisa_plano("calcule 2+2") is False
    assert _precisa_plano("oi") is False
    assert _precisa_plano("qual a capital da França?") is False


def test_tarefa_complexa_dispara_plano(tmp_path):
    """Pergunta com múltiplos passos → heurística ativa (sem LLM)."""
    from aegis.nos import _precisa_plano
    assert _precisa_plano("crie o arquivo X, depois rode os testes e por fim faça o push") is True
    assert _precisa_plano("implemente a ferramenta com testes e documentação") is True
    assert _precisa_plano("analise o projeto, liste as dependências e sugira melhorias") is True


def test_plano_gerado_e_injetado_no_system(tmp_path):
    """Tarefa complexa → plano no estado E bloco '## Plano ativo' no system."""
    from aegis.nos import fabricar_nos
    plano_json = _resposta_plano([
        {"passo": "listar arquivos", "objetivo": "ver o repo"},
        {"passo": "rodar testes", "objetivo": "validar"},
    ])
    espiao = ModeloEspiao(saida=plano_json.content)
    cfg = _cfg(tmp_path)
    nos = fabricar_nos(espiao, [], None, cfg)
    saida = nos["no_planejamento"]({
        "mensagens": [HumanMessage("implemente a ferramenta X, rode os testes e faça o push")],
        "metadados_sessao": {"thread_id": "t-plano"},
    })
    assert saida["plano"], "plano não gerado"
    assert all(p["status"] == "pendente" for p in saida["plano"])
    assert saida["plano_considerado"] is True

    # injeção no system do nó agente
    espiao2 = ModeloEspiao(saida="resp")
    nos2 = fabricar_nos(espiao2, [], None, cfg)
    nos2["no_agente"]({
        "mensagens": [HumanMessage("implemente a ferramenta X")],
        "metadados_sessao": {"thread_id": "t-plano2"},
        "plano": saida["plano"],
    })
    system = espiao2.chamadas[0][0]
    assert "## Plano ativo" in system.content
    assert "listar arquivos" in system.content


def test_plano_nao_chama_llm_em_pergunta_simples(tmp_path):
    """Heurística negativa → nó de planejamento retorna sem invocar o LLM."""
    from aegis.nos import fabricar_nos
    espiao = ModeloEspiao(saida="nem deveria ser chamado")
    cfg = _cfg(tmp_path)
    nos = fabricar_nos(espiao, [], None, cfg)
    saida = nos["no_planejamento"]({
        "mensagens": [HumanMessage("oi")],
        "metadados_sessao": {"thread_id": "t-simples"},
    })
    assert not espiao.chamadas, "LLM não deveria ser chamado para pergunta simples"
    assert not saida.get("plano")


def test_replanejamento_marca_passo_falho(tmp_path):
    """LLM sem plano válido → fallback mantém o plano com o passo marcado falho."""
    from aegis.nos import fabricar_nos
    plano = [
        {"passo": "executar comando", "objetivo": "rodar", "status": "pendente"},
        {"passo": "validar saída", "objetivo": "verificar", "status": "pendente"},
    ]
    espiao = ModeloEspiao(saida="falhei, mas sem json")  # LLM não devolve plano → fallback
    cfg = _cfg(tmp_path)
    nos = fabricar_nos(espiao, [], None, cfg)
    saida = nos["no_replanejamento"]({
        "mensagens": [HumanMessage("roda o comando")],
        "metadados_sessao": {"thread_id": "t-replan"},
        "plano": plano,
        "erros_ferramenta": ["ERRO_FERRAMENTA: comando falhou"],
    })
    novo_plano = saida["plano"]
    assert novo_plano[0]["status"] == "falhou"
    assert novo_plano[1]["status"] == "pendente"


def test_replanejamento_reformula_com_llm(tmp_path):
    """LLM devolve plano revisado → o passo que falhou sai; continuação fica."""
    import json
    from aegis.nos import fabricar_nos
    plano = [
        {"passo": "executar comando", "objetivo": "rodar", "status": "pendente"},
        {"passo": "validar saída", "objetivo": "verificar", "status": "pendente"},
    ]
    espiao = ModeloEspiao(saida=json.dumps({"plano": [
        {"passo": "validar saída", "objetivo": "verificar"},
    ]}, ensure_ascii=False))
    cfg = _cfg(tmp_path)
    nos = fabricar_nos(espiao, [], None, cfg)
    saida = nos["no_replanejamento"]({
        "mensagens": [HumanMessage("roda o comando")],
        "metadados_sessao": {"thread_id": "t-replan2"},
        "plano": plano,
        "erros_ferramenta": ["ERRO_FERRAMENTA: comando falhou"],
    })
    plano_novo = saida["plano"]
    assert [p["passo"] for p in plano_novo] == ["validar saída"]
    assert all(p["status"] == "pendente" for p in plano_novo)
