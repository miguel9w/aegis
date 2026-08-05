"""
Gateway Webhook HTTP — canal desacoplado do backend LangGraph.

Expõe o mesmo grafo que a TUI/CLI através de uma API REST mínima (stdlib):

    POST /mensagem   {"mensagem": "...", "thread_id": "opcional"}
                     → {"resposta": "...", "ferramentas": [...], "thread_id": "..."}
    GET  /healthz    → {"status": "ok", "versao": "..."}

Sem dependências novas (http.server). Threading por requisição.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from ..grafo import executar_headless


def processar_mensagem(app, thread_id: str, texto: str) -> dict[str, Any]:
    """Executa uma mensagem no grafo e devolve a resposta estruturada.

    É a ÚNICA função que o canal precisa conhecer — TUI, CLI, webhook e bots
    futuros (Telegram/Discord) usam o mesmo contrato.
    """
    resultado = executar_headless(app, texto, thread_id)

    mensagens = resultado.get("mensagens") or []
    resposta = ""
    if mensagens:
        ultima = mensagens[-1]
        resposta = str(getattr(ultima, "content", ultima))

    return {
        "thread_id": thread_id,
        "resposta": resposta,
        "ferramentas": resultado.get("registros_ferramentas") or [],
    }


class HandlerWebhook(BaseHTTPRequestHandler):
    """Handler HTTP com acesso ao grafo via atributo de classe `app`."""

    app = None  # injetado antes de iniciar o servidor

    def _responder(self, codigo: int, corpo: dict) -> None:
        payload = json.dumps(corpo, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self) -> None:  # noqa: N802 — assinatura do http.server
        try:
            tamanho = int(self.headers.get("Content-Length") or 0)
            corpo = json.loads(self.rfile.read(tamanho) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self._responder(400, {"erro": "corpo deve ser JSON válido"})
            return

        texto = str(corpo.get("mensagem", "")).strip()
        if not texto:
            self._responder(400, {"erro": "campo 'mensagem' é obrigatório"})
            return

        if self.app is None:
            self._responder(503, {"erro": "grafo não inicializado"})
            return

        try:
            thread_id = str(corpo.get("thread_id") or "default")
            resposta = processar_mensagem(self.app, thread_id, texto)
            self._responder(200, resposta)
        except Exception as exc:  # noqa: BLE001 — erro de execução vira 500
            self._responder(500, {"erro": str(exc)})

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/healthz":
            try:
                from .. import __version__
            except Exception:  # noqa: BLE001
                __version__ = "?"
            self._responder(200, {"status": "ok", "versao": __version__})
            return
        self._responder(404, {"erro": "rota não encontrada"})

    def log_message(self, format: str, *args) -> None:  # noqa: A002 — silencia acesso
        pass


def iniciar_servidor(app, host: str = "127.0.0.1", porta: int = 8787) -> ThreadingHTTPServer:
    """Inicia o servidor webhook com o grafo injetado no handler."""
    HandlerWebhook.app = app
    return ThreadingHTTPServer((host, porta), HandlerWebhook)
