"""G5 — Pausa/retomada com handoff e reversão segura.

Tools do ciclo de trabalho (paridade `gsd-pause-work`/`gsd-resume-work` +
`gsd-undo` + `gsd-forensics`):

- `pausar_trabalho`  : congela o trabalho em andamento — lê o estado do
  checkpointer da thread, deriva os próximos passos por regra (sem LLM) e
  grava o handoff na Store (namespace `handoffs/...`). O estado do ciclo
  G1 (fluxo_trabalho/plano/critérios) já persiste no checkpointer por passo
  — o handoff adiciona o contexto de retomada de LONGa duração.
- `retomar_trabalho` : lê o handoff da thread e devolve o contexto completo
  (fase, pendências, próximos passos ordenados) para o agente continuar o
  ciclo do ponto exato, sem re-executar passos concluídos.
- `reverter_entrega` : reversão segura via `git revert` (a última entrega ou
  um commit específico), SEM afetar o resto do histórico.
- `replay_turno`     : reprodutor forensics — re-executa (sem LLM) os
  `registros_ferramentas` gravados no estado e compara com o resultado
  original (determinismo/diagnóstico).

Nenhuma tool exige rede. As tools de leitura de estado abrem o checkpointer
e a Store do `config.banco` em conexões SEPARADAS (mesma regra do grafo).
"""

from __future__ import annotations

import re
import shlex
import subprocess
import time

from langchain_core.tools import tool

from ..config import RAIZ, config
from ..memoria import (
    criar_checkpointer_sync,
    criar_store_sync,
    namespace_handoff_thread,
)

_PROXIMOS_PASSOS: dict[str, list[str]] = {
    "discuss": [
        "Responder às perguntas de levantamento que ficaram abertas",
        "Confirmar escopo e critérios de aceite com o usuário",
        "Elaborar o plano da entrega",
    ],
    "plan": [
        "Apresentar/validar o plano com o usuário",
        "Executar cada passo do plano em ordem",
    ],
    "execute": [
        "Concluir os passos pendentes do plano",
        "Registrar o commit da wave ao terminar o lote",
    ],
    "verify": [
        "Corrigir os critérios reprovados",
        "Re-verificar os critérios da entrega",
    ],
    "revisar": [
        "Aplicar o feedback da revisão por pares",
        "Re-verificar os critérios e seguir para o ship",
    ],
    "ship": [
        "Confirmar a entrega finalizada",
        "Rodar o UAT com o usuário",
    ],
}

_ORDEM_FASES = ("discuss", "plan", "execute", "verify", "revisar", "ship")

_RE_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")


def _thread_id() -> str:
    """Thread ativa do processo (singleton `config` — mesmo padrão das tools
    `estatisticas`/`consultar_grafo`)."""
    return str(config.thread_id or "default")


def _estado_da_thread(thread_id: str) -> dict:
    """Lê o estado mais recente da thread no checkpointer (conexão própria)."""
    saver = criar_checkpointer_sync(config.banco)
    try:
        tupla = saver.get_tuple({"configurable": {"thread_id": thread_id}})
        if tupla is None:
            return {}
        return dict(tupla.checkpoint.get("channel_values", {}) or {})
    finally:
        try:
            saver.close()
        except Exception:  # noqa: BLE001 — fechamento é melhor esforço
            pass


def _proximos_passos(fluxo: dict) -> list[str]:
    fase = str(fluxo.get("fase") or "")
    base = list(_PROXIMOS_PASSOS.get(fase, _PROXIMOS_PASSOS["execute"]))
    pendentes = [p for p in (fluxo.get("plano") or []) if (p or {}).get("status") != "feito"]
    if pendentes:
        base.insert(0, f"{len(pendentes)} passo(s) pendente(s) do plano: "
                       f"{', '.join(str(p.get('passo', '?'))[:60] for p in pendentes[:3])}")
    return base


@tool
def pausar_trabalho(motivo: str = "pausa solicitada pelo usuário") -> str:
    """Pausa o trabalho em andamento com um HANDOFF completo.

    Congela a fase atual do ciclo de entrega (G1): grava na memória de longo
    prazo o estado do fluxo (fase, plano, critérios, commits) e os próximos
    passos ordenados. Para retomar depois, use `retomar_trabalho` na mesma
    thread — o ciclo continua do ponto exato, sem re-executar o que já foi
    feito. `motivo` documenta por que a entrega foi pausada.
    """
    thread = _thread_id()
    estado = _estado_da_thread(thread)
    fluxo = estado.get("fluxo_trabalho") or {}
    if not fluxo:
        return ("Não há entrega em andamento nesta thread para pausar "
                "(fluxo_trabalho vazio).")
    fase = str(fluxo.get("fase") or "discuss")
    passos = _proximos_passos(fluxo)
    handoff = {
        "thread_id": thread,
        "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        "motivo": motivo[:200],
        "fase_atual": fase,
        "fluxo_trabalho": fluxo,
        "plano": fluxo.get("plano") or [],
        "criterios": fluxo.get("criterios") or [],
        "commits_entrega": estado.get("commits_entrega") or [],
        "proximos_passos": passos,
    }
    store = criar_store_sync(config.banco)
    try:
        store.put(namespace_handoff_thread(thread), f"handoff-{time.strftime('%H%M%S')}",
                  handoff)
    finally:
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass
    pend = sum(1 for p in (fluxo.get("plano") or []) if (p or {}).get("status") != "feito")
    return (
        f"Pausa registrada (motivo: {motivo}). Fase atual: {fase.upper()} "
        f"({pend} passo(s) pendente(s)).\n"
        f"Próximos passos:\n" + "\n".join(f"- {p}" for p in passos) +
        "\nPara retomar, peça 'retome o trabalho' (tool retomar_trabalho)."
    )


