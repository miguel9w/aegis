"""
Exportador de trajetórias → datasets de treinamento (ShareGPT / OpenAI).

Lê os JSONL de auditoria em `trajetorias/` (gravados por `aegis.trajetoria`)
e transforma em formatos prontos para pipelines de fine-tuning / RLHF:

  - **ShareGPT**: `{"conversations": [{"from": "human"|"gpt", "value": ...}, ...]}`
  - **OpenAI** (ChatML / SFT):  `{"messages": [{"role": "user"|"assistant", ...}]}`

As chamadas de ferramenta são mescladas na mensagem do assistente como notas
(🔧 nome(args) → saída), preservando a intenção de auditoria no dataset.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------

def carregar_registros(diretorio: str | Path) -> list[dict[str, Any]]:
    """Carrega e mescla todos os `*.jsonl` do diretório, ordenados por `ts`."""
    base = Path(diretorio)
    registros: list[dict[str, Any]] = []
    if not base.is_dir():
        return registros
    for arquivo in sorted(base.glob("*.jsonl")):
        try:
            for linha in arquivo.read_text(encoding="utf-8").splitlines():
                linha = linha.strip()
                if not linha:
                    continue
                try:
                    registros.append(json.loads(linha))
                except json.JSONDecodeError:
                    continue
        except OSError:
            continue
    registros.sort(key=lambda r: r.get("ts", ""))
    return registros


def agrupar_por_thread(registros: list[dict]) -> dict[str, list[dict]]:
    """Agrupa os registros por `thread_id`, preservando a ordem temporal."""
    grupos: dict[str, list[dict]] = {}
    for r in registros:
        grupos.setdefault(r.get("thread_id", "?"), []).append(r)
    return grupos


# ---------------------------------------------------------------------
# Conversão para mensagens
# ---------------------------------------------------------------------

def _converter_para_mensagens(registros: list[dict]) -> list[dict[str, str]]:
    """Converte registros de uma thread em pares {role, content} (OpenAI/ChatML)."""
    mensagens: list[dict[str, str]] = []
    notas_ferramenta: list[str] = []
    pendente: dict[str, Any] = {}  # nome/args do início aguardando o fim

    for r in registros:
        tipo = r.get("tipo")
        dados = r.get("dados") or {}

        if tipo == "mensagem_usuario":
            _anexar_notas(mensagens, notas_ferramenta)
            mensagens.append({"role": "user", "content": str(dados.get("conteudo", ""))})

        elif tipo == "mensagem_agente":
            conteudo = str(dados.get("conteudo", ""))
            notas = "\n".join(notas_ferramenta)
            mensagens.append(
                {"role": "assistant", "content": f"{conteudo}\n\n{notas}".strip()}
            )
            notas_ferramenta.clear()

        elif tipo == "ferramenta_inicio":
            pendente = {
                "nome": dados.get("nome", "?"),
                "args": dados.get("args", {}),
            }

        elif tipo == "ferramenta_fim":
            nome = pendente.pop("nome", "?")
            args = json.dumps(pendente.pop("args", {}), ensure_ascii=False, default=str)
            saida = str(dados.get("saida", ""))
            notas_ferramenta.append(f"🔧 {nome}({args}) → {saida}")

    _anexar_notas(mensagens, notas_ferramenta)
    return mensagens


def _anexar_notas(mensagens: list[dict[str, str]], notas: list[str]) -> None:
    """Anexa notas de ferramenta pendentes à última mensagem do assistente."""
    if notas and mensagens and mensagens[-1]["role"] == "assistant":
        mensagens[-1]["content"] = f"{mensagens[-1]['content']}\n\n" + "\n".join(notas)
        notas.clear()


def _para_sharegpt(mensagens: list[dict[str, str]]) -> list[dict]:
    """Converte pares {role, content} para o formato ShareGPT {from, value}."""
    return [
        {"from": "human" if m["role"] == "user" else "gpt", "value": m["content"]}
        for m in mensagens
    ]


# ---------------------------------------------------------------------
# Exportação
# ---------------------------------------------------------------------

def exportar_sharegpt(diretorio: str | Path, saida: str | Path | None = None) -> dict:
    """
    Exporta todas as trajetórias como um arquivo JSON no formato ShareGPT.

    Retorna resumo: {arquivo, conversas, threads, pular bobagens?}.
    """
    registros = carregar_registros(diretorio)
    grupos = agrupar_por_thread(registros)
    conversas: list[dict] = []
    for _thread, regs in sorted(grupos.items()):
        mensagens = _converter_para_mensagens(regs)
        sharegpt = _para_sharegpt(mensagens)
        if any(m["from"] == "human" for m in sharegpt) and any(
            m["from"] == "gpt" for m in sharegpt
        ):
            conversas.append({"conversations": sharegpt})

    destino = _destino(saida, "sharegpt_", ".json")
    destino.write_text(
        json.dumps(conversas, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"arquivo": str(destino), "conversas": len(conversas), "threads": len(grupos)}


def exportar_openai(diretorio: str | Path, saida: str | Path | None = None) -> dict:
    """
    Exporta as trajetórias como JSONL no formato OpenAI (SFT/RLHF).

    Uma linha por conversa: {"messages": [...]}. Padrão usado em `openai
    fine_tuning` e pipelines RLHF.
    """
    registros = carregar_registros(diretorio)
    grupos = agrupar_por_thread(registros)
    linhas: list[str] = []
    for _thread, regs in sorted(grupos.items()):
        mensagens = _converter_para_mensagens(regs)
        if any(m["role"] == "user" for m in mensagens) and any(
            m["role"] == "assistant" for m in mensagens
        ):
            linhas.append(json.dumps({"messages": mensagens}, ensure_ascii=False))

    destino = _destino(saida, "openai_", ".jsonl")
    destino.write_text("\n".join(linhas) + ("\n" if linhas else ""), encoding="utf-8")
    return {"arquivo": str(destino), "conversas": len(linhas), "threads": len(grupos)}


def _destino(saida: str | Path | None, prefixo: str, sufixo: str) -> Path:
    """Define o caminho de saída padrão: `config/dados/datasets/<prefixo><sufixo>`."""
    if saida is not None:
        return Path(saida)
    data = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    base = Path(__file__).resolve().parent.parent / "config" / "dados" / "datasets"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{prefixo}{data}{sufixo}"