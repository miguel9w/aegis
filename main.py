"""
Project Aegis — Ponto de entrada da CLI.

Modos de execução:
    pixi run start                        # TUI interativa (streaming de eventos)
    pixi run start "sua pergunta"         # execução única (headless)
    pixi run start --headless "pergunta"  # idem, explícito (automação)
    pixi run start --thread meu_topico    # alterna tópico/conversa
    pixi run start --novo-thread          # cria novo tópico UUID
    pixi run start --listar-ferramentas   # lista ferramentas registradas
    pixi run start --listar-skills        # lista habilidades (extensions/skills)
    pixi run start --exportar-sharegpt    # trajetorias → dataset ShareGPT (config/dados/)
    pixi run start --exportar-openai      # trajetorias → dataset OpenAI/RL (config/dados/)
    pixi run start --gateway [PORTA]      # serve o grafo via Webhook HTTP
    pixi run start --dev                  # modo verboso (todos os eventos)
"""

from __future__ import annotations

import asyncio
import sys
import uuid
from argparse import ArgumentParser, Namespace, RawDescriptionHelpFormatter

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from aegis.config import ConfigError, config
from aegis.ferramentas import avisos_carregamento, carregar_ferramentas
from aegis.grafo import mk_config, montar_grafo
from aegis.llm import criar_llm
from aegis.memoria import criar_checkpointer_sync, criar_store_sync
from aegis.trajetoria import Trajetoria
from aegis.tui import TuiAegis

console = Console()