@tool
def retomar_trabalho() -> str:
    """Retoma o trabalho pausado: devolve o context completo do handoff.

    Lê o handoff gravado por `pausar_trabalho` na thread atual e devolve
    fase, plano pendente, critérios e próximos passos — o agente continua o
    ciclo G1 do ponto exato (nenhum passo concluído é re-executado).
    """
    thread = _thread_id()
    store = criar_store_sync(config.banco)
    try:
        itens = list(store.search(namespace_handoff_thread(thread)))
    finally:
        try:
            store.close()
        except Exception:  # noqa: BLE001
            pass
    if not itens:
        return "Nenhum handoff encontrado para esta thread — nada a retomar."
    dados = itens[-1].value
    fase = str(dados.get("fase_atual") or "discuss")
    pendentes = [p for p in (dados.get("plano") or []) if (p or {}).get("status") != "feito"]
    linhas = [
        f"CONTEXTO DE RETOMADA — entrega pausada na fase {fase.upper()} "
        f"(motivo: {dados.get('motivo', '—')})",
        f"Pendências: {len(pendentes)} passo(s) do plano.",
    ]
    for p in pendentes[:5]:
        linhas.append(f"- {str(p.get('passo', '?'))[:100]}")
    linhas.append("Próximos passos (ordem):")
    linhas.extend(f"{i + 1}. {p}" for i, p in enumerate(dados.get("proximos_passos") or []))
    linhas.append("Continue o ciclo a partir da fase atual — não repita o que já foi feito.")
    return "\n".join(linhas)


@tool
def reverter_entrega(sha: str = "") -> str:
    """Reverte com segurança a última entrega (ou um commit específico).

    Executa `git revert --no-edit` no repositório do projeto (a entrega vira
    um novo commit REVERT — o histórico não é reescrito e nada mais é
    afetado). Sem `sha`, reverte o HEAD (a última entrega). `sha` precisa
    ser um hash git válido. Auditado nos registros de ferramentas.
    """
    if sha and not _RE_SHA.match(sha):
        return ("ERRO_FERRAMENTA: sha inválido — use um hash de commit git "
                "(7–40 hex) ou deixe vazio para reverter o HEAD.")
    alvo = sha or "HEAD"
    try:
        proc = subprocess.run(
            ["git", "revert", "--no-edit", alvo],
            capture_output=True, text=True, timeout=90, cwd=RAIZ,
        )
    except subprocess.TimeoutExpired:
        return "ERRO_FERRAMENTA: git revert excedeu 90s — entrega não revertida."
    except FileNotFoundError:
        return "ERRO_FERRAMENTA: git não instalado no ambiente."
    if proc.returncode == 0:
        return (f"Entrega revertida com segurança: git revert {alvo} ok.\n"
                f"{proc.stdout.strip()[:500]}")
    return (f"ERRO_FERRAMENTA: git revert {alvo} falhou — {proc.stderr.strip()[:500]} "
            f"(nada foi alterado).")


def _tool_por_nome(nome: str):
    """Localiza a função da ferramenta pelo nome no registro conhecido."""
    from .basicas import ferramentas_basicas
    from .sistema import ferramentas_sistema
    for tool_cand in [*ferramentas_basicas(), *ferramentas_sistema()]:
        if tool_cand.name == nome:
            return tool_cand
    return None


@tool
def replay_turno(limite: int = 8) -> str:
    """Reproduz (forensics) o último turno passo a passo, SEM LLM.

    Re-executa os `registros_ferramentas` gravados no estado com os MESMOS
    argumentos e compara com o resultado original — revela não-determinismo
    e ajuda no diagnóstico sem custo de modelo. `limite` limita quantas
    ferramentas do turno são reproduzidas (default 8). Somente tools do
    registro conhecido são reproduzidas; as demais são relacionadas como
    'não reproduzível'.
    """
    n_repro = max(1, min(int(limite), 50))
    estado = _estado_da_thread(_thread_id())
    registros = list(estado.get("registros_ferramentas") or [])
    if not registros:
        return "Nenhum registro de ferramentas para reproduzir nesta thread."
    linha = [f"Replay do turno — {len(registros)} registro(s), reproduzindo até {n_repro}:", ""]
    iguais = 0
    diferentes = 0
    sem_tool = 0
    for r in registros[-n_repro:]:
        nome = str(r.get("nome") or "?")
        args = r.get("args") or {}
        original = str(r.get("resultado") or "")
        funcao = _tool_por_nome(nome)
        if funcao is None:
            linha.append(f"⤼ {nome}: não reproduzível (fora do registro conhecido)")
            sem_tool += 1
            continue
        try:
            rep = str(funcao.invoke(dict(args)) or "")
        except Exception as exc:  # noqa: BLE001 — replay nunca derruba
            rep = f"ERRO_FERRAMENTA: {exc}"
        igual = (rep.strip() == original.strip())
        iguais += igual
        diferentes += not igual
        marcador = "✓ igual" if igual else "✗ DIFERENTE"
        linha.append(f"{marcador} — {nome}({dict(args)})")
        if not igual:
            linha.append(f"   original  : {original[:120]}")
            linha.append(f"   reproduzido: {rep[:120]}")
    linha.append("")
    linha.append(f"Resumo: {iguais} idêntica(s), {diferentes} diferente(s), "
                 f"{sem_tool} não reproduzível(is).")
    return "\n".join(linha)


def ferramentas_trabalho() -> list:
    """Registro das tools de trabalho (G5)."""
    return [pausar_trabalho, retomar_trabalho, reverter_entrega, replay_turno]