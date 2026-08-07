"""
Camada de interface: TUI Textual baseada em Event Streaming.

Substitui a antiga TUI Rich (loop `Live` + `Prompt`) por um `App` do Textual,
mantendo a MESMA arquitetura desacoplada: a TUI só conhece o app (grafo) e
escuta `app.astream_events()` em um *worker* assíncrono, renderizando em tempo
real (tokens do assistente, painéis de ferramentas, status). Sem lógica de
negócio aqui — a troca por outros canais (gateway) segue independente.

O contrato público é idêntico ao anterior (`TuiAegis(app, ferramentas, cfg,
trajetoria=None)` + `await tui.iniciar()`), então o `main.py` não muda.

Para permitir teste headless (sem rede/LLM), o fluxo de eventos é injetável:
`produtor_eventos` (async-iterável) substitui `astream_events` quando fornecido.
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

from langchain_core.messages import HumanMessage

from textual.app import App, ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Footer, Header, Input, Markdown, Static

from .config import Config
from .config_json import carregar_config_json as _cfg_json

# Truncamento de saídas na TUI — configurável via config/dados/limites.json
_LIMITE_TRUNC = int(_cfg_json("limites.json", {"limite_truncamento_tui": 2000})["limite_truncamento_tui"])

_CSS = """
#chat { height: 1fr; border: round $accent; padding: 0 1; }
#status { height: 1; color: $text-muted; text-align: right; }
#entrada { margin-top: 1; margin-bottom: 1; }
"""


class TuiAegis(App[None]):
    """Interface terminal interativa (Textual) baseada em streaming de eventos."""

    TITLE = "Project Aegis"
    CSS = _CSS

    def __init__(self, app, ferramentas: list, cfg: Config, trajetoria=None,
                 produtor_eventos: Any = None) -> None:
        super().__init__()
        self.grafo = app                  # o grafo LangGraph compilado
        self.ferramentas = list(ferramentas)
        self.cfg = cfg
        self.trajetoria = trajetoria
        self._produtor = produtor_eventos   # injetável p/ teste (sem rede)
        # estado exposto p/ asserts de teste e auditabilidade
        self.ultima_resposta = ""
        self.ultima_saida = ""
        self.ultima_rodape = ""

    # ------------------------------------------------------------------ widgets
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        yield VerticalScroll(id="chat")
        yield Static("", id="status")
        yield Input(
            placeholder="Você — Enter envia · Ctrl+C sai",
            id="entrada",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.entrada.focus()

    @property
    def chat(self) -> VerticalScroll:
        return self.query_one("#chat", VerticalScroll)

    @property
    def status(self) -> Static:
        return self.query_one("#status", Static)

    @property
    def entrada(self) -> Input:
        return self.query_one("#entrada", Input)

    # ------------------------------------------------------------------ entrada
    def on_input_submitted(self, evento: Input.Submitted) -> None:
        texto = (evento.value or "").strip()
        self.entrada.clear()
        if not texto:
            return
        if texto.lower() in {"sair", "exit", "quit", "fim"}:
            self.exit(0)
            return
        self.enviar(texto)

    def enviar(self, pergunta: str) -> None:
        """Inicia um turno: mostra a pergunta e dispara o streaming em um worker."""
        self.ultima_resposta = ""
        self.ultima_rodape = ""
        self.chat.mount(Markdown(f"**Você:** {pergunta}"))
        self.chat.scroll_end(animate=False)
        self.status.update("Pensando…")
        self.run_worker(self._turno(pergunta))

    # ------------------------------------------------------------------ turno
    async def _turno(self, pergunta: str) -> None:
        if self.trajetoria:
            self.trajetoria.registrar_mensagem_usuario(self.cfg.thread_id, pergunta)

        bloco = Markdown("*(aguardando resposta…)*")
        await self.chat.mount(bloco)

        buffer: list[str] = []
        tokens = 0
        inicio = time.monotonic()
        ferramentas_abertas: dict[Any, Markdown] = {}
        estado_ferramenta: dict[Any, str] = {}

        try:
            async for quadro in self._iterar_frames(pergunta):
                kind = quadro.get("tipo")
                if kind == "token":
                    buffer.append(quadro.get("texto", ""))
                    tokens += 1
                    bloco.update("".join(buffer))
                    self.chat.scroll_end(animate=False)
                elif kind == "tool_inicio":
                    nome = quadro.get("nome", "?")
                    args = quadro.get("args", {})
                    painel = Markdown(
                        f"🔧 **{nome}**\n\n*args:* `{json.dumps(args, ensure_ascii=False)[:800]}`"
                    )
                    await self.chat.mount(painel)
                    ferramentas_abertas[quadro.get("id")] = painel
                elif kind == "tool_fim":
                    painel = ferramentas_abertas.pop(quadro.get("id"), None)
                    nome = quadro.get("nome", "?")
                    saida = quadro.get("saida", "")[:_LIMITE_TRUNC]
                    self.ultima_saida = saida
                    if painel is None:
                        painel = Markdown("")
                        await self.chat.mount(painel)
                    painel.update(f"→ **{nome}**\n\n{saida or '(sem saída)'}")
                self.chat.scroll_end(animate=False)
        finally:
            bloco.update("".join(buffer) or "*(sem resposta)*")
            self.ultima_resposta = "".join(buffer)
            duracao = time.monotonic() - inicio
            self.ultima_rodape = f"⏱ {duracao:.1f}s · ~{tokens} tokens · thread {self.cfg.thread_id}"
            self.status.update(self.ultima_rodape)
            self.chat.scroll_end(animate=False)

    async def _iterar_frames(self, pergunta: str) -> AsyncIterator[dict]:
        """Produz quadros tipados (token/tool_inicio/tool_fim) a partir de eventos.

        Usa `produtor_eventos` quando injetado (teste headless); senão consome
        `app.astream_events(...)` da mesma forma que a TUI Rich antiga.
        """
        if self._produtor is not None:
            async for quadro in self._produtor():
                yield quadro
            return

        configurar = {"configurable": {"thread_id": self.cfg.thread_id}}
        entrada = {
            "mensagens": [HumanMessage(pergunta)],
            "metadados_sessao": {"thread_id": self.cfg.thread_id},
        }
        async for evento in self.grafo.astream_events(entrada, config=configurar, version="v2"):
            kind = evento.get("event", "")
            if kind == "on_chat_model_stream" and "resposta" in (evento.get("tags") or []):
                chunk = (evento.get("data") or {}).get("chunk")
                conteudo = getattr(chunk, "content", None)
                if isinstance(conteudo, str) and conteudo:
                    yield {"tipo": "token", "texto": conteudo}
            elif kind == "on_tool_start":
                dado = (evento.get("data") or {}).get("input") or {}
                yield {
                    "tipo": "tool_inicio",
                    "id": evento.get("run_id"),
                    "nome": evento.get("name") or "?",
                    "args": dado if isinstance(dado, dict) else {},
                }
            elif kind == "on_tool_end":
                saida = evento.get("data") or {}
                obj = saida.get("output")
                texto = str(getattr(obj, "content", obj))[:_LIMITE_TRUNC]
                yield {
                    "tipo": "tool_fim",
                    "nome": getattr(obj, "name", "") or evento.get("name") or "?",
                    "saida": texto,
                }

    # ------------------------------------------------------------------ ciclo
    async def iniciar(self) -> None:
        """Início do app (chamado pelo main.py). Roda até o usuário sair."""
        await self.run_async()