"""
Agendador de tarefas do Aegis — cron interno (paridade Hermes).

Permite agendar tarefas autônomas (lembretes, pesquisas, execuções curtas)
persistidas em `agendamentos.jsonl` (gitignored). Um daemon (`pixi run agendador`)
verifica periodicamente agendamentos VENCIDOS, executa-os no mesmo grafo e
registra resultado/erro; opcionalmente notifica um webhook de callback.

Ferramentas do agente: `agendar`, `listar_agendamentos`, `cancelar_agendamento`.
A lógica de vencimento/execução recebe um instante `agora` explícito, o que a
torna determinística e testável offline (sem disparar rede/relógio).
"""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from langchain_core.tools import tool

from .config_json import carregar_config_json as _cfg_json

# Frequências do cron-configuráveis via config/dados/agendador_config.json
_AGENDADOR_CFG = _cfg_json("agendador_config.json", {
    "frequencias": ["nenhuma", "horaria", "diaria", "semanal"],
})
DEFAULT_FREQUENCIAS = tuple(_AGENDADOR_CFG["frequencias"])

_ESTADOS_VALIDOS = ("agendado", "executando", "concluido", "falhou", "cancelado")


def _fuso_local() -> Any:
    """tzinfo local concreto, com fallback a UTC (evita dep. de tzdata)."""
    try:
        return datetime.now().astimezone().tzinfo or timezone.utc
    except Exception:  # noqa: BLE001 — sem tzdata, usa UTC
        return timezone.utc


_TZ_LOCAL = _fuso_local()


def _agora() -> datetime:
    """Instante atual (UTC). Isolado para permitir freeze em testes."""
    return datetime.now(timezone.utc)


def _caminho_padrao() -> Path:
    from .config import config
    return config.agendamentos_path


def _timestamp(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ_LOCAL)
    return dt.astimezone(timezone.utc).isoformat()


# ---------------------------------------------------------------------
# Persistência (JSONL simples)
# ---------------------------------------------------------------------

class ArmazenamentoAgendamentos:
    """Armazenamento de agendamentos em arquivo JSONL (lock por escrita)."""

    def __init__(self, caminho: Path) -> None:
        self.caminho = Path(caminho)

    def carregar(self) -> list[dict[str, Any]]:
        if not self.caminho.exists():
            return []
        itens: list[dict[str, Any]] = []
        try:
            for linha in self.caminho.read_text(encoding="utf-8").splitlines():
                if linha.strip():
                    itens.append(json.loads(linha))
        except (json.JSONDecodeError, OSError):
            return []
        return itens

    def salvar(self, itens: list[dict[str, Any]]) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        linhas = [json.dumps(i, ensure_ascii=False) for i in itens]
        self.caminho.write_text("\n".join(linhas) + "\n", encoding="utf-8")

    def adicionar(self, item: dict[str, Any]) -> None:
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        with self.caminho.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(item, ensure_ascii=False) + "\n")


def _store(caminho: Path | None = None) -> ArmazenamentoAgendamentos:
    return ArmazenamentoAgendamentos(caminho or _caminho_padrao())


# ---------------------------------------------------------------------
# Parsing do instante alvo
# ---------------------------------------------------------------------
def _parsear_quando(quando: str, agora: datetime | None = None) -> datetime:
    """Converte um alvo legível em datetime tz-aware.

    Aceita: "agora", ISO datetime ("2026-08-05T09:00"), ou relativo
    ("em 5 min", "em 2 horas", "em 1 dia"). Relativos são baseados em `agora`
    (ou now UTC se omitido) para determinismo em testes.
    """
    base = agora or _agora()
    quando = (quando or "").strip().lower()

    if not quando or quando == "agora":
        return base

    rel = re.fullmatch(r"em\s+(\d+)\s*(segundo[s]?|min[s]?|minuto[s]?|h[s]?|hora[s]?|dia[s]?|dia)?", quando)
    if rel:
        valor = int(rel.group(1))
        unidade = (rel.group(2) or "min")[:1]  # s/m/h/d
        if unidade == "s":
            delta = timedelta(seconds=valor)
        elif unidade == "m":
            delta = timedelta(minutes=valor)
        elif unidade == "h":
            delta = timedelta(hours=valor)
        elif unidade == "d":
            delta = timedelta(days=valor)
        else:
            delta = timedelta(seconds=valor)
        return base + delta

    try:
        dt = datetime.fromisoformat(quando)
    except ValueError:
        raise ValueError(
                    f"Formato inválido de quando: {quando!r}. Use ISO (2026-08-05T09:00), "
                    "'agora' ou 'em N min|horas|dias'."
                ) from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_TZ_LOCAL)
    return dt


