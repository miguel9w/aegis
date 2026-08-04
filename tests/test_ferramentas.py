"""Testes das ferramentas básicas (cálculo seguro, hora, busca web, sandbox)."""

from __future__ import annotations

import pytest

from aegis.ferramentas.basicas import (
    _avaliar_ast,
    calculadora,
    executar_comando,
    hora_atual,
)


# ---------------------------------------------------------------------
# Calculadora segura (AST com whitelist)
# ---------------------------------------------------------------------

def test_calculadora_basica():
    assert calculadora.invoke({"expressao": "2 + 2"}) == "2 + 2 = 4"


def test_calculadora_precedencia_e_funcoes():
    resultado = calculadora.invoke({"expressao": "sqrt(16) + 2 ** 3 * (3 - 1)"})
    assert resultado == "sqrt(16) + 2 ** 3 * (3 - 1) = 20"


def test_calculadora_constantes():
    resultado = calculadora.invoke({"expressao": "pi * 2"})
    assert resultado.startswith("pi * 2 = 6.283")


def test_calculadora_bloqueia_codigo_arbitrario():
    for maliciosa in [
        "__import__('os').system('echo hack')",
        "import os",
        "open('/etc/passwd')",
        "lambda: 1",
        "a = 1",
        "[x for x in range(10)]",
    ]:
        resultado = calculadora.invoke({"expressao": maliciosa})
        assert resultado.startswith("ERRO_FERRAMENTA:"), maliciosa


def test_calculadora_erro_sintaxe():
    assert calculadora.invoke({"expressao": "2 +" }).startswith("ERRO_FERRAMENTA:")


def test_avaliar_ast_direto():
    assert _avaliar_ast(__import__("ast").parse("3 * 7", mode="eval")) == 21.0


# ---------------------------------------------------------------------
# Hora atual
# ---------------------------------------------------------------------

def test_hora_atual_fuso_valido():
    saida = hora_atual.invoke({"fuso": "America/Sao_Paulo"})
    assert saida.endswith("(America/Sao_Paulo)")
    assert "/" in saida  # data no formato dd/mm/aaaa


def test_hora_atual_fuso_invalido():
    saida = hora_atual.invoke({"fuso": "Zona/Inexistente"})
    assert saida.startswith("ERRO_FERRAMENTA:")


# ---------------------------------------------------------------------
# Sandbox local
# ---------------------------------------------------------------------

def test_executar_comando_sucesso():
    saida = executar_comando.invoke({"comando": "echo ola-aegis"})
    assert "ola-aegis" in saida
    assert "código=0" in saida


def test_executar_comando_falha():
    saida = executar_comando.invoke({"comando": "comando_que_nao_existe_zzz"})
    assert saida.startswith("ERRO_FERRAMENTA:")


def test_executar_comando_timeout():
    saida = executar_comando.invoke({"comando": "sleep 5", "timeout": 1})
    assert "tempo esgotado" in saida


# ---------------------------------------------------------------------
# Busca web (com monkeypatch para não tocar a rede)
# ---------------------------------------------------------------------

def test_buscar_web_ddgs(monkeypatch):
    import aegis.ferramentas.basicas as mod_basicas
    from aegis.config import config as cfg
    monkeypatch.setattr(cfg, "searxng_url", "")

    def fake_ddgs_text(*args, **kwargs):
        return [
            {"href": "https://exemplo.com/1", "title": "Título Um", "body": "Trecho do resultado."},
            {"href": "https://exemplo.com/2", "title": "Título Dois", "body": ""},
        ]

    class DDGSFake:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, consulta, max_results):
            return fake_ddgs_text()

    monkeypatch.setattr(mod_basicas, "DDGS", DDGSFake)
    saida = mod_basicas.buscar_web.invoke({"consulta": "langgraph", "max_resultados": 2})
    assert "Título Um" in saida
    assert "exemplo.com/1" in saida
    assert "Título Dois" in saida


def test_buscar_web_searxng(monkeypatch):
    import aegis.ferramentas.basicas as mod_basicas
    from aegis.config import config as cfg
    monkeypatch.setattr(cfg, "searxng_url", "http://localhost:8888")

    class RespFake:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [
                {"title": "SX Titulo", "url": "https://sx.local/1", "content": "conteudo sx"},
            ]}

    monkeypatch.setattr(mod_basicas.requests, "get", lambda *a, **k: RespFake())
    saida = mod_basicas.buscar_web.invoke({"consulta": "teste", "max_resultados": 5})
    assert "SX Titulo" in saida


def test_buscar_web_sem_resultados(monkeypatch):
    import aegis.ferramentas.basicas as mod_basicas
    from aegis.config import config as cfg
    monkeypatch.setattr(cfg, "searxng_url", "")

    class DDGSVazio:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def text(self, consulta, max_results):
            return []

    monkeypatch.setattr(mod_basicas, "DDGS", DDGSVazio)
    saida = mod_basicas.buscar_web.invoke({"consulta": "nada", "max_resultados": 5})
    assert "nenhum resultado" in saida.lower()