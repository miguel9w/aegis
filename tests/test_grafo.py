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
            chamada_tool("calculadora", {"expressao": "2+2"}, id_chamada="call_a"),
            AIMessage(content="Resultado: 4."),
            _resposta_verificacao("ok", [{"fonte": "calculadora", "conferida": True,
                                          "observacao": "bate"}]),
            _resposta_licoes([{"texto": "sempre validar entrada antes de calcular", "prioridade": "media"}]),
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


# ---------------------------------------------------------------------
# C3 — Verify-then-answer: evidência antes de confirmar
# ---------------------------------------------------------------------

def _resposta_verificacao(veredito: str, evidencias: list[dict]) -> AIMessage:
    import json
    return AIMessage(content=json.dumps(
        {"veredito": veredito, "evidencias": evidencias}, ensure_ascii=False))


def test_turno_com_ferramenta_gera_evidencia(tmp_path):
    """Turno com ferramenta → verificação anexa evidência e segue ao fim."""
    from aegis.memoria import namespace_licoes
    modelo = ModeloFake()
    modelo.configurar([
        chamada_tool("calculadora", {"expressao": "2 + 2"}, id_chamada="call_a"),
        AIMessage(content="Resultado: 4."),
        _resposta_verificacao("ok", [{"fonte": "calculadora", "conferida": True,
                                      "observacao": "resultado bate"}]),
        _resposta_licoes([]),
    ])
    app, cfg = _app(tmp_path, modelo)
    resultado = app.invoke(
        {"mensagens": [HumanMessage("calcule 2+2")],
         "metadados_sessao": {"thread_id": cfg.thread_id}},
        config={"configurable": {"thread_id": cfg.thread_id}},
    )
    assert resultado.get("evidencias"), "evidência não anexada após verificação"
    assert resultado["evidencias"][0]["conferida"] is True
    assert resultado.get("verificacao_veredito") == "ok"


def test_divergencia_dispara_correcao(tmp_path):
    """Veredito divergente → agente corrige a resposta (uma única vez)."""
    modelo = ModeloFake()
    modelo.configurar([
        chamada_tool("calculadora", {"expressao": "2 + 2"}, id_chamada="call_a"),
        AIMessage(content="Resultado: 2."),  # resposta errada
        _resposta_verificacao("divergencia", [{"fonte": "calculadora", "conferida": False,
                                               "observacao": "calculadora diz 4"}]),
        AIMessage(content="Resultado correto: 4."),  # correção
        _resposta_licoes([]),
    ])
    app, cfg = _app(tmp_path, modelo)
    resultado = app.invoke(
        {"mensagens": [HumanMessage("calcule 2+2")],
         "metadados_sessao": {"thread_id": cfg.thread_id}},
        config={"configurable": {"thread_id": cfg.thread_id}},
    )
    assert resultado["mensagens"][-1].content == "Resultado correto: 4."
    assert resultado.get("verificacoes_realizadas") == 1
    assert resultado["evidencias"][0]["conferida"] is False


def test_sem_ferramentas_nao_verifica(tmp_path):
    """Turno sem ferramentas → verificação não chama LLM adicional."""
    from aegis.nos import fabricar_nos
    espiao = ModeloEspiao(saida="resposta simples")
    cfg = _cfg(tmp_path)
    nos = fabricar_nos(espiao, [], None, cfg)
    saida = nos["no_verificar"]({
        "mensagens": [HumanMessage("oi")],
        "metadados_sessao": {"thread_id": "t-sem-tools"},
    })
    assert not espiao.chamadas, "verificação não deveria chamar LLM sem ferramentas"
    assert not saida