def _reagendar(item: dict[str, Any]) -> dict[str, Any]:
    """Avança `quando_iso` conforme a frequência, se for recorrente."""
    freq = item.get("frequencia") or "nenhuma"
    if freq == "nenhuma":
        return item
    try:
        dt = datetime.fromisoformat(item["quando_iso"])
    except (KeyError, ValueError):
        return item
    if freq == "horaria":
        dt += timedelta(hours=1)
    elif freq == "diaria":
        dt += timedelta(days=1)
    elif freq == "semanal":
        dt += timedelta(weeks=1)
    else:
        return item
    item["quando_iso"] = _timestamp(dt)
    return item


# ---------------------------------------------------------------------
# API de agendamento (também usada pelas ferramentas do agente)
# ---------------------------------------------------------------------

def agendar_tarefa(
    tarefa: str,
    quando: str,
    frequencia: str = "nenhuma",
    caminho: Path | None = None,
    agora: datetime | None = None,
) -> dict[str, Any]:
    """Cria um agendamento e retorna o registro (com `id`)."""
    if frequencia not in DEFAULT_FREQUENCIAS:
        raise ValueError(
            f"frequência inválida: {frequencia!r}. Use {DEFAULT_FREQUENCIAS}."
        )
    alvo = _parsear_quando(quando, agora)
    item = {
        "id": uuid.uuid4().hex[:12],
        "tarefa": tarefa,
        "quando": quando,
        "quando_iso": _timestamp(alvo),
        "frequencia": frequencia,
        "estado": "agendado",
        "criado": _timestamp(_agora()),
        "resultado": "",
        "erro": "",
    }
    _store(caminho).adicionar(item)
    return item


def listar(
    caminho: Path | None = None,
    *,
    estados: tuple[str, ...] = ("agendado", "executando"),
) -> list[dict[str, Any]]:
    """Lista agendamentos ativos (não concluídos/cancelados), por instante."""
    itens = [i for i in _store(caminho).carregar() if i.get("estado") in estados]
    itens.sort(key=lambda i: i.get("quando_iso", ""))
    return itens


def cancelar(agend_id: str, caminho: Path | None = None) -> bool:
    """Cancela um agendamento pelo id. Retorna False se não encontrar."""
    armaz = _store(caminho)
    itens = armaz.carregar()
    for i in itens:
        if i.get("id") == agend_id and i.get("estado") in ("agendado", "executando"):
            i["estado"] = "cancelado"
            armaz.salvar(itens)
            return True
    return False


def vencidos(
    agora: datetime | None = None,
    caminho: Path | None = None,
) -> list[dict[str, Any]]:
    """Agendamentos 'agendado' com instante alvo <= `agora` (determinístico)."""
    instante = agora or _agora()
    devido: list[dict[str, Any]] = []
    for i in _store(caminho).carregar():
        if i.get("estado") != "agendado":
            continue
        try:
            alvo = datetime.fromisoformat(i.get("quando_iso", ""))
        except ValueError:
            continue
        if alvo <= instante:
            devido.append(i)
    devido.sort(key=lambda i: i.get("quando_iso", ""))
    return devido


