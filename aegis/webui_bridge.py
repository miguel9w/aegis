"""
Ponte web — processo persistente que executa o grafo do Aegis via
`astream_events(version="v2")` (mesmo produtor da TUI, provado em 237 testes)
e fala JSONL com o Bun: 1 comando/linha no stdin → N frames/linhas no stdout.

Frames (todos com `job_id`, exceto respostas de comando):
- token / reasoning  — deltas com campo `cumulativo` (recuperação pós-reconnect)
- tool_inicio / tool_fim — ciclo de vida das ferramentas (id + args/saída)
- arquivo / comando — derivados das ferramentas do sistema (diff / auditoria)
- subgrafo — on_chain_start/end com nome `sub_<dominio>` (multiagente/delegação)
- veredito — avaliador do multiagente (estado final)
- fim (estado_final redigido + texto) / metrica / erro

Comandos: mensagem (job_id, texto, thread_id, dominio?), historico, estado,
sugestoes, slash (nome, arg), ping.
Execução: `python -m aegis.webui_bridge` (spawnada pelo Bun — 1 job por vez
equivale à TUI; o Bun mantém a fila FIFO).
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import threading
import time
from typing import Any, AsyncIterator

from langchain_core.messages import AIMessage, HumanMessage

from .config import config
from .config_json import carregar_config_json as _cfg_json
from .ferramentas import recarregar_tudo
from .grafo import montar_grafo
from .llm import criar_llm

_LIMITE_TOOL = 2000          # truncamento de saída de ferramenta (igual TUI)
_LIMITE_ESTADO = 8000        # estado final redigido
_LIMITE_HISTORICO = 50

_SEGREDO = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH|CREDENTIAL|SENHA)", re.I)

_APP: Any = None
_N_FERRAMENTAS = 0


async def _montar_app_async(cfg: Any) -> tuple[Any, int]:
    """Caminho da TUI: checkpointer async + store sync (threads compartilhadas)."""
    from .memoria import criar_checkpointer_async, criar_store_sync
    checkpointer = await criar_checkpointer_async(cfg.banco)
    store = criar_store_sync(cfg.banco)
    ferramentas = recarregar_tudo(cfg)
    app = montar_grafo(criar_llm(cfg), ferramentas,
                       checkpointer=checkpointer, store=store, cfg=cfg)
    return app, len(ferramentas)


def montar_app(cfg: Any = None) -> Any:
    """Compila o grafo uma única vez (processo persistente da ponte)."""
    global _APP, _N_FERRAMENTAS
    if _APP is None:
        cfg = cfg or config
        _APP, _N_FERRAMENTAS = asyncio.run(_montar_app_async(cfg))
    return _APP


# ---------------------------------------------------------------------
# Redação de segredos e truncamento (chaves NUNCA saem da ponte)
# ---------------------------------------------------------------------


def _redigir(arv: Any, profundidade: int = 0) -> Any:
    """Recursivamente redige chaves sensíveis e trunca strings longas."""
    if profundidade > 6:
        return "[…]"
    if isinstance(arv, dict):
        saida = {}
        for k, v in arv.items():
            if _SEGREDO.search(str(k)):
                saida[k] = "[REDACTED]"
            else:
                saida[k] = _redigir(v, profundidade + 1)
        return saida
    if isinstance(arv, list):
        return [_redigir(x, profundidade + 1) for x in arv[:50]]
    if isinstance(arv, str):
        return arv if len(arv) <= 2000 else arv[:2000] + "…"
    return arv


def _truncar_json(obj: Any, limite: int = _LIMITE_ESTADO) -> str:
    txt = json.dumps(obj, ensure_ascii=False, default=str)
    if len(txt) > limite:
        txt = txt[:limite] + f"\n… ({len(txt)} caracteres, truncado)"
    return txt


# ---------------------------------------------------------------------
# Execução de um job (gerador de frames — testável sem subprocesso)
# ---------------------------------------------------------------------


def _extrair_vereditos(saida: dict) -> list[dict]:
    """Vereditos do multiagente no estado final."""
    vereditos = saida.get("vereditos") or []
    if not isinstance(vereditos, list):
        return []
    return [v for v in vereditos if isinstance(v, dict)]


def _texto_final(saida: dict, acumulado: str) -> str:
    orquestracao = saida.get("orquestracao_final")
    if isinstance(orquestracao, str) and orquestracao.strip():
        return orquestracao
    mensagens = saida.get("mensagens") or []
    for msg in reversed(mensagens):
        if isinstance(msg, AIMessage):
            conteudo = getattr(msg, "content", "")
            if isinstance(conteudo, str) and conteudo.strip():
                return conteudo
    return acumulado


def _processar_evento(evento: dict, est: dict) -> list[dict]:
    """Converte 1 evento cru do astream_events v2 em 0..N frames (contrato).

    `est` são os acumuladores do job: acumulado_texto, acumulado_razao,
    n_chunks, inputs_tools, ultimo_output. Frames saem SEM job_id (o loop
    do job anexa). Função pura — testável com eventos sintéticos (o runtime
    1.x não emite on_chat_model_stream em invoke de modelos customizados;
    mesmo motivo dos testes da TUI com produtor injetável).
    """
    frames: list[dict] = []
    kind = evento.get("event", "")
    if kind == "on_chat_model_stream" and "resposta" in (evento.get("tags") or []):
        chunk = (evento.get("data") or {}).get("chunk")
        conteudo = getattr(chunk, "content", None)
        if isinstance(conteudo, str) and conteudo:
            est["n_chunks"] += 1
            est["acumulado_texto"] += conteudo
            frames.append({"kind": "token", "texto": conteudo,
                           "cumulativo": est["acumulado_texto"]})
        kw = getattr(chunk, "additional_kwargs", {}) or {}
        razao = kw.get("reasoning_content")
        if isinstance(razao, str) and razao:
            est["acumulado_razao"] += razao
            frames.append({"kind": "reasoning", "texto": razao,
                           "cumulativo": est["acumulado_razao"]})
    elif kind == "on_tool_start":
        dado = (evento.get("data") or {}).get("input") or {}
        ident = evento.get("run_id") or ""
        if not ident:
            ident = f"f{len(est['inputs_tools'])}"
        est["inputs_tools"][ident] = dado if isinstance(dado, dict) else {}
        frames.append({"kind": "tool_inicio", "id": ident,
                       "nome": evento.get("name") or "?", "args": est["inputs_tools"][ident]})
    elif kind == "on_tool_end":
        obj = (evento.get("data") or {}).get("output")
        texto_saida = str(getattr(obj, "content", obj))[:_LIMITE_TOOL]
        nome = getattr(obj, "name", "") or evento.get("name") or "?"
        ident = evento.get("run_id") or ""
        args = est["inputs_tools"].pop(ident, {})
        frames.append({"kind": "tool_fim", "id": ident, "nome": nome, "saida": texto_saida})
        if nome in ("escrever_arquivo", "editar_arquivo"):
            frames.append({
                "kind": "arquivo",
                "acao": "escrever" if nome == "escrever_arquivo" else "editar",
                "caminho": args.get("caminho", ""),
                "diff": texto_saida,
                "status": "ok" if not texto_saida.startswith("erro") else "erro",
            })
        elif nome == "executar_comando":
            status, motivo = "ok", ""
            if "recusado pela política" in texto_saida:
                status, motivo = "recusado", "politica"  # denylist — nunca autorizável
            elif "recusado" in texto_saida and "confirmar=True" in texto_saida:
                status, motivo = "recusado", "confirmacao"  # janela de perguntas
            elif texto_saida.startswith("erro") or "nan" in texto_saida.lower():
                status = "erro"
            m = re.search(r"duração=(\d+)ms", texto_saida)
            frames.append({
                "kind": "comando", "cmd": args.get("comando", ""),
                "status": status, "motivo": motivo,
                "duracao_ms": int(m.group(1)) if m else 0,
                "resumo": texto_saida.splitlines()[0] if texto_saida else "",
                "confirmado": bool(args.get("confirmar")),
            })
    elif kind == "on_chain_end":
        saida = (evento.get("data") or {}).get("output")
        if isinstance(saida, dict) and saida:
            est["ultimo_output"] = saida  # o mais externo vence (igual TUI)
            # C6: nó cortou por orçamento → aviso visível na UI (frame novo)
            corte = saida.get("orcamento_estourado")
            if isinstance(corte, dict):
                frames.append({"kind": "orcamento", **corte})
        nome_chain = evento.get("name") or ""
        if nome_chain.startswith("sub_"):
            frames.append({"kind": "subgrafo", "nome": nome_chain,
                           "evento": "end", "nivel": 1, "tipo": "multiagente"})
    elif kind == "on_chain_start":
        nome_chain = evento.get("name") or ""
        if nome_chain.startswith("sub_"):
            frames.append({"kind": "subgrafo", "nome": nome_chain,
                           "evento": "start", "nivel": 1, "tipo": "multiagente"})
    return frames


def _estado_job() -> dict:
    return {
        "acumulado_texto": "", "acumulado_razao": "", "n_chunks": 0,
        "inputs_tools": {}, "ultimo_output": {},
    }


async def executar_job(
    app: Any,
    texto: str,
    thread_id: str,
    job_id: str,
    cfg: Any = None,
    dominio: str = "",
) -> AsyncIterator[dict]:
    """Roda um turno e produz os frames do protocolo (token→…→fim/erro).

    `dominio` (opcional) força o subgrafo multiagente correspondente —
    metadado lido pelo orquestrador (web UI com `@programacao`).
    """
    cfg = cfg or config
    inicio = time.monotonic()
    est = _estado_job()

    def frame(**campos: Any) -> dict:
        return {"job_id": job_id, **campos}

    configurar = {
        "configurable": {"thread_id": thread_id},
        # O LangGraph lê `recursion_limit` no TOPO do config (default 25) —
        # dentro de `configurable` ele é ignorado e turnos longos (ex.: revisão
        # com muitas ferramentas) morriam com GraphRecursionError aos 25 passos.
        # Mesmo formato da TUI (aegis/tui.py).
        "recursion_limit": getattr(cfg, "recursion_limit", 50),
    }
    entrada = {
        "mensagens": [HumanMessage(texto)],
        "metadados_sessao": {"thread_id": thread_id, **({"dominio": dominio} if dominio else {})},
    }
    falha: Exception | str | None = None
    try:
        async for evento in app.astream_events(entrada, config=configurar, version="v2"):
            for f in _processar_evento(evento, est):
                yield frame(**f)
    except asyncio.CancelledError:
        # interrompido pelo usuário: silêncio total do gerador (o wrapper
        # `_rodar_job` emite o frame `fim` com interrompido=True)
        falha = "interrompido"
        raise
    except Exception as exc:  # noqa: BLE001 — erro vira frame, nunca quebra a ponte
        falha = exc
        yield frame(kind="erro", tipo=type(exc).__name__, mensagem=str(exc)[:1000])
    finally:
        if falha is None:
            for v in _extrair_vereditos(est["ultimo_output"]):
                yield frame(kind="veredito", veredito=_redigir(v))
            duracao = time.monotonic() - inicio
            tps = round(est["n_chunks"] / duracao, 1) if duracao > 0 else 0.0
            yield frame(kind="metrica", tokens=est["n_chunks"],
                        duracao_s=round(duracao, 2), tps=tps)
            # `fim` é SEMPRE o último (o Bun fecha o job nele — o que vier
            # depois seria descartado)
            texto_final = _texto_final(est["ultimo_output"], est["acumulado_texto"])
            yield frame(kind="fim", texto=texto_final,
                        estado_final=_truncar_json(_redigir(est["ultimo_output"])))


# ---------------------------------------------------------------------
# Comandos síncronos (ping/estado/historico) — testáveis
# ---------------------------------------------------------------------


def snapshot_estado(cfg: Any = None) -> dict:
    cfg = cfg or config
    limites = _cfg_json("limites.json", {})
    return {
        "versao": "dev",
        "modelo": cfg.modelo,
        "multiagente": cfg.multiagente_ativos,
        "subagentes": cfg.subagentes_ativos,
        "n_ferramentas": _N_FERRAMENTAS,
        "artefatos": str(cfg.artefatos_dir),
        "thread_id": cfg.thread_id,
        "limites": limites,
    }


async def listar_historico(app: Any, limite: int = _LIMITE_HISTORICO) -> list[dict]:
    """Threads do checkpointer (AsyncSqliteSaver — .alist no main thread).
    Nunca lança — [] em falha."""
    cp = getattr(app, "checkpointer", None)
    if cp is None or not hasattr(cp, "alist"):
        return []
    try:
        resultado = []
        async for cfg_item in cp.alist(None, limit=limite):
            if isinstance(cfg_item, dict):
                configurable = cfg_item.get("configurable") or {}
            else:  # CheckpointTuple: .config
                configurable = getattr(cfg_item, "config", {}) or {}
                configurable = configurable.get("configurable") or {}
            resultado.append({
                "thread_id": configurable.get("thread_id", "?"),
                "atualizada": str(configurable.get("updated_at", "")),
            })
        return resultado
    except Exception:  # noqa: BLE001
        return []


def snapshot_sugestoes() -> dict:
    """Catálogo das sugestões do input da web UI (`/`, `@`).

    Uma única fonte de verdade: os registros reais do Aegis (slash da TUI,
    domínios multiagente, fichas APF e papéis) — o front nunca hardcoda.
    """
    from .multiagente import DOMINIOS
    from .papeis import carregar_papeis
    from .prompts_avancados import carregar_prompts_avancados
    from .slash import IMPLEMENTADOS

    dominios = [
        {
            "nome": nome,
            "descricao": f"subgrafo multiagente: {', '.join(papel.split('(')[0].strip() for _, papel in d.slots[:2])}…",
        }
        for nome, d in DOMINIOS.items()
    ]
    prompts = [
        {"id": str(f.get("id", "")), "versao": str(f.get("versao", "")),
         "descricao": str(f.get("descricao", ""))}
        for f in carregar_prompts_avancados().values() if f.get("id")
    ]
    papeis = [{"nome": p.nome, "descricao": p.descricao} for p in carregar_papeis()]
    return {
        "comandos": [{"nome": n, "descricao": d} for n, d in sorted(IMPLEMENTADOS.items())],
        "agentes": dominios,
        "prompts": prompts,
        "papeis": papeis,
    }


def processar_comando(cmd: dict, app: Any = None) -> str:
    """Processa um comando síncrono e devolve a linha JSON de resposta."""
    acao = cmd.get("cmd")
    if acao == "ping":
        return json.dumps({"cmd": "pong"})
    if acao == "interromper":
        # no-op no modo síncrono (o cancelamento real é do _main_loop)
        return json.dumps({"cmd": "interromper", "ok": True})
    if acao == "estado":
        return json.dumps({"cmd": "estado", "dados": snapshot_estado()}, ensure_ascii=False)
    if acao == "sugestoes":
        return json.dumps({"cmd": "sugestoes", "dados": snapshot_sugestoes()},
                          ensure_ascii=False)
    if acao == "slash":
        from .slash import executar_slash
        nome = str(cmd.get("nome") or "")
        texto = executar_slash(nome, str(cmd.get("arg") or ""))
        return json.dumps({"cmd": "slash", "nome": nome, "texto": texto},
                          ensure_ascii=False)
    if acao == "historico":
        limite = int(cmd.get("limit", _LIMITE_HISTORICO))
        threads = asyncio.run(listar_historico(app or montar_app(), limite))
        return json.dumps(
            {"cmd": "historico", "threads": threads},
            ensure_ascii=False,
        )
    return json.dumps({"erro": f"comando desconhecido: {acao!r}"}, ensure_ascii=False)


def _emitir_job(app: Any, cmd: dict) -> None:
    """Executa um turno e imprime todos os frames (com flush)."""
    job_id = str(cmd.get("job_id") or "j-0")
    texto = str(cmd.get("texto") or "")
    thread_id = str(cmd.get("thread_id") or config.thread_id)

    async def rodar() -> None:
        async for f in executar_job(app, texto, thread_id, job_id):
            print(json.dumps(f, ensure_ascii=False), flush=True)

    asyncio.run(rodar())


async def _rodar_job(app: Any, cmd: dict) -> None:
    """Roda um turno como task independente (cancelável pelo comando
    `interromper`). O cancel emite `fim` com interrompido=True — nunca um erro."""
    job_id = str(cmd.get("job_id") or "j-0")
    texto = str(cmd.get("texto") or "")
    thread_id = str(cmd.get("thread_id") or config.thread_id)
    dominio = str(cmd.get("dominio") or "")
    try:
        async for f in executar_job(app, texto, thread_id, job_id, dominio=dominio):
            print(json.dumps(f, ensure_ascii=False), flush=True)
    except asyncio.CancelledError:
        print(json.dumps({
            "job_id": job_id, "kind": "fim",
            "texto": "(turno interrompido pelo usuário)",
            "estado_final": None, "interrompido": True,
        }, ensure_ascii=False), flush=True)
    except Exception as exc:  # noqa: BLE001 — nunca derruba a ponte
        print(json.dumps({"job_id": job_id, "kind": "erro",
                          "tipo": type(exc).__name__, "mensagem": str(exc)[:1000]},
                         ensure_ascii=False), flush=True)


async def _main_loop() -> None:
    """Loop principal da ponte — UM event loop para montagem + todos os jobs
    (o AsyncSqliteSaver prende conexões/locks ao loop; dois loops = RuntimeError).
    O stdin é lido de forma assíncrona (add_reader) para que o comando
    `interromper` chegue enquanto um turno ainda roda."""
    app, _ = await _montar_app_async(config)
    sys.stderr.write(f"[ponte] Aegis bridge pronta — modelo {config.modelo}\n")
    sys.stderr.flush()
    fila: asyncio.Queue[str | None] = asyncio.Queue()
    loop = asyncio.get_running_loop()

    def _push() -> None:
        linha = sys.stdin.readline()
        fila.put_nowait(linha if linha else None)

    try:
        loop.add_reader(sys.stdin.fileno(), _push)
    except Exception:  # noqa: BLE001 — stdin sem fileno (testes/StringIO) → thread
        def _alimentar() -> None:
            for l in sys.stdin:
                fila.put_nowait(l)
            fila.put_nowait(None)

        threading.Thread(target=_alimentar, daemon=True).start()
    tarefa: asyncio.Task[None] | None = None
    while True:
        linha = await fila.get()
        if linha is None:
            if tarefa is not None and not tarefa.done():
                tarefa.cancel()
            break
        linha = linha.strip()
        if not linha:
            continue
        try:
            cmd = json.loads(linha)
            if not isinstance(cmd, dict):
                raise ValueError("comando não é objeto JSON")
        except Exception as exc:  # noqa: BLE001 — linha malformada não derruba a ponte
            print(json.dumps({"erro": f"linha inválida: {exc}"}, ensure_ascii=False),
                  flush=True)
            continue
        acao = cmd.get("cmd")
        if acao == "ping":
            print('{"cmd": "pong"}', flush=True)
        elif acao == "estado":
            print(json.dumps({"cmd": "estado", "dados": snapshot_estado()},
                             ensure_ascii=False), flush=True)
        elif acao == "sugestoes":
            print(json.dumps({"cmd": "sugestoes", "dados": snapshot_sugestoes()},
                             ensure_ascii=False), flush=True)
        elif acao == "slash":
            from .slash import executar_slash
            nome = str(cmd.get("nome") or "")
            print(json.dumps({"cmd": "slash", "nome": nome,
                              "texto": executar_slash(nome, str(cmd.get("arg") or ""))},
                             ensure_ascii=False), flush=True)
        elif acao == "historico":
            limite = int(cmd.get("limit", _LIMITE_HISTORICO))
            print(json.dumps({"cmd": "historico",
                              "threads": await listar_historico(app, limite)},
                             ensure_ascii=False), flush=True)
        elif acao == "mensagem":
            if tarefa is not None and not tarefa.done():
                print(json.dumps({"cmd": "mensagem",
                                  "erro": "ocupado: outro turno em execução"},
                                 ensure_ascii=False), flush=True)
                continue
            tarefa = asyncio.create_task(_rodar_job(app, cmd))
        elif acao == "interromper":
            if tarefa is not None and not tarefa.done():
                tarefa.cancel()
            print('{"cmd": "interromper", "ok": true}', flush=True)
        elif acao == "autorizar":
            from .autorizacoes import aprovar_comando
            aprovado = aprovar_comando(command := str(cmd.get("comando") or ""))
            print(json.dumps({"cmd": "autorizar", "ok": aprovado, "comando": command},
                             ensure_ascii=False), flush=True)
        else:
            print(json.dumps({"erro": f"comando desconhecido: {acao!r}"},
                             ensure_ascii=False), flush=True)


def main() -> None:
    """Ponto de entrada do processo (spawnado pelo Bun). Lê JSONL do stdin."""
    asyncio.run(_main_loop())


if __name__ == "__main__":
    main()