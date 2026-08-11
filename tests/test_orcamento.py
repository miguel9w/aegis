"""C6 — Orçamento e controle de custo (billing guard).

Medição por passo (uso_tokens com reducer de soma), corte de execução quando
o orçamento estoura (turno ou sessão), evento `orcamento` na ponte web e a
tool `estatisticas` (paridade caveman-stats, sem rede).
"""

from langchain_core.messages import AIMessage, HumanMessage

from aegis.config import Config
from aegis.ferramentas.basicas import estatisticas
from aegis.grafo import montar_grafo
from aegis.memoria import criar_checkpointer_sync, criar_store_sync
from aegis.uso import custo_estimado, extrair_uso, somar_uso, total_tokens, verificar_orcamento
from aegis.webui_bridge import _estado_job, _processar_evento
from conftest import ModeloFake, basico_tools


# ---------------------------------------------------------------------
# Unidades (uso.py)
# ---------------------------------------------------------------------

class _Resp:
    """Resposta OpenAI-compat mínima para extrair_uso."""

    def __init__(self, token_usage=None):
        self.response_metadata = {"token_usage": token_usage} if token_usage else {}


def test_extrair_uso_completo():
    uso = extrair_uso(_Resp({
        "prompt_tokens": 120, "completion_tokens": 80,
        "completion_tokens_details": {"reasoning_tokens": 30},
    }))
    assert uso == {"entrada": 120, "saida": 80, "reasoning": 30}


def test_extrair_uso_sem_metadata():
    assert extrair_uso(_Resp(None)) == {"entrada": 0, "saida": 0, "reasoning": 0}


def test_somar_uso_acumula_por_chave():
    total = somar_uso({"entrada": 10, "saida": 5}, {"entrada": 4, "reasoning": 2})
    assert total == {"entrada": 14, "saida": 5, "reasoning": 2}


def test_custo_estimado_por_tabela():
    # 1M tokens de entrada a R$ 0,55/M → R$ 0,55
    assert custo_estimado({"entrada": 1_000_000}, {"entrada": 0.55, "saida": 2.2, "reasoning": 3.0}) == 0.55
    assert custo_estimado({}) == 0.0


def test_verificar_orcamento_turno_e_sessao():
    uso = {"entrada": 900, "saida": 100}  # 1000 tokens no turno
    sessao = {"entrada": 900, "saida": 100}
    assert verificar_orcamento(uso, sessao, {"tokens": 500}, {"tokens": 5000})["escopo"] == "turno"
    assert verificar_orcamento(uso, sessao, {"tokens": 5000}, {"tokens": 500})["escopo"] == "sessao"
    assert verificar_orcamento(uso, sessao, {"tokens": 5000}, {"tokens": 5000}) is None
    assert verificar_orcamento(uso, sessao, None, None) is None


# ---------------------------------------------------------------------
# Corte no fluxo + contabilidade incremental
# ---------------------------------------------------------------------

def _app(tmp_path, modelo, **kwargs):
    cfg = Config()
    cfg.banco = tmp_path / "c6.db"
    cfg.thread_id = "t-c6"
    cfg.limiar_compressao = 100
    cfg.memoria_ativa = False
    for chave, valor in kwargs.items():
        setattr(cfg, chave, valor)
    checkpointer = criar_checkpointer_sync(cfg.banco)
    store = criar_store_sync(cfg.banco)
    app = montar_grafo(modelo, basico_tools(), checkpointer=checkpointer, store=store, cfg=cfg)
    return app, cfg


def _invocar(app, cfg, texto="quanto é 2+2"):
    return app.invoke(
        {"mensagens": [HumanMessage(texto)],
         "metadados_sessao": {"thread_id": cfg.thread_id}},
        config={"configurable": {"thread_id": cfg.thread_id}},
    )


def test_corte_por_orcamento_impede_tools(tmp_path):
    """Resposta com tool_calls E usage alto → corte imediato: NENHUMA
    ferramenta executa (resumo parcial) e o estado registra o estouro."""
    ai = AIMessage(
        content="",
        tool_calls=[{"name": "calculadora", "args": {"expressao": "2+2"},
                     "id": "call_c6", "type": "tool_call"}],
        response_metadata={"token_usage": {"prompt_tokens": 2_000_000, "completion_tokens": 1}},
    )
    modelo = ModeloFake()
    modelo.configurar([ai])
    app, cfg = _app(tmp_path, modelo,
                    orcamento_por_turno={"tokens": 1000, "reais": 0.0001},
                    orcamento_por_sessao={"tokens": 100_000_000, "reais": 999})
    res = _invocar(app, cfg)

    assert not res.get("registros_ferramentas")  # calculadora NÃO rodou
    assert res["uso_tokens"]["entrada"] == 2_000_000
    corte = res["orcamento_estourado"]
    assert corte["escopo"] == "turno" and corte["metrica"] == "tokens"
    assert corte["teto"] == 1000
    assert isinstance(res["mensagens"][-1], AIMessage)  # resumo parcial


