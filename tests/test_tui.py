"""Testes da TUI Textual (headless, sem rede/LLM — produtor de eventos fake)."""

import asyncio

from textual.widgets import Input, Markdown

from aegis.tui import TuiAegis


class CfgFake:
    thread_id = "teste"
    modelo = "fake"


def _produtor_texto(texto="Olá, mundo!", tools=()):
    """Factory de produtor que emite frames de token (e opcionalmente tool)."""

    async def gerar():
        if tools:
            yield {"tipo": "tool_inicio", "id": "r1", "nome": "calcular", "args": {"expr": "2+2"}}
        for pedaco in (texto[i:i + 5] for i in range(0, len(texto), 5)):
            yield {"tipo": "token", "texto": pedaco}
        if tools:
            yield {"tipo": "tool_fim", "id": "r1", "nome": "calcular", "saida": "4"}

    return gerar


def _montar(produtor):
    return TuiAegis(app=None, ferramentas=[], cfg=CfgFake(), produtor_eventos=produtor)


def _rodar(app, turno="oi"):
    async def main():
        async with app.run_test() as pilot:
            app.enviar(turno)
            for _ in range(40):
                await pilot.pause()
    asyncio.run(main())
    return app


def test_compose_tem_widgets_essenciais():
    app = _montar(_produtor_texto())

    async def main():
        async with app.run_test() as pilot:
            assert app.query_one("#chat") is not None
            assert app.query_one("#status") is not None
            assert app.query_one(Input) is not None
            await pilot.pause()

    asyncio.run(main())


def test_turno_streama_resposta():
    app = _rodar(_montar(_produtor_texto("Olá, mundo!")))
    assert app.ultima_resposta == "Olá, mundo!"
    assert "tokens" in app.ultima_rodape
    assert "teste" in app.ultima_rodape  # thread id no rodapé


def test_turno_renderiza_pergunta_e_resposta():
    app = _montar(_produtor_texto("Oi!"))
    total = None

    async def main():
        nonlocal total
        async with app.run_test() as pilot:
            app.enviar("oi")
            for _ in range(40):
                await pilot.pause()
            # pergunta + bloco de resposta = pelo menos 2 Markdown visíveis no chat
            total = len(list(app.chat.query(Markdown)))

    asyncio.run(main())
    assert total is not None and total >= 2


def test_turno_com_ferramenta_expõe_saida():
    app = _rodar(_montar(_produtor_texto("usando ferramenta", tools=("calcular",))))
    assert app.ultima_resposta == "usando ferramenta"
    assert app.ultima_saida == "4"


def test_comando_sair_nao_dispara_turno():
    app = _montar(_produtor_texto())

    async def main():
        async with app.run_test() as pilot:
            entrada = app.query_one(Input)
            app.on_input_submitted(Input.Submitted(entrada, "fim"))
            for _ in range(10):
                await pilot.pause()

    asyncio.run(main())
    assert app.ultima_resposta == ""  # nenhum turno chegou a rodar
    assert app.ultima_rodape == ""


def test_pergunta_vazia_ignorada():
    app = _montar(_produtor_texto())

    async def main():
        async with app.run_test() as pilot:
            entrada = app.query_one(Input)
            app.on_input_submitted(Input.Submitted(entrada, "   "))
            await pilot.pause()

    asyncio.run(main())
    assert app.ultima_resposta == ""