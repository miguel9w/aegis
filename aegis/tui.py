"""
Camada de interface: TUI Textual em streaming, em estilo Hermes.

Layout: cabeçalho com relógio, chat central + painel lateral de contexto
(modelo, papel, prompt avançado, sessão, métricas), barra de status inferior
(tempo, taxa de tokens, contadores), modo RAW alternável, atalhos de
teclado e notificações transitórias. A arquitetura continua desacoplada: a
TUI só conhece o grafo via `astream_events` (ou um produtor injetável de
frames tipados, usado nos testes headless). Sem lógica de negócio aqui.

Contrato público: `TuiAegis(app, ferramentas, cfg, trajetoria=None)` +
`await tui.iniciar()` — o `main.py` não muda.
"""

from __future__ import annotations

import json
import time
from typing import Any, AsyncIterator

from langchain_core.messages import HumanMessage

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, Input, Markdown, Static

from .config import Config
from .config_json import carregar_config_json as _cfg_json

# Truncamento de saídas na TUI — configurável via config/dados/limites.json
_LIMITE_TRUNC = int(
    _cfg_json("limites.json", {"limite_truncamento_tui": 2000})["limite_truncamento_tui"])

_CSS = """
#chat { border: round $accent; padding: 0 1; }
#painel { width: 34; border: round $primary; padding: 0 1; }
#statusbar { height: 1; color: $text; background: $panel; }
#status { height: 1; color: $text-muted; text-align: right; }
#entrada { margin-top: 1; margin-bottom: 1; }
.meta { color: $text-muted; text-style: italic; margin-bottom: 1; }
.erro { color: $error; text-style: bold; }
"""


