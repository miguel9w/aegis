"""
Trajetória de auditoria (Trajectory Logging) — exporta decisões do agente
em formato JSONL (pronto para datasets ShareGPT / treinamento RL).

Registra: início/fim de ferramentas, transições de nós e chamadas ao modelo.
Escrita incremental e à prova de interrupção (append + flush).
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class Trajetoria:
    """Registrador de trajetórias em JSONL, por dia de execução."""

    def __init__(self, diretorio: str | Path) -> None:
        self.dir = Path(diretorio)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _caminho(self) -> Path:
        hoje = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.dir / f"trajetoria_{hoje}.jsonl"

    def registrar(self, thread_id: str, tipo: str, dados: dict[str, Any]) -> None:
        """Grava um registro JSONL com timestamp e thread de origem."""
        linha = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "thread_id": thread_id,
            "tipo": tipo,
            "dados": dados,
        }
        with self._caminho().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(linha, ensure_ascii=False, default=str) + "\n")
            fh.flush()

    # -----------------------------------------------------------------
    # Hook para consumo direto de eventos (astream_events)
    # -----------------------------------------------------------------

    def hook(self, thread_id: str):
        """Retorna um callable pronto para receber cada evento do stream."""
        def _hook(evento: dict) -> None:
            kind = evento.get("event", "")
            meta = evento.get("metadata") or (evento.get("data") or {}).get("metadata") or {}
            try:
                if kind == "on_tool_start":
                    dados = evento.get("data", {})
                    self.registrar(thread_id, "ferramenta_inicio", {
                        "nome": evento.get("name", "?"),   # v2: nome no top-level
                        "args": dados.get("input") or {},
                    })
                elif kind == "on_tool_end":
                    dados = evento.get("data", {})
                    saida_obj = dados.get("output")
                    self.registrar(thread_id, "ferramenta_fim", {
                        "saida": str(getattr(saida_obj, "content", saida_obj))[:500],
                    })
                elif kind == "on_chain_start":
                    no = meta.get("langgraph_node")
                    if no:
                        self.registrar(thread_id, "no", {"acao": "inicio", "no": no})
                elif kind == "on_chain_end":
                    no = meta.get("langgraph_node")
                    if no:
                        self.registrar(thread_id, "no", {"acao": "fim", "no": no})
            except Exception:  # noqa: BLE001 — logging nunca quebra a execução
                pass
        return _hook