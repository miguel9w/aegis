"""
Camada de interface: TUI Rich baseada em Event Streaming.

Escuta `app.astream_events()` e renderiza em tempo real:
  - resposta do assistente em Markdown (streaming) dentro de um Panel,
  - Spinner de status ("Pensando..." / "Executando ferramenta X"),
  - painéis de parâmetros e retornos das ferramentas invocadas.

Arquitetura desacoplada: a TUI só conhece o app (grafo) — nenhuma lógica
de negócio vive aqui — facilitando a troca por outros canais (gateway).
"""

from __future__ import annotations

import json
import time

from langchain_core.messages import HumanMessage
from rich.console import Console, Group
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.spinner import Spinner
from rich.text import Text

from .config import Config

_LEGENDA = "[dim]Ctrl+C para sair[/]"


def _meta(evento: dict) -> dict:
    """Extrai metadados do evento (compatível com stream v1 e v2)."""
    return evento.get("metadata") or (evento.get("data") or {}).get("metadata") or {}


class TuiAegis:
    """Interface terminal interativa baseada em streaming de eventos."""

    def __init__(self, app, ferramentas: list, cfg: Config, trajetoria=None) -> None:
        self.app = app
        self.ferramentas = ferramentas
        self.cfg = cfg
        self.trajetoria = trajetoria
        self.console = Console()

    # -----------------------------------------------------------------
    def _painel_usuario(self, pergunta: str) -> Panel:
        return Panel(Text(pergunta), title="[bold cyan]Você[/]", border_style="cyan")

    def _painel_resposta(self, buffer: list[str]) -> Panel:
        texto = "".join(buffer).strip()
        corpo: object
        if not texto:
            corpo = Text("(aguardando resposta do Aegis...)", style="dim")
        else:
            corpo = Markdown(texto)
        return Panel(corpo, title="[bold green]Aegis[/]", border_style="green")

    def _painel_ferramenta_inicio(self, nome: str, args: dict) -> Panel:
        return Panel(
            Text(f"args: {json.dumps(args, ensure_ascii=False)[:800]}", style="dim"),
            title=f"[bold yellow][em]🔧 {nome}[/em][/bold yellow]",
            border_style="yellow",
        )

    def _painel_ferramenta_fim(self, nome: str, saida: str) -> Panel:
        estilo = "red" if saida.startswith(("ERRO_FERRAMENTA:", "Error:")) else "green"
        corpo = Text(saida[:2000], style="default")
        return Panel(corpo, title=f"[/] {nome}", border_style=estilo)

    # -----------------------------------------------------------------
    async def iniciar(self) -> None:
        """Loop REPL interativo."""
        self.console.print(Panel(
            Text.assemble(
                ("Aegis", "bold green"), " — ", ("agente pessoal autônomo", "dim"), "",
                ("\nthread: ", "dim"), (self.cfg.thread_id, "cyan"),
                ("   modelo: ", "dim"), (self.cfg.modelo, "magenta"),
                ("\n", ""), (_LEGENDA, "dim"),
            ),
            title="[bold]Project Aegis[/]",
            border_style="green",
        ))
        while True:
            try:
                pergunta = Prompt.ask("Você")
            except (KeyboardInterrupt, EOFError):
                self.console.print("\n[dim]Encerrando Aegis. Até logo! 👋[/]")
                break
            if not pergunta.strip():
                continue
            if pergunta.strip().lower() in {"sair", "exit", "quit", "fim"}:
                self.console.print("[dim]Encerrando Aegis. Até logo! 👋[/]")
                break
            try:
                await self.turno(pergunta)
            except Exception as exc:  # noqa: BLE001 — erro de turno não derruba o REPL
                self.console.print(Panel(f"[red]{exc}[/]", title="Erro", border_style="red"))

    # -----------------------------------------------------------------
    async def turno(self, pergunta: str) -> dict:
        """Executa um turno com streaming e renderização ao vivo."""
        self.console.print(self._painel_usuario(pergunta))

        # Auditabilidade: registra a pergunta na trajetória (dataset ShareGPT/RL)
        if self.trajetoria:
            self.trajetoria.registrar_mensagem_usuario(self.cfg.thread_id, pergunta)

        config = {"configurable": {"thread_id": self.cfg.thread_id}}
        entrada = {
            "mensagens": [HumanMessage(pergunta)],
            "metadados_sessao": {"thread_id": self.cfg.thread_id},
        }

        buffer: list[str] = []
        paineis_ferramenta: list[Panel] = []
        idx_ferramenta_aberta: int | None = None   # índice do painel de execução em andamento
        ultima_render = 0.0
        inicio = time.monotonic()
        tokens = 0
        ultima_saida = ""

        status = Spinner("dots", text="Pensando... ", style="cyan")

        def render_atual() -> Panel:
            partes = [self._painel_usuario(pergunta), self._painel_resposta(buffer)]
            partes.extend(paineis_ferramenta)
            if not buffer and not paineis_ferramenta:
                partes.append(Panel(status, border_style="dim"))
            return Panel(Group(*partes), border_style="green")

        hook_trajetoria = self.trajetoria.hook(self.cfg.thread_id) if self.trajetoria else None

        with Live(render_atual(), console=self.console, refresh_per_second=10,
                  vertical_overflow="visible", transient=True) as live:
            async for evento in self.app.astream_events(entrada, config=config, version="v2"):
                kind = evento.get("event", "")

                # tokens do modelo principal (tags=["resposta"]); em v2 os tags
                # ficam no TOP-LEVEL do evento (não no metadata)
                if kind == "on_chat_model_stream" and "resposta" in (evento.get("tags") or []):
                    chunk = (evento.get("data") or {}).get("chunk")
                    conteudo = getattr(chunk, "content", None)
                    if isinstance(conteudo, str) and conteudo:
                        buffer.append(conteudo)
                        tokens += 1

                elif kind == "on_model_start":
                    # modelo chamado sem tag de resposta? deixa fluir
                    pass

                elif kind == "on_tool_start":
                    dado = (evento.get("data") or {}).get("input") or {}
                    nome = evento.get("name") or "?"   # v2: nome no top-level
                    args = dado if isinstance(dado, dict) else {}
                    paineis_ferramenta.append(
                        Panel(Text(f"args: {json.dumps(args, ensure_ascii=False)[:800]}", style="dim"),
                              title=f"[bold yellow]🔧 {nome}[/]", border_style="yellow")
                    )
                    idx_ferramenta_aberta = len(paineis_ferramenta) - 1

                elif kind == "on_tool_end":
                    dado = (evento.get("data") or {}).get("output")
                    saida = str(getattr(dado, "content", dado))[:2000]  # v2: ToolMessage
                    nome_tool = getattr(dado, "name", "") or evento.get("name") or "?"
                    ultima_saida = saida
                    style = "red" if saida.startswith(("ERRO_FERRAMENTA:", "Error:")) else "green"
                    corpo = Text(saida)
                    if idx_ferramenta_aberta is not None and idx_ferramenta_aberta < len(paineis_ferramenta):
                        paineis_ferramenta[idx_ferramenta_aberta] = Panel(
                            corpo, title=f"→ resultado: {nome_tool}", border_style=style)
                    else:
                        paineis_ferramenta.append(Panel(corpo, title="→ resultado", border_style=style))
                    idx_ferramenta_aberta = None

                # throttled refresh da Live
                agora = time.monotonic()
                if agora - ultima_render > 0.08:
                    live.update(render_atual())
                    ultima_render = agora

                if hook_trajetoria:
                    hook_trajetoria(evento)

            live.update(render_atual())  # render final

        # A Live com transient=True some ao fechar; reimprime os painéis de
        # ferramenta rodadas e a resposta final para que PERMANEÇAM no terminal.
        for painel in paineis_ferramenta:
            self.console.print(painel)
        self.console.print(self._painel_resposta(buffer))

        duracao = time.monotonic() - inicio
        rodape = Text(f"⏱ {duracao:.2f}s  ·  ~{tokens} tokens  ·  thread {self.cfg.thread_id}", style="dim")
        self.console.print(rodape)
        return {"duracao": duracao, "tokens": tokens, "saida": ultima_saida}