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
from ..seguranca import marcar_conteudo

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
    # C5: resultados web são DADO não confiável — marcados com a classificação
    return marcar_conteudo("\n\n".join(blocos), fonte="busca web")


# ---------------------------------------------------------------------
# Execução de comando em sandbox
# ---------------------------------------------------------------------

def _executor_sandbox():
    """Executor do backend configurado (`AEGIS_SANDBOX_BACKEND`)."""
    from ..sandbox import criar_executor
    return criar_executor(config.sandbox_backend, cfg=config)


def _auditar_comando(resultado, comando: str) -> None:
    """Registra a execução em `comandos.jsonl` (backend + comando + código).

    Mesmo arquivo/estilo da auditoria da tool `comando` (sistema.py) —
    campo `backend` em cada registro (C7). A auditoria nunca bloqueia.
    """
    from datetime import datetime, timezone
    import json
    try:
        registro = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "cmd": comando[:200],
            "status": "ok" if resultado.sucesso else "erro",
            "codigo": resultado.codigo,
            "duracao_ms": int(resultado.duracao * 1000),
            "motivo": (resultado.erro or "")[:200],
            "backend": resultado.backend,
        }
        caminho = config.comandos_path
        caminho.parent.mkdir(parents=True, exist_ok=True)
        with open(caminho, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — auditoria nunca derruba o fluxo
        pass


@tool
def comando_sandbox(comando: str, timeout: int = 30) -> str:
    """Executa um comando shell em um sandbox isolado com timeout.

    Backend por `AEGIS_SANDBOX_BACKEND` (local | docker | ssh — default
    local). Docker: container efêmero com rede isolada e artefatos montados
    (denylist de comandos perigosos). SSH: host do .env com allowlist.
    NUNCA execute comandos destrutivos sem confirmação explícita do usuário.
    """
    try:
        timeout = max(1, min(int(timeout), 120))
    except (TypeError, ValueError):
        timeout = 30
    resultado = _executor_sandbox().executar(comando, timeout=timeout)
    _auditar_comando(resultado, comando)
    if resultado.erro:
        return f"ERRO_FERRAMENTA: {resultado.erro}"
    # C5: a saída de um comando é DADO não confiável — marcada com a classificação
    return marcar_conteudo(resultado.resumo(), fonte=f"comando: {comando}")


# ---------------------------------------------------------------------
# Conjunto de ferramentas básicas
# ---------------------------------------------------------------------

def ferramentas_basicas() -> list:
    return [calculadora, hora_atual, buscar_web, comando_sandbox, estatisticas]


# ---------------------------------------------------------------------
# Estatísticas de uso e custo (C6 — paridade caveman-stats)
# ---------------------------------------------------------------------

@tool
def estatisticas(escopo: str = "sessao", formato: str = "texto") -> str:
    """Métricas de uso: tokens, custo estimado e ferramentas executadas.

    Sem rede. `escopo="sessao"` → contabilidade da thread atual (persistida
    no checkpointer, acumulada entre turnos); `escopo="acumulado"` → total de
    todo o banco (todas as threads). `formato="json"` → export JSON das
    métricas (paridade com o status armazenado no checkpointer).
    """
    from ..memoria import criar_checkpointer_sync
    from ..uso import custo_estimado, somar_uso, total_tokens

    sessao: dict[str, int] = {}
    registros: list[dict] = []
    try:
        saver = criar_checkpointer_sync(config.banco)
        if escopo == "acumulado":
            for tupla in saver.list({}):
                valores = ((tupla.checkpoint or {}).get("channel_values") or {})
                sessao = somar_uso(sessao, valores.get("uso_tokens") or {})
        else:
            tupla = saver.get_tuple({"configurable": {"thread_id": config.thread_id}})
            if tupla:
                valores = ((tupla.checkpoint or {}).get("channel_values") or {})
                sessao = dict(valores.get("uso_tokens") or {})
                registros = list((valores.get("registros_ferramentas") or [])[:6])
    except Exception:  # noqa: BLE001 — métricas nunca bloqueiam a resposta
        sessao = {}

    custo = custo_estimado(sessao, config.precos_por_token)
    ok = sum(1 for r in registros if not r.get("erro"))
    n = len(registros)
    por_nome: dict[str, int] = {}
    for r in registros:
        por_nome[r.get("nome", "?")] = por_nome.get(r.get("nome", "?"), 0) + 1
    top = ", ".join(f"`{nome}` ×{qtd}" for nome, qtd in
                    sorted(por_nome.items(), key=lambda kv: -kv[1])[:3])
    taxa = f"{ok}/{n}" if n else "0/0"
    if formato == "json":
        import json
        return json.dumps({
            "escopo": escopo,
            "tokens": sessao,
            "total_tokens": total_tokens(sessao),
            "custo_estimado_reais": custo,
            "ferramentas_turno": {"execucoes": n, "sucesso": taxa,
                                  "top": dict(sorted(por_nome.items(), key=lambda kv: -kv[1])[:3])},
        }, ensure_ascii=False)
    return (
        "📊 **Estatísticas de uso**"
        f" ({'acumulado do banco' if escopo == 'acumulado' else f'sessão `{config.thread_id}`'})\n"
        f"- Tokens: {sessao.get('entrada', 0):,} entrada | "
        f"{sessao.get('saida', 0):,} saída | {sessao.get('reasoning', 0):,} reasoning — "
        f"total {total_tokens(sessao):,}\n"
        f"- Custo estimado: **R$ {custo:.4f}**\n"
        f"- Ferramentas (turno): {n} execuções — taxa de sucesso {taxa}"
        + (f"\n  - top: {top}" if top else "")
    )