def test_modo_estrita_desligada_nao_verifica(tmp_path):
    """verificacao_estrita=False → verificação inativa mesmo com ferramentas."""
    from aegis.nos import fabricar_nos
    cfg = _cfg(tmp_path)
    cfg.verificacao_estrita = False
    espiao = ModeloEspiao(saida="não deveria ser chamado")
    nos = fabricar_nos(espiao, [], None, cfg)
    saida = nos["no_verificar"]({
        "mensagens": [HumanMessage("roda o comando")],
        "metadados_sessao": {"thread_id": "t-estrita-off"},
        "registros_ferramentas": [{"nome": "comando_sandbox", "resultado": "ok", "erro": False}],
    })
    assert not espiao.chamadas
    assert not saida


# ---------------------------------------------------------------------
# C4 — Memória estrutural: resumo de sessão + decisões + recall hierárquico
# ---------------------------------------------------------------------

def _resposta_resumo(texto: str, decisoes: list[str]) -> AIMessage:
    import json
    return AIMessage(content=json.dumps(
        {"resumo": texto, "decisoes": decisoes}, ensure_ascii=False))


def test_resumo_sessao_gravado_apos_intervalo(tmp_path):
    """Turno com ≥ intervalo de mensagens → resumo e decisões na Store."""
    import json as _json
    from aegis.memoria import namespace_resumos, namespace_decisoes
    from aegis.nos import fabricar_nos
    cfg = _cfg(tmp_path)
    store = criar_store_sync(tmp_path / "res.db")
    espiao = ModeloEspiao(saida=_json.dumps(
        {"resumo": "sessão sobre instalação", "decisoes": ["usar pacman para instalar"]},
        ensure_ascii=False))
    nos = fabricar_nos(espiao, [], store, cfg)
    mensagens = [HumanMessage(f"pergunta {i}") for i in range(5)]
    saida = nos["no_memoria_estrutural"]({
        "mensagens": mensagens,
        "metadados_sessao": {"thread_id": "t-res"},
    })
    assert "instalação" in saida.get("resumo_sessao", "")
    assert saida.get("decisoes_turno") == ["usar pacman para instalar"]
    assert store.get(namespace_resumos("t-res"), "resumo") is not None
    assert store.get(namespace_decisoes("t-res"), "recentes") is not None


def test_memoria_estrutural_ignora_turno_curto(tmp_path):
    """Menos que o intervalo → zero chamadas de LLM."""
    from aegis.nos import fabricar_nos
    cfg = _cfg(tmp_path)
    espiao = ModeloEspiao(saida="não deveria")
    nos = fabricar_nos(espiao, [], None, cfg)
    nos["no_memoria_estrutural"]({
        "mensagens": [HumanMessage("oi")],
        "metadados_sessao": {"thread_id": "t-curto"},
    })
    assert not espiao.chamadas


def test_recuperar_contexto_hierarquia(tmp_path):
    """Recall hierárquico: perfil → lições → resumo → decisões, na ordem."""
    from aegis.recuperacao import recuperar_contexto_para_system
    store = criar_store_sync(tmp_path / "ctx.db")
    store.put(("aegis", "perfil"), "perfil", {"nome": "Fulano", "stack": "Next"})
    store.put(("aegis", "licoes"), "l1", {"texto": "verificar o caminho antes de gravar", "prioridade": "media"})
    store.put(("aegis", "resumos", "t-ctx"), "resumo", {"texto": "configuramos o sandbox", "ts": "x"})
    store.put(("aegis", "decisoes", "t-ctx"), "recentes", {"lista": ["usar uv em vez de pip"]})
    bloco = recuperar_contexto_para_system(store, "t-ctx", "como gravar arquivos no projeto?")
    # ordem hierárquica dos cabeçalhos
    import re
    pos = [bloco.index(h) for h in ("## Perfil", "## Lições", "## Resumo", "## Decisões")]
    assert pos == sorted(pos), "hierarquia fora de ordem"
    assert "Fulano" in bloco
    assert "verificar o caminho" in bloco
    assert "sandbox" in bloco
    assert "uv" in bloco