def novo_argumentos() -> ArgumentParser:
    p = ArgumentParser(
        prog="aegis",
        description="Project Aegis — Agente Pessoal Autônomo (LangGraph + Pixi + Rich).",
        formatter_class=RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("pergunta", nargs="?", default=None,
                   help="Pergunta única (modo one-shot). Sem argumentos: TUI interativa.")
    p.add_argument("--headless", action="store_true", dest="headless",
                   help="Execução headless (sem TUI) — ideal para automação.")
    p.add_argument("--thread", default=None, help="ID do tópico/conversa.")
    p.add_argument("--novo-thread", action="store_true", dest="novo_thread",
                   help="Cria um novo tópico com UUID.")
    p.add_argument("--dev", action="store_true", help="Modo verboso (eventos completos).")
    p.add_argument("--listar-ferramentas", action="store_true", dest="listar_ferramentas",
                   help="Lista todas as ferramentas registradas.")
    p.add_argument("--listar-skills", action="store_true", dest="listar_skills",
                   help="Lista as habilidades disponíveis (extensions/skills).")
    p.add_argument("--exportar-sharegpt", nargs="?", const="", dest="exportar_sharegpt",
                   metavar="DESTINO", help="Exporta trajetorias para ShareGPT (config/dados/datasets).")
    p.add_argument("--exportar-openai", nargs="?", const="", dest="exportar_openai",
                   metavar="DESTINO", help="Exporta trajetorias para OpenAI/RL (config/dados/datasets).")
    p.add_argument("--gateway", nargs="?", const="", dest="gateway",
                   metavar="PORTA", help="Serve o grafo via Webhook HTTP (std: 8787).")
    p.add_argument("--agendador", nargs="?", const="", dest="agendador",
                   metavar="SEGUNDOS", help="Loop do cron interno (executa vencidos a cada N s).")
    p.add_argument("--agendador-uma-vez", action="store_true", dest="agenda_uma_vez",
                   help="Executa os agendamentos vencidos uma única vez e sai.")
    p.add_argument("--versao", action="store_true", help="Mostra a versão do Aegis.")
    p.add_argument("--comando", default=None, metavar="SLASH",
                   help="Executa um comando de barra (ex.: \"/notas\") e sai.")
    p.add_argument("--papeis", nargs="?", const="", default=None, metavar="[NOME]",
                   help="Lista os papéis; com NOME, define o papel ativo.")
    p.add_argument("--memoria", nargs="?", const="", default=None, metavar="[CONSULTA]",
                   help="Consulta a memória pontuada (sem argumento: últimos registros).")
    p.add_argument("--plano", action="store_true", help="Mostra o plano de tarefas atual.")
    p.add_argument("--notas", nargs="?", const="", default=None, metavar="[N]",
                   help="Lista as notas recentes (N opcional).")
    p.add_argument("--papers", default=None, metavar="CONSULTA",
                   help="Busca papers no arXiv.")
    p.add_argument("--obsidian", action="store_true", help="Lista as notas do vault Obsidian.")
    return p


def _aplicar_flags(args: Namespace) -> None:
    """Ajusta a configuração conforme as flags de linha de comando."""
    if args.dev:
        config.dev = True
    if args.novo_thread:
        config.thread_id = uuid.uuid4().hex[:12]
        console.print(f"[bold cyan]→ Novo thread criado: [green]{config.thread_id}[/][/]")
    elif args.thread:
        config.thread_id = args.thread


def listar_ferramentas(ferramentas: list) -> None:
    tabela = Table(title="Ferramentas registradas", border_style="green")
    tabela.add_column("Nome", style="bold cyan")
    tabela.add_column("Descrição", style="default", overflow="fold")
    for f in ferramentas:
        tabela.add_row(f.name, (f.description or "").replace("\n", " ")[:150])
    console.print(tabela)
    for aviso in avisos_carregamento():
        console.print(f"[yellow]⚠ {aviso}[/]")


def listar_skills() -> None:
    from aegis.skills import carregar_skills
    habilidades = carregar_skills(config.skills_dir)
    if not habilidades:
        console.print("[yellow]Nenhuma habilidade encontrada em extensions/skills[/]")
        return
    tabela = Table(title=f"Habilidades ({config.skills_dir})", border_style="magenta")
    tabela.add_column("Nome", style="bold")
    tabela.add_column("Descrição", style="default")
    for nome, info in sorted(habilidades.items()):
        tabela.add_row(nome, info["descricao"][:150])
    console.print(tabela)


def _exportar(formato: str, destino: str | None) -> int:
    """Exporta trajetórias para dataset ShareGPT ou OpenAI (fine-tuning/RL)."""
    from aegis.exportador import exportar_openai, exportar_sharegpt

    diretorio = config.trajetorias_dir
    if not diretorio.is_dir():
        console.print("[yellow]Nenhuma trajetória encontrada — habilite AEGIS_TRAJETORIA=true.[/]")
        return 1
    fn = exportar_sharegpt if formato == "sharegpt" else exportar_openai
    resumo = fn(diretorio, destino or None)
    console.print(Panel(
        f"[green]✔ {resumo['conversas']} conversa(s) de {resumo['threads']} thread(s)\n"
        f"[cyan]{resumo['arquivo']}[/]",
        title=f"Exportação {formato}", border_style="green"))
    return 0


def _rodar_gateway(porta: str) -> int:
    """Serve o grafo via Webhook HTTP (mesma lógica da TUI, sem terminal)."""
    import os

    from aegis.gateways import iniciar_servidor

    try:
        app, _ferramentas = _montar_app_sync()
    except ConfigError as exc:
        console.print(Panel(f"[red]{exc}[/]", title="Configuração", border_style="red"))
        return 1
    porta_int = int(porta or os.getenv("AEGIS_GATEWAY_PORT", "8787"))
    servidor = iniciar_servidor(app, porta=porta_int)
    console.print(Panel(
        f"Webhook ativo em [bold cyan]http://127.0.0.1:{porta_int}[/]\n"
        "POST /mensagem  {'mensagem': '...', 'thread_id': 'opcional'}\n"
        "GET  /healthz\n[dim]Ctrl+C para encerrar[/]",
        title="Aegis Gateway", border_style="green"))
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        console.print("\n[dim]Gateway encerrado. Até logo! 👋[/]")
    finally:
        servidor.server_close()
    return 0


def _rodar_agendador(intervalo: int, uma_vez: bool = False) -> int:
    """Loop do cron: executa agendamentos vencidos no grafo a cada intervalo."""
    import time

    import aegis.agendador as ag

    try:
        app, _ferramentas = _montar_app_sync()
    except ConfigError as exc:
        console.print(Panel(f"[red]{exc}[/]", title="Configuração", border_style="red"))
        return 1

    local = config.agendamentos_path
    callback = config.agendador_callback or None
    console.print(Panel(
        f"[green]Agendador ativo[/] — arquivo: [cyan]{local}[/]\n"
        f"intervalo: {intervalo}s · callback: {callback or '(nenhum)'}\n"
        "[dim]Ctrl+C para encerrar[/]",
        title="Aegis Cron", border_style="green"))

    def _processar() -> None:
        processados = ag.executar_vencidos(app, caminho=local, webhook_url=callback)
        for r in processados:
            estado: str = r.get("estado") or "desconhecido"
            simbolo = {"agendado": "🔁", "concluido": "✅", "falhou": "❌"}.get(estado, "⏸")
            linha = (
                f"[cyan]{simbolo}[/] [{r['id']}] {str(r['tarefa'])[:60]} — "
                f"[{'green' if estado in ('concluido', 'agendado') else 'red'}]{estado}[/]"
            )
            if r.get("erro"):
                linha += f" · {str(r['erro'])[:120]}"
            console.print(linha)
        if not processados:
            console.print("[dim]sem agendamentos vencidos no momento[/]")

    _processar()
    if uma_vez:
        return 0

    try:
        while True:
            time.sleep(intervalo)
            _processar()
    except KeyboardInterrupt:
        console.print("\n[dim]Cron encerrado. Até logo! 👋[/]")
    return 0


def _montar_app_sync():
    """Constrói o grafo com checkpointer síncrono (headless/testes)."""
    checkpointer = criar_checkpointer_sync(config.banco)
    store = criar_store_sync(config.banco)
    ferramentas = carregar_ferramentas()
    llm = criar_llm(config)
    app = montar_grafo(llm, ferramentas, checkpointer=checkpointer, store=store, cfg=config)
    return app, ferramentas


async def _montar_app_async():
    """Constrói o grafo com checkpointer assíncrono (TUI — astream_events)."""
    from aegis.memoria import criar_checkpointer_async

    checkpointer = await criar_checkpointer_async(config.banco)
    store = criar_store_sync(config.banco)
    ferramentas = carregar_ferramentas()
    llm = criar_llm(config)
    app = montar_grafo(llm, ferramentas, checkpointer=checkpointer, store=store, cfg=config)
    return app, ferramentas


def _imprimir_resultado(resultado: dict, cfg) -> None:
    """Imprime o resultado de uma execução headless (painel + ferramentas)."""
    registros = resultado.get("registros_ferramentas") or []
    for reg in registros:
        estilo = "red" if reg.get("erro") else "green"
        titulo = f"🔧 {reg['nome']}"
        corpo = f"args: {reg.get('args')}\n{reg.get('resultado', '')[:1500]}"
        console.print(Panel(Text(corpo), title=f"[bold]{titulo}[/]", border_style=estilo))
    if registros:
        console.print("")

    mensagens = resultado.get("mensagens") or []
    if mensagens and hasattr(mensagens[-1], "content"):
        from rich.markdown import Markdown
        console.print(Panel(Markdown(str(mensagens[-1].content)),
                            title="[bold green]Aegis[/]", border_style="green"))


def executar_headless(app, ferramentas, pergunta: str, cfg) -> dict:
    """Execução síncrona one-shot (também usada nos testes)."""
    from aegis.grafo import executar_headless as _eh
    return _eh(app, pergunta, cfg.thread_id)


def main(argv: list[str] | None = None) -> int:
    args = novo_argumentos().parse_args(argv)
    _aplicar_flags(args)

    if args.versao:
        from aegis import __version__
        console.print(f"[bold green]Aegis[/] [cyan]v{__version__}[/]")
        return 0

    # Comandos de barra e ações auxiliares — sem montar o grafo
    if args.comando is not None:
        from aegis.slash import executar_slash, parsear_slash
        par = parsear_slash(args.comando)
        if par is None:
            console.print("[red]⚠ --comando espera algo como \"/notas\"[/]")
            return 1
        nome, arg = par
        console.print(executar_slash(nome, arg))
        return 0
    if args.papeis is not None:
        from aegis.slash import executar_slash
        if args.papeis:
            console.print(executar_slash("definir_papel", args.papeis))
        else:
            console.print(executar_slash("papeis", ""))
        return 0
    if args.memoria is not None:
        from aegis.slash import executar_slash
        console.print(executar_slash("memoria", args.memoria))
        return 0
    if args.plano:
        from aegis.slash import executar_slash
        console.print(executar_slash("plano", ""))
        return 0
    if args.notas is not None:
        from aegis.slash import executar_slash
        console.print(executar_slash("notas", args.notas))
        return 0
    if args.papers is not None:
        from aegis.slash import executar_slash
        console.print(executar_slash("buscar_paper", args.papers))
        return 0
    if args.obsidian:
        from aegis.obsidian import listar_obsidian_vault
        console.print(listar_obsidian_vault())
        return 0

    ferramentas = carregar_ferramentas()

    if args.listar_ferramentas:
        listar_ferramentas(ferramentas)
        return 0
    if args.listar_skills:
        listar_skills()
        return 0
    if args.exportar_sharegpt is not None:
        return _exportar("sharegpt", args.exportar_sharegpt or None)
    if args.exportar_openai is not None:
        return _exportar("openai", args.exportar_openai or None)
    if args.gateway is not None:
        return _rodar_gateway(args.gateway)
    if args.agenda_uma_vez:
        return _rodar_agendador(config.agendador_intervalo, uma_vez=True)
    if args.agendador is not None:
        intervalo = int(args.agendador or config.agendador_intervalo)
        return _rodar_agendador(intervalo)

    trajetoria = Trajetoria(config.trajetorias_dir) if config.trajetoria_ativa else None

    modo_single = args.pergunta is not None or args.headless
    if modo_single:
        pergunta = args.pergunta or input("Pergunta: ").strip()
        if not pergunta:
            console.print("[yellow]Nenhuma pergunta fornecida.[/]")
            return 1
        try:
            app, ferramentas = _montar_app_sync()
        except ConfigError as exc:
            console.print(Panel(f"[red]{exc}[/]", title="Configuração", border_style="red"))
            return 1
        resultado = executar_headless(app, ferramentas, pergunta, config)
        # Auditabilidade: registra a troca na trajetória (dataset ShareGPT/RL)
        if trajetoria:
            trajetoria.registrar_mensagem_usuario(config.thread_id, pergunta)
            mensagens = resultado.get("mensagens") or []
            if mensagens:
                trajetoria.registrar(config.thread_id, "mensagem_agente", {
                    "conteudo": str(getattr(mensagens[-1], "content", mensagens[-1])),
                })
        _imprimir_resultado(resultado, config)
        return 0

    # TUI interativa (streaming assíncrono)
    async def _rodar_tui() -> int:
        ckpt_async = None
        try:
            app, ferramentas = await _montar_app_async()
            ckpt_async = getattr(app, "checkpointer", None)
        except ConfigError as exc:
            console.print(Panel(f"[red]{exc}[/]", title="Configuração", border_style="red"))
            return 1
        tui = TuiAegis(app, ferramentas, config, trajetoria=trajetoria)
        try:
            await tui.iniciar()
        finally:
            # Fecha a conexão aiosqlite do checkpointer async para evitar o
            # aviso de shutdown "Event loop is closed" no encerramento.
            conn = getattr(ckpt_async, "conn", None)
            if conn is not None:
                try:
                    await conn.close()
                except Exception:  # noqa: BLE001 — teardown nunca quebra a saída
                    pass
        return 0

    try:
        return asyncio.run(_rodar_tui())
    except KeyboardInterrupt:
        console.print("\n[dim]Encerrando Aegis. Até logo! 👋[/]")
        return 0


if __name__ == "__main__":
    sys.exit(main())