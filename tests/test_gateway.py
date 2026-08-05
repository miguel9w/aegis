"""Testes do gateway Webhook HTTP (contrato desacoplado do grafo)."""

from __future__ import annotations

import http.client
import json
import threading

from langchain_core.messages import AIMessage

from aegis.gateways import HandlerWebhook, iniciar_servidor, processar_mensagem


class AplicacaoStub:
    """Substituto do grafo compilado — apenas o contrato `.invoke()`."""

    def __init__(self, resposta: str = "oi", ferramentas: list | None = None) -> None:
        self.resposta = resposta
        self.ferramentas = ferramentas or []

    def invoke(self, entrada: dict, config: dict | None = None) -> dict:
        assert entrada["mensagens"][0].content
        return {
            "mensagens": [AIMessage(content=self.resposta)],
            "registros_ferramentas": self.ferramentas,
        }


def test_processar_mensagem_contrato():
    app = AplicacaoStub("8 * 8 = 64", [{"nome": "calculadora", "erro": False}])
    saida = processar_mensagem(app, "thread-x", "calcule 8*8")

    assert saida["thread_id"] == "thread-x"
    assert saida["resposta"] == "8 * 8 = 64"
    assert saida["ferramentas"][0]["nome"] == "calculadora"


def test_http_post_mensagem():
    servidor = iniciar_servidor(AplicacaoStub("olá mundo"), porta=0)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    try:
        porta = servidor.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
        corpo = json.dumps({"mensagem": "oi", "thread_id": "t"})
        conn.request("POST", "/mensagem", body=corpo,
                     headers={"Content-Type": "application/json"})
        resposta = conn.getresponse()
        dados = json.loads(resposta.read().decode("utf-8"))
        conn.close()

        assert resposta.status == 200
        assert dados["resposta"] == "olá mundo"
        assert dados["thread_id"] == "t"
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_http_healthz():
    servidor = iniciar_servidor(AplicacaoStub(), porta=0)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    try:
        porta = servidor.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
        conn.request("GET", "/healthz")
        resposta = conn.getresponse()
        dados = json.loads(resposta.read().decode("utf-8"))
        conn.close()

        assert resposta.status == 200
        assert dados["status"] == "ok"
    finally:
        servidor.shutdown()
        servidor.server_close()


def test_http_erro_sem_mensagem():
    servidor = iniciar_servidor(AplicacaoStub(), porta=0)
    thread = threading.Thread(target=servidor.serve_forever, daemon=True)
    thread.start()
    try:
        porta = servidor.server_address[1]
        conn = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
        conn.request("POST", "/mensagem", body="{}",
                     headers={"Content-Type": "application/json"})
        resposta = conn.getresponse()
        dados = json.loads(resposta.read().decode("utf-8"))
        conn.close()

        assert resposta.status == 400
        assert "mensagem" in dados["erro"]
    finally:
        servidor.shutdown()
        servidor.server_close()