def test_recuperar_contexto_tool_registrada():
    """A tool recuperar_contexto existe e responde com o contexto da Store."""
    from aegis.ferramentas import carregar_ferramentas
    nomes = {f.name for f in carregar_ferramentas()}
    assert "recuperar_contexto" in nomes


# ---------------------------------------------------------------------
# G1 — Modo entrega: ciclo discuss → plan → execute → verify → ship
# ---------------------------------------------------------------------

def _resposta_verify_entrega(vereditos: list[dict]) -> AIMessage:
    import json
    return AIMessage(content=json.dumps({"criterios": vereditos}, ensure_ascii=False))


def _resposta_revisao(itens: list[dict]) -> AIMessage:
    """Veredito estruturado do revisor por pares (G3)."""
    import json
    return AIMessage(content=json.dumps({"itens": itens}, ensure_ascii=False))


def _executar_entrega_com_uat(app, cfg, pedido, respostas_uat, thread_id=None):
    """Invoca uma entrega até o ship e responde o UAT (G2) pergunta a pergunta
    via Command(resume); retorna o resultado final."""
    from langgraph.types import Command
    tid = thread_id or cfg.thread_id
    config = {"configurable": {"thread_id": tid}}
    r = app.invoke(
        {"mensagens": [HumanMessage(pedido)],
         "metadados_sessao": {"thread_id": tid}},
        config=config,
    )
    i = 0
    while r.get("__interrupt__"):
        assert i < len(respostas_uat), f"interrupt sem resposta programada ({i})"
        r = app.invoke(Command(resume=respostas_uat[i]), config=config)
        i += 1
    return r


def test_entrega_ciclo_completo_ordem_fases(tmp_path):
    """Pedido de entrega → fases na ordem discuss→plan→execute→verify→ship
    (invariante de ordem), com wave registrada e ship só com tudo verificado."""
    modelo = ModeloFake()
    modelo.configurar([
        _resposta_plano([{"passo": "criar tool somador", "objetivo": "entrega",
                          "status": "pendente"}]),
        chamada_tool("calculadora", {"expressao": "1 + 1"}, id_chamada="call_g"),
        AIMessage(content="Tool somador criada e testada."),
        _resposta_verify_entrega([{"indice": 0, "verificado": True,
                                   "evidencia": "tool existe e roda"}]),
        _resposta_revisao([
            {"item": "seguranca", "veredito": "aprovado", "apontamento": ""},
            {"item": "sandbox de escrita", "veredito": "aprovado", "apontamento": ""},
            {"item": "testes", "veredito": "aprovado", "apontamento": ""},
            {"item": "documentacao", "veredito": "aprovado", "apontamento": ""},
            {"item": "anti-alucinacao", "veredito": "aprovado", "apontamento": ""},
        ]),
        _resposta_licoes([]),
    ])
    app, cfg = _app(tmp_path, modelo)
    resultado = _executar_entrega_com_uat(
        app, cfg,
        "adicione a ferramenta somador com testes e push",
        ["aprovado"],
    )
    ft = resultado["fluxo_trabalho"]
    assert ft is not None and ft["fase"] == "ship", f"fase final: {ft}"
    assert ft["ship"]["criterios_verificados"] == 1
    # ordem das fases observável nos registros (fase anexada em cada execução)
    fases_vistas = [r.get("fase") for r in resultado["registros_ferramentas"] if r.get("fase")]
    assert fases_vistas, "registros sem fase (auditoria G1)"
    assert "execute" in fases_vistas
    assert resultado["commits_entrega"], "wave sem commit registrado"
    # mensagens finais: selo de ship + selo de UAT (G2)
    textos = " ".join(str(m.content) for m in resultado["mensagens"])
    assert "🛳️" in textos and "🧪" in textos