def test_corte_por_orcamento_da_sessao(tmp_path):
    """Primeiro turno ok; o segundo acumula e estoura a sessão → corte."""
    ai_ok = AIMessage(content="ok", response_metadata={
        "token_usage": {"prompt_tokens": 4000, "completion_tokens": 1000}})
    ai_alto = AIMessage(content="", tool_calls=[
        {"name": "calculadora", "args": {"expressao": "1+1"}, "id": "call_c6b", "type": "tool_call"}],
        response_metadata={"token_usage": {"prompt_tokens": 1000, "completion_tokens": 100}})
    modelo = ModeloFake()
    modelo.configurar([ai_ok, ai_alto])
    app, cfg = _app(tmp_path, modelo,
                    orcamento_por_turno={"tokens": 1_000_000, "reais": 1},
                    orcamento_por_sessao={"tokens": 6000, "reais": 1})
    res1 = _invocar(app, cfg, "primeiro")
    assert not res1.get("orcamento_estourado")
    assert res1["uso_tokens"]["entrada"] == 4000

    res2 = _invocar(app, cfg, "segundo")  # 4000 + 1100 = 5100 > 6000? não... 5000 < 6000
    # ajuste: entrada 4000+1000=5000, saída 1000+100=1100 → total 6100 > 6000 ✓
    assert res2["orcamento_estourado"]["escopo"] == "sessao"
    assert res2["uso_tokens"]["entrada"] == 5000  # contabilidade incremental


def test_contabilidade_soma_entre_turnos(tmp_path):
    """Reducer de soma: uso de turnos consecutivos na MESMA thread acumula."""
    modelo = ModeloFake()
    modelo.configurar([
        AIMessage(content="um", response_metadata={
            "token_usage": {"prompt_tokens": 100, "completion_tokens": 50}}),
        AIMessage(content="dois", response_metadata={
            "token_usage": {"prompt_tokens": 40, "completion_tokens": 20}}),
    ])
    app, cfg = _app(tmp_path, modelo)
    _invocar(app, cfg, "primeiro")
    res = _invocar(app, cfg, "segundo")

    assert res["uso_tokens"]["entrada"] == 140
    assert res["uso_tokens"]["saida"] == 70
    assert total_tokens(res["uso_tokens"]) == 210


# ---------------------------------------------------------------------
# Tool estatisticas (sem rede) + evento na ponte
# ---------------------------------------------------------------------

def test_estatisticas_devolve_metricas_sem_rede(tmp_path, monkeypatch):
    from aegis.config import config as cfg_global

    modelo = ModeloFake()
    modelo.configurar([AIMessage(content="ok", response_metadata={
        "token_usage": {"prompt_tokens": 10, "completion_tokens": 5,
                        "completion_tokens_details": {"reasoning_tokens": 3}}})])
    app, cfg = _app(tmp_path, modelo)
    app.invoke(
        {"mensagens": [HumanMessage("oi")], "metadados_sessao": {"thread_id": cfg.thread_id}},
        config={"configurable": {"thread_id": cfg.thread_id}},
    )

    # a tool lê o SINGLETON — aponta para o mesmo banco/thread do fluxo
    monkeypatch.setattr(cfg_global, "banco", tmp_path / "c6.db")
    monkeypatch.setattr(cfg_global, "thread_id", "t-c6")
    saida = estatisticas.invoke({"escopo": "sessao"})
    assert "📊" in saida and "Custo estimado" in saida
    assert "10" in saida  # tokens de entrada da sessão
    assert "R$" in saida

    # export JSON (paridade com o estado do checkpointer)
    import json
    dados = json.loads(estatisticas.invoke({"escopo": "sessao", "formato": "json"}))
    assert dados["tokens"]["entrada"] == 10
    assert dados["total_tokens"] == 18


def test_ponte_emite_frame_orcamento():
    est = _estado_job()
    corte = {"escopo": "turno", "metrica": "tokens", "teto": 1000,
             "usado": 2_000_001, "tokens_turno": 2_000_001}
    frames = _processar_evento(
        {"event": "on_chain_end", "name": "no_agente",
         "data": {"output": {"orcamento_estourado": corte}}}, est)
    assert frames and frames[0]["kind"] == "orcamento"
    assert frames[0]["escopo"] == "turno" and frames[0]["teto"] == 1000
    # sem estouro → nenhum frame
    est2 = _estado_job()
    frames2 = _processar_evento(
        {"event": "on_chain_end", "name": "no_agente",
         "data": {"output": {"mensagens": []}}}, est2)
    assert all(f["kind"] != "orcamento" for f in frames2)