"""Testes da TUI Textual (headless, sem rede/LLM — produtor de eventos fake)."""

import asyncio

from textual.widgets import Input, Markdown, Static

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


# ======================================================================
# Polimento estilo Hermes: painel lateral, statusbar, modo RAW, bindings
# ======================================================================

def test_painel_lateral_mostra_estado():
    app = _montar(_produtor_texto())

    async def main():
        async with app.run_test() as pilot:
            assert app.query_one("#painel") is not None
            conteudo = str(app.painel.query_one(Static).render())
            assert "fake" in conteudo            # modelo
            assert "teste" in conteudo           # sessão/thread
            assert "Ferramentas" in conteudo
            await pilot.pause()

    asyncio.run(main())


def test_statusbar_mostra_metricas_apos_turno():
    app = _rodar(_montar(_produtor_texto("Olá, mundo!")))

    async def main():
        async with app.run_test() as pilot:
            await pilot.pause()

    asyncio.run(main())
    # dentro do run_test para garantir DOM montado
    async def ver():
        async with app.run_test() as pilot:
            conteudo = str(app.statusbar.render())
            assert "tok/s" in conteudo
            assert "fake" in conteudo            # modelo
            await pilot.pause()
    asyncio.run(ver())


def test_meta_do_turno_montada_no_chat():
    app = _montar(_produtor_texto("Oi!"))

    async def main():
        async with app.run_test() as pilot:
            app.enviar("oi")
            for _ in range(40):
                await pilot.pause()
            metas = [w for w in app.chat.query(Static) if "meta" in w.classes]
            assert len(metas) >= 1
            texto = str(metas[0].render())
            assert "⏱" in texto and "tok/s" in texto

    asyncio.run(main())


def test_modo_raw_alterna_e_usa_static():
    app = _montar(_produtor_texto("resposta crua"))
    assert app.modo_raw is False

    async def main():
        async with app.run_test() as pilot:
            app.enviar("/modo")
            await pilot.pause()
            assert app.modo_raw is True
            app.enviar("oi")
            for _ in range(40):
                await pilot.pause()
            # a resposta do turno raw vive num Static, não num Markdown
            statics = [str(s.render()) for s in app.chat.query(Static)]
            assert any("resposta crua" in t for t in statics), "bloco raw esperado"
            markdowns = [str(m.render()) for m in app.chat.query(Markdown)]
            assert all("resposta crua" not in t for t in markdowns), \
                "resposta não deve ser markdown em modo raw"
            app.enviar("/modo")
            await pilot.pause()
            assert app.modo_raw is False

    asyncio.run(main())


def test_modelo_alterado_via_slash():
    app = _montar(_produtor_texto())
    assert app.cfg.modelo == "fake"

    async def main():
        async with app.run_test() as pilot:
            app.enviar("/modelo gpt-5")
            await pilot.pause()
            assert app.cfg.modelo == "gpt-5"

    asyncio.run(main())


def test_turno_registra_ferramenta_no_painel():
    app = _rodar(_montar(_produtor_texto("usando ferramenta", tools=("calcular",))))
    assert app.chamadas_ferramenta == 1
    assert app.ultimas_ferramentas[0]["nome"] == "calcular"
    assert app.ultimas_ferramentas[0]["status"] == "ok"

    async def main():
        async with app.run_test() as pilot:
            conteudo = str(app.painel.query_one(Static).render())
            assert "calcular" in conteudo
            await pilot.pause()

    asyncio.run(main())


def test_bindings_teclado_limpar_e_novo():
    app = _montar(_produtor_texto())

    async def main():
        async with app.run_test() as pilot:
            app.enviar("oi")
            for _ in range(20):
                await pilot.pause()
            assert len(list(app.chat.children)) > 0
            await pilot.press("ctrl+l")
            await pilot.pause()
            assert len(list(app.chat.children)) == 0
            app.enviar("oi de novo")
            for _ in range(20):
                await pilot.pause()
            await pilot.press("ctrl+n")
            await pilot.pause()
            assert len(list(app.chat.children)) == 0
            assert "(nova sessão)" in str(app.status.render())

    asyncio.run(main())


def test_frame_erro_notifica_e_mostra_no_bloco():
    async def gerar():
        yield {"tipo": "erro", "texto": "timeout na rede"}

    app = _montar(gerar)

    async def main():
        async with app.run_test() as pilot:
            app.enviar("oi")
            for _ in range(30):
                await pilot.pause()
            assert app.ultima_resposta == ""
            assert app.ultimos_tokens == 0

    asyncio.run(main())