def test_tarefa_informativa_fluxo_legado_byte_identico(tmp_path):
    """Tarefa informativa → fluxo legado: fluxo_trabalho ausente, UMA chamada
    ao LLM, resposta byte-idêntica à do fluxo sem classificador."""
    modelo = ModeloEspiao(saida="Um agente é um sistema que decide o próximo passo.")
    app, cfg = _app(tmp_path, modelo)
    resultado = app.invoke(
        {"mensagens": [HumanMessage("explique o que é um agente")],
         "metadados_sessao": {"thread_id": cfg.thread_id}},
        config={"configurable": {"thread_id": cfg.thread_id}},
    )
    assert resultado["fluxo_trabalho"] is None
    assert len(modelo.chamadas) == 1, "ciclo GSD não deve rodar para pergunta informativa"
    assert resultado["mensagens"][-1].content == "Um agente é um sistema que decide o próximo passo."


def test_verify_reprovado_volta_execute_sem_ship(tmp_path):
    """verify reprova critério → volta a execute (feedback no histórico),
    NÃO ship; correção final → verify ok → ship."""
    modelo = ModeloFake()
    modelo.configurar([
        _resposta_plano([{"passo": "criar tool", "objetivo": "entrega",
                          "status": "pendente"}]),
        chamada_tool("calculadora", {"expressao": "2 + 2"}, id_chamada="call_r"),
        AIMessage(content="Tool criada."),
        _resposta_verify_entrega([{"indice": 0, "verificado": False,
                                   "evidencia": "teste ausente"}]),
        AIMessage(content="Tool criada com teste."),
        _resposta_verify_entrega([{"indice": 0, "verificado": True,
                                   "evidencia": "teste presente"}]),
        _resposta_licoes([]),
    ])
    app, cfg = _app(tmp_path, modelo)
    resultado = _executar_entrega_com_uat(
        app, cfg,
        "adicione a rotina de backup com teste e push",
        ["aprovado"],
    )
    ft = resultado["fluxo_trabalho"]
    assert ft["fase"] == "ship"
    assert ft["ship"]["criterios_verificados"] == 1
    # o feedback da verificação entrou no histórico (prova de que voltou a execute)
    textos = [str(m.content) for m in resultado["mensagens"]]
    assert any("[verificação da entrega]" in t for t in textos), "sem feedback no histórico"
    # ship só registra quando a verificação passou: ship montado no fim
    assert ft["ship"]["total_criterios"] == 1


def test_discuss_vago_pausa_com_pergunta_e_resume(tmp_path):
    """Pedido de entrega vago → no_discuss PAUSA com pergunta (interrupt);
    resposta do usuário (Command resume) → ciclo completa até ship."""
    from langgraph.types import Command
    modelo = ModeloFake()
    modelo.configurar([
        _resposta_plano([{"passo": "implementar buscar", "objetivo": "entrega",
                          "status": "pendente"}]),
        chamada_tool("calculadora", {"expressao": "3 + 3"}, id_chamada="call_d"),
        AIMessage(content="Ferramenta buscar implementada."),
        _resposta_verify_entrega([{"indice": 0, "verificado": True,
                                   "evidencia": "busca retorna resultados"}]),
        _resposta_licoes([]),
    ])
    app, cfg = _app(tmp_path, modelo)
    config = {"configurable": {"thread_id": cfg.thread_id}}
    primeiro = app.invoke(
        {"mensagens": [HumanMessage("crie a ferramenta buscar")],
         "metadados_sessao": {"thread_id": cfg.thread_id}},
        config=config,
    )
    interrupcoes = primeiro.get("__interrupt__")
    assert interrupcoes, "pedido vago deveria pausar em discuss"
    pergunta = interrupcoes[0].value
    assert "?" in pergunta or "detalhe" in str(pergunta).lower()
    # resume com a especificação → ciclo segue e termina em ship
    final = app.invoke(
        Command(resume="deve buscar arquivos por nome na pasta artefatos, com teste"),
        config=config,
    )
    # após o ship, o UAT (G2) pergunta o critério → responde e conclui
    final2 = app.invoke(Command(resume="aprovado"), config=config)
    assert final2["fluxo_trabalho"]["fase"] == "ship"
    assert "🛳️" in final2["mensagens"][-2].content or "🛳️" in str(final2["mensagens"][-1].content)


