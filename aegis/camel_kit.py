"""
Toolkits estilo CAMEL/newai para o Aegis.

Três mini-toolkits acionáveis pelo agente:
  - **thinking** (`pensar`, `ver_pensamento`): cadeia de raciocínio
    registrada em `config/dados/pensamento_atual.json`;
  - **task-planning** (`planejar_tarefa`, `atualizar_plano`, `ver_plano`):
    plano com passos e status em `config/dados/plano_tarefas.json`;
  - **note-taking** (`anotar`, `ver_notas`): bloco de notas em
    `config/dados/notas.json`.

Tudo determinístico e persistido em JSON — sem LLM no caminho crítico.
"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from .config import config

_ESTADOS_PLANO = ("pendente", "executando", "ok", "cancelado")
_SIMBOLOS_PLANO = {"pendente": "⬜", "executando": "⏳", "ok": "✅", "cancelado": "⬛"}


def _agora() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _caminho(attr: str, caminho: str | Path | None) -> Path:
    if caminho is not None:
        return Path(caminho)
    return Path(getattr(config, attr))


def _carregar(attr: str, padrao: Any, caminho: str | Path | None = None) -> Any:
    alvo = _caminho(attr, caminho)
    try:
        if not alvo.is_file():
            return padrao
        with alvo.open(encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001 — estado auxiliar nunca quebra
        return padrao


def _salvar(attr: str, dados: Any, caminho: str | Path | None = None) -> None:
    alvo = _caminho(attr, caminho)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# Thinking (cadeia de raciocínio)
# --------------------------------------------------------------------------

@tool
def pensar(passo_raciocinio: str) -> str:
    """Registra um passo de raciocínio e devolve a cadeia completa numerada."""
    if not str(passo_raciocinio or "").strip():
        raise ValueError("passo_raciocinio é obrigatório")
    dados = _carregar("pensamento_path", {"passos": []})
    passos = dados.get("passos") if isinstance(dados, dict) else []
    if not isinstance(passos, list):
        passos = []
    passos.append({
        "n": len(passos) + 1,
        "passo": str(passo_raciocinio).strip(),
        "ts": _agora(),
    })
    _salvar("pensamento_path", {"passos": passos})
    return "Cadeia de raciocínio:\n" + "\n".join(f"{p['n']}. {p['passo']}" for p in passos)


@tool
def ver_pensamento() -> str:
    """Mostra a cadeia de raciocínio registrada até agora."""
    dados = _carregar("pensamento_path", {"passos": []})
    passos = dados.get("passos") if isinstance(dados, dict) else []
    if not passos:
        return "(nenhum passo de raciocínio registrado)"
    return "💡 Cadeia de raciocínio:\n" + "\n".join(f"{p['n']}. {p['passo']}" for p in passos)


# --------------------------------------------------------------------------
# Task-planning (plano com passos e status)
# --------------------------------------------------------------------------

def _parsear_passos(texto: str) -> list[str]:
    """Converte '1. x\n2. y' ou '- x\n- y' numa lista de passos limpos."""
    passos: list[str] = []
    for linha in (texto or "").replace(";", "\n").splitlines():
        item = linha.strip()
        if not item:
            continue
        for prefixo in ("1. ", "2. ", "3. ", "4. ", "5. ", "6. ", "7. ", "8. ", "9. ", "0. ",
                        "- ", "* ", "• "):
            if item.startswith(prefixo):
                item = item[len(prefixo):].strip()
                break
        if item:
            passos.append(item)
    return passos


@tool
def planejar_tarefa(objetivo: str, passos: str) -> str:
    """Cria (ou sobrescreve o plano atual) um plano de tarefas em passos (um por linha, '- x' ou '1. x')."""
    if not str(objetivo or "").strip():
        raise ValueError("objetivo é obrigatório")
    itens = _parsear_passos(passos)
    if not itens:
        raise ValueError("forneça ao menos um passo (um por linha)")
    plano = {
        "id": uuid.uuid4().hex[:8],
        "objetivo": str(objetivo).strip(),
        "passos": [
            {"id": f"p{i+1}", "passo": passo, "status": "pendente"}
            for i, passo in enumerate(itens)
        ],
        "criado_em": _agora(),
    }
    _salvar("plano_tarefas_path", plano)
    return _formatar_plano(plano)


@tool
def atualizar_plano(id: str, novo_status: str) -> str:
    """Atualiza o status de um passo do plano (pendente|executando|ok|cancelado)."""
    status = str(novo_status or "").strip().lower()
    if status not in _ESTADOS_PLANO:
        raise ValueError(f"status inválido: {novo_status}. Válidos: {', '.join(_ESTADOS_PLANO)}")
    plano = _carregar("plano_tarefas_path", None)
    if not isinstance(plano, dict) or not plano.get("passos"):
        raise ValueError("nenhum plano ativo — use planejar_tarefa primeiro")
    for passo in plano["passos"]:
        if passo.get("id") == str(id).strip():
            passo["status"] = status
            _salvar("plano_tarefas_path", plano)
            return _formatar_plano(plano)
    raise ValueError(f"passo '{id}' não encontrado no plano")


@tool
def ver_plano() -> str:
    """Mostra o plano de tarefas atual com o progresso."""
    plano = _carregar("plano_tarefas_path", None)
    if not isinstance(plano, dict) or not plano.get("passos"):
        return "(nenhum plano de tarefas ativo)"
    return _formatar_plano(plano)


def _formatar_plano(plano: dict[str, Any]) -> str:
    """Formata um plano (passos + progresso) — função pura p/ reuso interno."""
    linhas = [f"📋 Plano: {plano['objetivo']}"]
    feitos = 0
    for passo in plano["passos"]:
        status = passo.get("status", "pendente")
        if status == "ok":
            feitos += 1
        simbolo = _SIMBOLOS_PLANO.get(status, "⬜")
        linhas.append(f"  {simbolo} [{passo.get('id')}] {passo.get('passo')} — {status}")
    linhas.append(f"Progresso: {feitos}/{len(plano['passos'])} concluídos")
    return "\n".join(linhas)


# --------------------------------------------------------------------------
# Note-taking (notas rápidas)
# --------------------------------------------------------------------------

@tool
def anotar(nota: str) -> str:
    """Registra uma nota rápida no bloco de notas (histórico anexado)."""
    if not str(nota or "").strip():
        raise ValueError("nota é obrigatório")
    notas = _carregar("notas_path", {"notas": []})
    lista = notas.get("notas") if isinstance(notas, dict) else []
    if not isinstance(lista, list):
        lista = []
    lista.append({"ts": _agora(), "nota": str(nota).strip()})
    _salvar("notas_path", {"notas": lista})
    return f"📝 Nota registrada ({len(lista)}ª): {nota[:200]}"


@tool
def ver_notas(qtd: int = 5) -> str:
    """Lista as últimas N notas registradas."""
    notas = _carregar("notas_path", {"notas": []})
    lista = notas.get("notas") if isinstance(notas, dict) else []
    if not isinstance(lista, list) or not lista:
        return "(nenhuma nota registrada)"
    qtd = max(1, int(qtd or 1))
    linhas = [f"📝 Últimas {min(qtd, len(lista))} notas:"]
    for item in lista[-qtd:]:
        linhas.append(f"- {item.get('ts', '?')}: {item.get('nota', '')}")
    return "\n".join(linhas)