class TuiAegis(App[None]):
    """Interface terminal interativa (Textual) em estilo Hermes."""

    TITLE = "Project Aegis"
    CSS = _CSS

    BINDINGS = [
        Binding("ctrl+n", "novo_chat", "Novo"),
        Binding("ctrl+l", "limpar_chat", "Limpar"),
        Binding("ctrl+o", "alternar_modo", "RAW"),
        Binding("ctrl+p", "alternar_painel", "Painel"),
        Binding("ctrl+d", "sair", "Sair"),
        Binding("f1", "ajuda", "Ajuda"),
    ]

    def __init__(self, app, ferramentas: list, cfg: Config, trajetoria=None,
                 produtor_eventos: Any = None) -> None:
        super().__init__()
        self.grafo = app               # grafo LangGraph compilado
        self.ferramentas = list(ferramentas)
        self.cfg = cfg
        self.trajetoria = trajetoria
        self._produtor = produtor_eventos  # injetável p/ teste (sem rede)

        # estado exposto p/ asserts de teste e auditabilidade
        self.ultima_resposta = ""
        self.ultima_saida = ""
        self.ultima_rodape = ""
        # modo de exibição: False = markdown, True = raw (texto puro)
        self.modo_raw = False
        # acumuladores da sessão
        self.tok_total = 0
        self.turnos = 0
        self.chamadas_ferramenta = 0
        # métricas do último turno (expostas p/ testes e painel)
        self.ultima_duracao = 0.0
        self.ultimo_tps = 0.0
        self.ultimos_tokens = 0
        # orquestração multiagente do último turno (exposta p/ testes/painel)
        self.dominio_turno = ""
        self.vereditos_turno: list[dict] = []
        self._ultimo_output = {}
        self.ultimas_ferramentas: list[dict] = []
        # último erro do turno (loop de recursão, falha de rede etc.)
        self.ultimo_erro: str | None = None

    # ------------------------------------------------------------------ widgets
    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            yield VerticalScroll(id="chat")
            yield VerticalScroll(id="painel")
        yield Static("", id="statusbar")
        yield Static("", id="status")
        yield Input(
            placeholder="Você — Enter envia · /ajuda · Ctrl+D sai",
            id="entrada",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.entrada.focus()
        self._atualizar_painel()
        self._atualizar_statusbar()

    # ------------------------------------------------------------------ helpers
    @property
    def chat(self) -> VerticalScroll:
        return self.query_one("#chat", VerticalScroll)

    @property
    def painel(self) -> VerticalScroll:
        return self.query_one("#painel", VerticalScroll)

    @property
    def status(self) -> Static:
        return self.query_one("#status", Static)

    @property
    def statusbar(self) -> Static:
        return self.query_one("#statusbar", Static)

    @property
    def entrada(self) -> Input:
        return self.query_one("#entrada", Input)

    # ------------------------------------------------------------------ painel
    def _nome_estado(self, caminho) -> str | None:
        """Lê o nome (ou id) de um arquivo de estado simples {"nome": ...}."""
        from pathlib import Path
        try:
            dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
            nome = dados.get("nome") or dados.get("id")
            return str(nome) if nome else None
        except (OSError, ValueError):
            return None

    def _texto_painel(self) -> str:
        modelo = getattr(self.cfg, "modelo", "—")
        sessao = getattr(self.cfg, "thread_id", "—")
        try:
            from .prompts_avancados import prompt_ativo_id
            prompt = prompt_ativo_id() or "nenhum"
        except Exception:  # noqa: BLE001 — painel é otimização
            prompt = "—"
        papel = self._nome_estado(getattr(self.cfg, "papel_ativo_path", "")) or "padrão"
        linhas = [
            "[b]🧠 Modelo[/b]",
            f"  {modelo}",
            "[b]🎯 Papel[/b]",
            f"  {papel}",
            "[b]📌 Prompt avançado[/b]",
            f"  {prompt}",
            "[b]🧾 Sessão[/b]",
            f"  {sessao}",
            "[b]🧰 Ferramentas[/b]",
            f"  {len(self.ferramentas)} carregadas",
            "",
            "[b]━ Turnos ━[/b]",
            f"  {self.turnos} turnos · {self.tok_total} tokens",
            "[b]━ Último turno ━[/b]",
            f"  ⏱ {self.ultima_duracao:.1f}s · ⚡ {self.ultimo_tps:.1f} tok/s",
            f"  ≈ {self.ultimos_tokens} tokens · {len(self.ultimas_ferramentas)} chamadas",
        ]
        if self.ultimas_ferramentas:
            linhas.append("")
            linhas.append("[b]━ Ferramentas ━[/b]")
            for ferr in self.ultimas_ferramentas[-6:]:
                status = ferr.get("status", "?")
                icone = "✔" if status == "ok" else ("✖" if status == "erro" else "…")
                sigma = ferr.get("duracao", 0.0)
                dur = f"{sigma:.1f}s" if sigma >= 0.01 else "—"
                linhas.append(f"  {icone} {ferr.get('nome', '?')} ({dur})")
        return "\n".join(linhas)

    def _atualizar_painel(self) -> None:
        try:
            self.painel.query_one(Static, expect_type=False).update(
                self._texto_painel(), markup=True)
        except Exception:  # noqa: BLE001 — painel é otimização
            try:
                self.painel.remove_children()
                self.painel.mount(Static(self._texto_painel(), markup=True))
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------ métricas
    def _atualizar_statusbar(self) -> None:
        txt = (
            f"⏱ {self.ultima_duracao:.1f}s  ⚡ {self.ultimo_tps:.1f} tok/s  "
            f"Σ {self.tok_total} tok  🤖 {self.chamadas_ferramenta}  "
            f"🧠 {getattr(self.cfg, 'modelo', '—')}  🧾 {getattr(self.cfg, 'thread_id', '—')}"
        )
        try:
            self.statusbar.update(txt)
        except Exception:  # noqa: BLE001
            pass

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
        """Mostra a pergunta e trata slash/streaming."""
        if pergunta.startswith("/"):
            from .slash import executar_slash, parsear_slash

            par = parsear_slash(pergunta)
            if par is not None:
                nome, arg = par
                self.chat.mount(Markdown(f"**Você:** {pergunta}"))
                self.chat.scroll_end(animate=False)
                resposta_ui = self._acoes_ui(nome, arg)
                if resposta_ui is not None:
                    self.chat.mount(Markdown(f"**Aegis:** {resposta_ui}"))
                    self.chat.scroll_end(animate=False)
                    self.status.update("")
                    self._atualizar_statusbar()
                    self.notify(resposta_ui, severity="information")
                    return
                resultado = executar_slash(nome, arg)
                if resultado.startswith("@@ACAO:"):
                    acao = resultado.split(":", 1)[1].strip()
                    if acao == "sair":
                        self.exit(0)
                    elif acao == "limpar":
                        self._limpar_chat("")
                    elif acao == "novo":
                        self._nova_sessao()
                else:
                    self.chat.mount(Markdown(f"**Aegis:** {resultado}"))
                self.chat.scroll_end(animate=False)
                self.status.update("")
                self._atualizar_statusbar()
                return

        self.ultima_resposta = ""
        self.ultima_rodape = ""
        self.ultimas_ferramentas = []
        self.ultimo_erro = None
        self.chat.mount(Markdown(f"**Você:** {pergunta}"))
        self.chat.scroll_end(animate=False)
        self.status.update("Pensando…")
        self._atualizar_statusbar()
        self.run_worker(self._turno(pergunta))

    # ------------------------------------------------------------------ ações
    def _acoes_ui(self, nome: str, arg: str) -> str | None:
        """Comandos de interface, antes do dispatcher de negócio."""
        if nome in ("modo", "display"):
            self.modo_raw = not self.modo_raw
            return (f"Modo de exibição: {'raw (texto puro)' if self.modo_raw else 'markdown'} "
                    "— vale para os próximos turnos")
        if nome == "modelo":
            if arg:
                self.cfg.modelo = arg
                self._atualizar_painel()
                self._atualizar_statusbar()
                return f"Modelo alterado em runtime → '{arg}'"
            return f"Modelo atual: {self.cfg.modelo}"
        if nome == "painel":
            self._alternar_painel()
            return "Painel lateral alternado (Ctrl+P)"
        return None

    def action_novo_chat(self) -> None:
        self._nova_sessao()

    def action_limpar_chat(self) -> None:
        self._limpar_chat("")

    def action_alternar_modo(self) -> None:
        self.modo_raw = not self.modo_raw
        self.notify(
            f"Modo de exibição: {'raw' if self.modo_raw else 'markdown'} "
            "(vale para os próximos turnos)",
            severity="information",
        )

    def action_alternar_painel(self) -> None:
        self._alternar_painel()

    def action_sair(self) -> None:
        self.exit(0)

    def action_ajuda(self) -> None:
        self.notify(
            "Ctrl+N novo · Ctrl+L limpar · Ctrl+O alterna RAW · Ctrl+P painel · "
            "Ctrl+D sai · /modo · /modelo · /ajuda lista comandos de barra",
            title="Atalhos de teclado",
            severity="information",
            timeout=8,
        )

    def _limpar_chat(self, mensagem: str) -> None:
        self.chat.remove_children()
        self.status.update(mensagem)
        self.ultima_resposta = ""
        self.ultima_rodape = ""
        self.ultimas_ferramentas = []
        self.ultimo_erro = None
        self._atualizar_statusbar()

    def _nova_sessao(self) -> None:
        """Nova sessão: limpa o chat E troca o thread_id (novo histórico).

        Sem isso, o checkpointer do LangGraph reutiliza o thread antigo e
        cada reinício carrega todo o histórico acumulado — foi o que causou
        o contexto gigante (input_tokens na casa dos milhões) no pico.
        """
        self._limpar_chat("")
        import uuid
        self.cfg.thread_id = f"tui-{uuid.uuid4().hex[:8]}"
        self.status.update(f"(nova sessão · thread {self.cfg.thread_id})")
        self._atualizar_painel()
        self._atualizar_statusbar()

    def _alternar_painel(self) -> None:
        painel = self.painel
        painel.display = not painel.display
        self.notify("Painel lateral " + ("visível" if painel.display else "oculto"),
                    severity="information")

    # ------------------------------------------------------------------ turno
    def _novo_bloco(self):
        if self.modo_raw:
            return Static("", markup=False)
        return Markdown("")

    async def _turno(self, pergunta: str) -> None:
        if self.trajetoria:
            self.trajetoria.registrar_mensagem_usuario(self.cfg.thread_id, pergunta)

        bloco = self._novo_bloco()
        bloco.update("*(aguardando resposta…)*")
        await self.chat.mount(bloco)

        buffer: list[str] = []
        tokens = 0
        inicio = time.monotonic()
        ferramentas_abertas: dict[Any, Markdown] = {}
        tempo_inicio_ferramenta: dict[Any, float] = {}

        try:
            async for quadro in self._iterar_frames(pergunta):
                kind = quadro.get("tipo")
                if kind == "token":
                    buffer.append(quadro.get("texto", ""))
                    tokens += 1
                    bloco.update("".join(buffer))
                elif kind == "resposta_multi":
                    # resposta entregue pelo multiagente via estado final
                    # (no_entrega não emite tokens LLM)
                    buffer.append(quadro.get("texto", ""))
                    tokens += max(1, len(str(quadro.get("texto", ""))) // 4)
                    bloco.update("".join(buffer))
                elif kind == "tool_inicio":
                    idf = quadro.get("id") or f"f{self.chamadas_ferramenta}"
                    nome = quadro.get("nome", "?")
                    args = quadro.get("args", {})
                    tempo_inicio_ferramenta[idf] = time.monotonic()
                    painel = Markdown(
                        f"🔧 **{nome}** (executando…)"
                        f"\n\n*args:* `{json.dumps(args, ensure_ascii=False)[:800]}`")
                    await self.chat.mount(painel)
                    ferramentas_abertas[idf] = painel
                elif kind == "tool_fim":
                    idf = quadro.get("id") or f"f{self.chamadas_ferramenta}"
                    painel = ferramentas_abertas.pop(idf, None)
                    nome = quadro.get("nome", "?")
                    saida = quadro.get("saida", "")[:_LIMITE_TRUNC]
                    duracao = time.monotonic() - tempo_inicio_ferramenta.pop(idf, inicio)
                    self.ultima_saida = saida
                    self.chamadas_ferramenta += 1
                    self.ultimas_ferramentas.append(
                        {"nome": nome, "status": "ok", "duracao": duracao})
                    if painel is None:
                        painel = Markdown("")
                        await self.chat.mount(painel)
                    texto_final = f"✔ **{nome}**"
                    if duracao >= 0.01:
                        texto_final += f" · {duracao:.2f}s"
                    texto_final += f"\n\n{saida or '(sem saída)'}"
                    painel.update(texto_final)
                elif kind == "erro":
                    self.ultimo_erro = quadro.get("texto", "erro desconhecido")
                    self.notify("Erro no turno — veja o chat", severity="error")
                self.ultimos_tokens = tokens
                self.ultima_duracao = time.monotonic() - inicio
                self.ultimo_tps = (
                    tokens / self.ultima_duracao if self.ultima_duracao > 0 else 0.0)
                self._atualizar_statusbar()
                self.chat.scroll_end(animate=False)
        except Exception as exc:  # noqa: BLE001 — erro de turno não derruba a TUI
            self.ultimo_erro = str(exc)
            self.notify("Turno interrompido — veja o chat", severity="error")
        finally:
            resposta = "".join(buffer)
            if self.ultimo_erro:
                bloco.update(
                    (resposta or "")
                    + "\n\n"
                    + f"⚠️ **turno interrompido:** {self.ultimo_erro}"
                    + "\n\n_Dica: `/novo` ou Ctrl+N inicia uma sessão limpa "
                    "(novo thread); `/ajuda` lista comandos._")
            else:
                bloco.update(resposta or "*(sem resposta)*")
            self.ultima_resposta = resposta
            duracao = time.monotonic() - inicio
            contagem = tokens or (len(resposta) // 4 if resposta else 0)
            tps = contagem / duracao if duracao > 0 else 0.0
            self.ultima_duracao = duracao
            self.ultimo_tps = tps
            self.ultimos_tokens = contagem
            self.turnos += 1
            self.tok_total += contagem
            self.ultima_rodape = (
                f"⏱ {duracao:.1f}s · ~{contagem} tokens · ⚡ {tps:.0f} tok/s · "
                f"thread {self.cfg.thread_id}")
            self.status.update(self.ultima_rodape)
            self._mount_meta(duracao, contagem, tps)
            self._atualizar_painel()
            self._atualizar_statusbar()
            self.chat.scroll_end(animate=False)

    def _mount_meta(self, duracao: float, tokens: int, tps: float) -> None:
        meta = Static(f"⏱ {duracao:.1f}s · ≈ {tokens} tokens · {tps:.0f} tok/s")
        meta.add_class("meta")
        self.chat.mount(meta)
        self.chat.scroll_end(animate=False)

    async def _iterar_frames(self, pergunta: str) -> AsyncIterator[dict]:
        """Produz frames tipados (token/tool_inicio/tool_fim/erro).

        Usa `produtor_eventos` quando injetado (teste headless); senão
        consome `grafo.astream_events(...)` da mesma forma que a Rich antiga.
        """
        if self._produtor is not None:
            async for frame in self._produtor():
                yield frame
            return

        configurar = {
            "configurable": {"thread_id": self.cfg.thread_id},
            # limite de recursão (loop agente↔ferramentas); default 50 via limites.json
            "recursion_limit": getattr(self.cfg, "recursion_limit", 50),
        }
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
            elif kind == "on_chain_end":
                # Retém o OUTPUT do grafo (o mais externo vence): o multiagente
                # entrega a resposta via estado final (orquestracao_final) e os
                # vereditos do avaliador — sem tokens LLM para o streaming.
                saida = (evento.get("data") or {}).get("output")
                if isinstance(saida, dict) and saida:
                    self._ultimo_output = saida
            elif kind == "on_tool_start":
                dado = (evento.get("data") or {}).get("input") or {}
                yield {
                    "tipo": "tool_inicio",
                    "id": evento.get("run_id") or f"f{self.chamadas_ferramenta}",
                    "nome": evento.get("name") or "?",
                    "args": dado if isinstance(dado, dict) else {},
                }
            elif kind == "on_tool_end":
                saida = evento.get("data") or {}
                obj = saida.get("output")
                texto = str(getattr(obj, "content", obj))[:_LIMITE_TRUNC]
                yield {
                    "tipo": "tool_fim",
                    "id": evento.get("run_id") or f"f{self.chamadas_ferramenta}",
                    "nome": getattr(obj, "name", "") or evento.get("name") or "?",
                    "saida": texto,
                }

        # Multiagente: a resposta sai do estado final (no_entrega não emite
        # tokens LLM); entrega como frame único e expõe a orquestração.
        saida = self._ultimo_output
        final = saida.get("orquestracao_final")
        if final:
            self.dominio_turno = saida.get("dominio") or ""
            self.vereditos_turno = list(saida.get("vereditos") or [])
            yield {"tipo": "resposta_multi", "texto": final}

    # ------------------------------------------------------------------ ciclo
    async def iniciar(self) -> None:
        """Início do app (chamado pelo main.py). Roda até o usuário sair."""
        await self.run_async()