# ---------------------------------------------------------------------
# G3 — Revisão por pares antes do ship
# ---------------------------------------------------------------------

def test_revisao_bloqueante_volta_execute_e_corrige(tmp_path):
    """Item bloqueante reprovado na revisão → volta a execute com o apontamento
    como feedback; após a correção, revisão aprovada → ship."""
    modelo = ModeloFake()
    modelo.configurar([
        _resposta_plano([{"passo": "criar tool", "objetivo": "entrega",
                          "status": "pendente"}]),
        chamada_tool("calculadora", {"expressao": "5 + 5"}, id_chamada="call_r1"),
        AIMessage(content="Entregue v1."),
        _resposta_verify_entrega([{"indice": 0, "verificado": True,
                                   "evidencia": "roda"}]),
        _resposta_revisao([{"item": "seguranca", "veredito": "reprovado",
                            "apontamento": "comando roda sem validar entrada"}],
                          ),
        AIMessage(content="Corrigido: valida entrada agora."),
        _resposta_verify_entrega([{"indice": 0, "verificado": True,
                                   "evidencia": "roda e valida"}]),
        _resposta_revisao([
            {"item": "seguranca", "veredito": "aprovado", "apontamento": ""},
            {"item": "sandbox de escrita", "veredito": "aprovado", "apontamento": ""},
            {"item": "testes", "veredito": "aprovado", "apontamento": ""},
            {"item": "documentacao", "veredito": "aprovado", "apontamento": ""},
            {"item": "anti-alucinacao", "veredito": "aprovado", "apontamento": ""},
        ]),
        _resposta_licoes([]),
    ])
    app, cfg = _app(tmp_path, modelo)
    r = _executar_entrega_com_uat(
        app, cfg, "adicione a rotina de backup com teste e push", ["aprovado"],
    )
    ft = r["fluxo_trabalho"]
    assert ft["fase"] == "ship"
    assert ft["correcoes"] >= 1, "revisão reprovada deveria contar como correção"
    rev = r["revisao_entrega"]
    assert rev and rev["itens"], "veredito estruturado da revisão no estado"
    assert rev["itens"][0]["item"] == "seguranca"
    assert "valida entrada" in " ".join(str(m.content) for m in r["mensagens"])


def test_revisao_aprovada_vai_direto_ship_sem_perguntas(tmp_path):
    """Tudo aprovado no checklist → ship direto (sem pergunta ao usuário até
    o UAT); o selo do ship cita os itens aprovados da revisão."""
    modelo = ModeloFake()
    modelo.configurar([
        _resposta_plano([{"passo": "criar tool", "objetivo": "entrega",
                          "status": "pendente"}]),
        chamada_tool("calculadora", {"expressao": "6 + 6"}, id_chamada="call_r2"),
        AIMessage(content="Entregue."),
        _resposta_verify_entrega([{"indice": 0, "verificado": True,
                                   "evidencia": "testes passam"}]),
        _resposta_revisao([
            {"item": "seguranca", "veredito": "aprovado", "apontamento": ""},
            {"item": "sandbox de escrita", "veredito": "aprovado", "apontamento": ""},
            {"item": "testes", "veredito": "aprovado", "apontamento": ""},
            {"item": "documentacao", "veredito": "aprovado", "apontamento": ""},
            {"item": "anti-alucinacao", "veredito": "aprovado", "apontamento": ""},
        ]),
        _resposta_licoes([]),
    ])
    app, cfg = _app(tmp_path, modelo)
    # sem resposta para o UAT por engano: o 1º invoke deve parar SÓ no UAT
    # (prova de que não houve pergunta antes do ship)
    from langgraph.types import Command
    config = {"configurable": {"thread_id": cfg.thread_id}}
    r1 = app.invoke(
        {"mensagens": [HumanMessage("adicione a rotina de backup com teste e push")],
         "metadados_sessao": {"thread_id": cfg.thread_id}},
        config=config,
    )
    assert r1.get("__interrupt__"), "fluxo deveria pausar apenas no UAT"
    assert "UAT" in str(r1["__interrupt__"]), "interrupt prematuro antes do UAT"
    r2 = app.invoke(Command(resume="aprovado"), config=config)
    ft = r2["fluxo_trabalho"]
    assert ft["fase"] == "ship" and ft.get("correcoes", 0) <= 2
    rev = r2["revisao_entrega"]
    assert len(rev["itens"]) == 5 and all(
        i["veredito"] == "aprovado" for i in rev["itens"])
    # o resumo do ship cita a revisão aprovada
    textos = " ".join(str(m.content) for m in r2["mensagens"])
    assert "Revisão" in textos and "5/5" in textos


