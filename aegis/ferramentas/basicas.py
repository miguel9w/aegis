"""
Ferramentas básicas (built-in) registradas com `@tool`.

Demonstram o fluxo de execução e a extensibilidade: busca web, cálculo
seguro, hora atual e execução de comando em sandbox isolado.
"""

from __future__ import annotations

import ast
import math
import operator
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from ddgs import DDGS
from langchain_core.tools import tool

from ..config import config
from ..sandbox import ExecutorLocal

# ---------------------------------------------------------------------
# Cálculo seguro (sem eval arbitrário) — analisa a AST com whitelist
# ---------------------------------------------------------------------

# Operadores binários permitidos
_BINARIOS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
    ast.Div: operator.truediv, ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod, ast.Pow: operator.pow,
}
# Operadores unários permitidos
_UNARIOS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
# Funções matemáticas permitidas
_MATH_FNS = {
    "sqrt": math.sqrt, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "log": math.log, "log10": math.log10, "log2": math.log2, "exp": math.exp,
    "abs": abs, "round": round, "floor": math.floor, "ceil": math.ceil,
    "min": min, "max": max, "pow": pow, "pi": math.pi, "e": math.e,
}


def _avaliar_ast(no: ast.AST) -> float:
    """Avalia um nó da AST com segurança (whitelist de operações)."""
    if isinstance(no, ast.Expression):
        return _avaliar_ast(no.body)
    if isinstance(no, ast.Constant):
        if isinstance(no.value, (int, float)):
            return float(no.value)
        raise ValueError(f"constante não numérica: {no.value!r}")
    if isinstance(no, ast.Name):
        if no.id in _MATH_FNS and callable(_MATH_FNS[no.id]):
            raise ValueError(f"uso inválido da constante {no.id!r}")
        if no.id in {"pi", "e"}:
            return _MATH_FNS[no.id]
        raise ValueError(f"nome não permitido: {no.id!r}")
    if isinstance(no, ast.BinOp) and type(no.op) in _BINARIOS:
        return float(_BINARIOS[type(no.op)](_avaliar_ast(no.left), _avaliar_ast(no.right)))
    if isinstance(no, ast.UnaryOp) and type(no.op) in _UNARIOS:
        return float(_UNARIOS[type(no.op)](_avaliar_ast(no.operand)))
    if isinstance(no, ast.Call) and isinstance(no.func, ast.Name) and no.func.id in _MATH_FNS:
        fn = _MATH_FNS[no.func.id]
        if not callable(fn):
            raise ValueError(f"{no.func.id!r} é uma constante, não uma função")
        args = [_avaliar_ast(a) for a in no.args]
        return float(fn(*args))
    raise ValueError(f"expressão não suportada: {ast.dump(no)}")


@tool
def calculadora(expressao: str) -> str:
    """
    Avalia uma expressão aritmética com segurança (sem eval arbitrário).
    Suporta + - * / // % **, parênteses e funções matemáticas
    (sqrt, sin, cos, tan, log, log10, log2, exp, abs, round, floor, ceil,
    min, max, pow), além das constantes pi e e.
    Exemplo: "2 * (3 + 4) ** 2 + sqrt(16)".
    """
    try:
        arvore = ast.parse(expressao, mode="eval")
        resultado = _avaliar_ast(arvore)
        return f"{expressao} = {resultado:g}"
    except Exception as exc:  # noqa: BLE001 — retorna erro amigável p/ reflexão
        return f"ERRO_FERRAMENTA: expressão inválida: {exc}"


# ---------------------------------------------------------------------
# Hora atual
# ---------------------------------------------------------------------

@tool
def hora_atual(fuso: str = "America/Sao_Paulo") -> str:
    """Retorna a data e hora atuais em um fuso horário IANA (ex.: America/Sao_Paulo, UTC)."""
    try:
        zona = ZoneInfo(fuso)
    except ZoneInfoNotFoundError:
        return f"ERRO_FERRAMENTA: fuso horário inválido: {fuso!r}. Use um nome IANA."
    agora = datetime.now(zona)
    return f"{agora:%d/%m/%Y %H:%M:%S} ({fuso})"


# ---------------------------------------------------------------------
# Busca web: DuckDuckGo (DDGS) ou SearXNG como fallback/alternativa
# ---------------------------------------------------------------------

def _buscar_ddgs(consulta: str, max_resultados: int) -> list[dict]:
    with DDGS() as ddgs:
        registros = ddgs.text(consulta, max_results=max_resultados)
    return [r for r in registros if r.get("href")]


def _buscar_searxng(consulta: str, max_resultados: int) -> list[dict]:
    resp = requests.get(
        f"{config.searxng_url}/search",
        params={"q": consulta, "format": "json"},
        timeout=15,
    )
    resp.raise_for_status()
    dados = resp.json()
    return dados.get("results", [])[:max_resultados]


@tool
def buscar_web(consulta: str, max_resultados: int = 5) -> str:
    """Busca na web (DuckDuckGo; usa SearXNG se AEGIS_SEARXNG_URL estiver configurado).

    Retorna uma lista numerada de resultados com título, URL e trecho.
    Use quando precisar de informação atualizada/externa.
    """
    limite = max(1, min(int(max_resultados), 10))
    try:
        if config.searxng_url:
            resultados = _buscar_searxng(consulta, limite)
        else:
            resultados = _buscar_ddgs(consulta, limite)
    except Exception as exc:  # noqa: BLE001
        return f"ERRO_FERRAMENTA: falha na busca web: {exc}"

    if not resultados:
        return "Busca concluída, mas nenhum resultado relevante foi encontrado."

    blocos = []
    for i, r in enumerate(resultados, 1):
        titulo = r.get("title") or "(sem título)"
        url = r.get("href") or r.get("url") or ""
        trecho = (r.get("body") or r.get("content") or "").strip()[:300]
        bloco = f"{i}. {titulo}\n   {url}"
        if trecho:
            bloco += f"\n   {trecho}"
        blocos.append(bloco)
    return "\n\n".join(blocos)


# ---------------------------------------------------------------------
# Execução de comando em sandbox
# ---------------------------------------------------------------------

_executor = ExecutorLocal()


@tool
def executar_comando(comando: str, timeout: int = 30) -> str:
    """Executa um comando shell em um sandbox isolado local com timeout.

    Use para scripts, automação e operações de arquivo. Retorna saída,
    código de saída e duração. NUNCA execute comandos destrutivos sem
    confirmação explícita do usuário.
    """
    try:
        timeout = max(1, min(int(timeout), 120))
    except (TypeError, ValueError):
        timeout = 30
    resultado = _executor.executar(comando, timeout=timeout)
    if resultado.erro:
        return f"ERRO_FERRAMENTA: {resultado.erro}"
    return resultado.resumo()


# ---------------------------------------------------------------------
# Conjunto de ferramentas básicas
# ---------------------------------------------------------------------

def ferramentas_basicas() -> list:
    return [calculadora, hora_atual, buscar_web, executar_comando]