def _executar_um(agend: dict[str, Any], app) -> str:
    """Executa a tarefa no grafo e devolve a resposta final."""
    from .grafo import executar_headless
    resultado = executar_headless(app, agend["tarefa"], thread_id=f"agendamento-{agend['id']}")
    mensagens = resultado.get("mensagens") or []
    if mensagens:
        ultima = mensagens[-1]
        return str(getattr(ultima, "content", ultima))
    return "(sem resposta)"


def _notificar(webhook_url: str | None, agend: dict[str, Any]) -> None:
    """Notifica um webhook (callback) sobre a conclusão de um agendamento."""
    if not webhook_url:
        return
    try:
        requests.post(webhook_url, json={
            "evento": "agendamento",
            "id": agend.get("id"),
            "tarefa": agend.get("tarefa"),
            "estado": agend.get("estado"),
            "resultado": agend.get("resultado"),
            "erro": agend.get("erro"),
        }, timeout=10)
    except requests.RequestException:
        pass  # falha de notificação nunca derruba o loop


def executar_vencidos(
    app,
    agora: datetime | None = None,
    caminho: Path | None = None,
    webhook_url: str | None = None,
) -> list[dict[str, Any]]:
    """Executa todos os vencidos no grafo e atualiza a persistência.

    Retorna a lista de agendamentos processados (concluídos, reagendados ou
    com erro registrado).
    """
    armaz = _store(caminho)
    venc = vencidos(agora, caminho)
    if not venc:
        return []

    itens = armaz.carregar()
    by_id = {i["id"]: i for i in itens}
    processados: list[dict[str, Any]] = []

    for pendente in venc:
        pendente["estado"] = "executando"
        try:
            pendente["resultado"] = _executar_um(pendente, app)
            pendente["erro"] = ""
            if (pendente.get("frequencia") or "nenhuma") != "nenhuma":
                _reagendar(pendente)
                pendente["estado"] = "agendado"
            else:
                pendente["estado"] = "concluido"
        except Exception as exc:  # noqa: BLE001 — falha não derruba o lote
            pendente["estado"] = "falhou"
            pendente["erro"] = str(exc)
        processados.append(pendente)

    for p in processados:
        by_id[p["id"]] = p
    armaz.salvar(list(by_id.values()))

    for r in processados:
        _notificar(webhook_url, r)
    return processados


# ---------------------------------------------------------------------
# Ferramentas do agente
# ---------------------------------------------------------------------

@tool
def agendar(tarefa: str, quando: str, frequencia: str = "nenhuma") -> str:
    """Agenda uma tarefa para execução autônoma futura (cron interno).

    Args:
        tarefa: o que executar (mensagem natural para o agente).
        quando: "agora", ISO datetime ("2026-08-05T09:00") ou relativo
                ("em 5 min", "em 2 horas", "em 1 dia").
        frequencia: "nenhuma" (uma vez) | "horaria" | "diaria" | "semanal".
    """
    try:
        item = agendar_tarefa(tarefa, quando, frequencia)
    except ValueError as exc:
        return f"ERRO_FERRAMENTA: {exc}"
    return (
        f"Agendado id={item['id']} para {item['quando_iso']} "
        f"(frequência {item['frequencia']})."
    )


@tool
def listar_agendamentos() -> str:
    """Lista os agendamentos ativos do cron interno."""
    pendentes = listar()
    if not pendentes:
        return "Nenhum agendamento pendente."
    import json as _json
    return _json.dumps(
        [{"id": i["id"], "tarefa": i["tarefa"], "quando": i["quando_iso"],
          "frequencia": i["frequencia"]} for i in pendentes],
        ensure_ascii=False, indent=2,
    )


@tool
def cancelar_agendamento(id_agendamento: str) -> str:
    """Cancela um agendamento pendente pelo seu id."""
    if cancelar(id_agendamento):
        return f"Agendamento {id_agendamento} cancelado."
    return f"ERRO_FERRAMENTA: agendamento {id_agendamento} não encontrado ou já encerrado."