def test_revisao_auditoria_no_estado_e_registros(tmp_path):
    """`revisao_entrega` persiste no estado final (auditoria replayável)."""
    modelo = ModeloFake()
    modelo.configurar([
        _resposta_plano([{"passo": "criar tool", "objetivo": "entrega",
                          "status": "pendente"}]),
        chamada_tool("calculadora", {"expressao": "7 + 7"}, id_chamada="call_r3"),
        AIMessage(content="Entregue."),
        _resposta_verify_entrega([{"indice": 0, "verificado": True,
                                   "evidencia": "ok"}]),
        _resposta_revisao([
            {"item": "seguranca", "veredito": "aprovado", "apontamento": ""},
            {"item": "sandbox de escrita", "veredito": "aprovado", "apontamento": ""},
            {"item": "testes", "veredito": "aprovado", "apontamento": ""},
            {"item": "documentacao", "veredito": "aprovado", "apontamento": ""},
            {"item": "anti-alucinacao", "veredito": "aprovado", "apontamento": ""},
        ]),
        _resposta_licoes([]),
    ])
    app, cfg = _app(tmp_path, modelo)
    r = _executar_entrega_com_uat(
        app, cfg, "adicione a rotina de backup com teste e push", ["aprovado"],
    )
    rev = r["revisao_entrega"]
    assert rev["checklist_total"] == 5
    assert rev["aprovados"] == 5
    assert rev["apontamentos"] == []
    assert "🛳️" in " ".join(str(m.content) for m in r["mensagens"])

def test_uat_aprova_criterios_um_a_um(tmp_path):
    """Entrega com 2 critérios → 2 perguntas de UAT (uma por execução),
    respostas registradas com evidência e selo final 🧪."""
    modelo = ModeloFake()
    modelo.configurar([
        _resposta_plano([
            {"passo": "criar tool a", "objetivo": "entrega", "status": "pendente"},
            {"passo": "criar tool b", "objetivo": "entrega", "status": "pendente"},
        ]),
        chamada_tool("calculadora", {"expressao": "1 + 1"}, id_chamada="call_ua"),
        AIMessage(content="Entregue."),
        _resposta_verify_entrega([
            {"indice": 0, "verificado": True, "evidencia": "tool a roda"},
            {"indice": 1, "verificado": True, "evidencia": "tool b roda"},
        ]),
        _resposta_licoes([]),
    ])
    app, cfg = _app(tmp_path, modelo)
    r = _executar_entrega_com_uat(
        app, cfg,
        "adicione o pacote de ferramentas a e b com teste e push",
        ["aprovado", "aprovado"],
    )
    assert "🧪" in str(r["mensagens"][-1].content)
    uat = r["uat"]
    assert len(uat) == 2
    assert all(u["resultado"] == "aprovado" for u in uat)
    assert uat[0]["evidencia"] == "aprovado"
    assert r["gaps"] == []


