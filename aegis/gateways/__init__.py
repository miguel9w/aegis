"""Pacote de gateways — canais desacoplados que consomem o mesmo grafo da TUI.

A camada de interface (TUI, CLI, Webhook HTTP, futuros bots Telegram/Discord)
apenas chama `processar_mensagem(app, thread_id, texto)` — o backend LangGraph
não conhece o canal.
"""

from __future__ import annotations

from .webhook import HandlerWebhook, processar_mensagem, iniciar_servidor

__all__ = ["HandlerWebhook", "processar_mensagem", "iniciar_servidor"]