def test_uat_reprovado_vira_gap_e_proximo_turno_retoma(tmp_path):
    """Critério reprovado → gap no estado; o próximo turno de entrega (OUTRA
    thread) carrega o gap como contexto do plano (persistência na Store)."""
    modelo = ModeloFake()
    modelo.configurar([
        _resposta_plano([{"passo": "criar tool de logs", "objetivo": "entrega",
                          "status": "pendente"}]),
        chamada_tool("calculadora", {"expressao": "2 + 2"}, id_chamada="call_g2"),
        AIMessage(content="Entregue."),
        _resposta_verify_entrega([{"indice": 0, "verificado": True,
                                   "evidencia": "roda"}]),
        _resposta_licoes([]),
    ])
    app, cfg = _app(tmp_path, modelo)
    r1 = _executar_entrega_com_uat(
        app, cfg,
        "adicione a rotina de logs com teste e push",
        ["reprovado: falta validar retorno"],
    )
    assert r1["gaps"], "critério reprovado deveria virar gap"
    # próximo turno em OUTRA thread: o plano recebe o gap como contexto
    espiao = ModeloEspiao(saida="plano minimo")
    app2, cfg2 = _app(tmp_path, espiao)
    config2 = {"configurable": {"thread_id": "outra-thread"}}
    app2.invoke(
        {"mensagens": [HumanMessage("adicione a correcao com teste e push")],
         "metadados_sessao": {"thread_id": "outra-thread"}},
        config=config2,
    )
    chamadas = " ".join(str(c) for c in espiao.chamadas)
    assert "Gaps pendentes" in chamadas or "criar tool de logs" in chamadas


def test_uat_persistido_entre_threads_sem_rede(tmp_path):
    """UAT gravado na Store sobrevive a thread nova (novo app, mesmo banco):
    o segundo UAT mescla o histórico, cada resposta via interrupt (zero LLM)."""
    modelo = ModeloFake()
    modelo.configurar([
        _resposta_plano([
            {"passo": "criar tool a", "objetivo": "entrega", "status": "pendente"},
            {"passo": "criar tool b", "objetivo": "entrega", "status": "pendente"},
        ]),
        chamada_tool("calculadora", {"expressao": "3 + 3"}, id_chamada="call_px"),
        AIMessage(content="Entregue."),
        _resposta_verify_entrega([
            {"indice": 0, "verificado": True, "evidencia": "a roda"},
            {"indice": 1, "verificado": True, "evidencia": "b roda"},
        ]),
        _resposta_licoes([]),
    ])
    app, cfg = _app(tmp_path, modelo)
    r1 = _executar_entrega_com_uat(
        app, cfg,
        "adicione o pacote de ferramentas a e b com teste e push",
        ["aprovado", "aprovado"],
    )
    assert len(r1["uat"]) == 2
    # novo app com o MESMO banco → UAT anterior recuperado da Store
    modelo2 = ModeloFake()
    modelo2.configurar([
        _resposta_plano([{"passo": "criar rotina de limpeza", "objetivo": "entrega",
                          "status": "pendente"}]),
        chamada_tool("calculadora", {"expressao": "4 + 4"}, id_chamada="call_py"),
        AIMessage(content="Entregue."),
        _resposta_verify_entrega([{"indice": 0, "verificado": True,
                                   "evidencia": "limpeza roda"}]),
        _resposta_licoes([]),
    ])
    app2, cfg2 = _app(tmp_path, modelo2)
    r2 = _executar_entrega_com_uat(
        app2, cfg2,
        "adicione a rotina de limpeza com teste e push",
        ["aprovado"],
        thread_id="t-outra",
    )
    assert len(r2["uat"]) == 3, f"UAT deveria mesclar 2+1: {r2['uat']}"
