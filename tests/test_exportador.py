"""Testes do exportador de trajetórias → datasets ShareGPT / OpenAI (RL)."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from aegis.exportador import (
    agrupar_por_thread,
    carregar_registros,
    exportar_openai,
    exportar_sharegpt,
)


def _linha(thread: str, tipo: str, dados: dict, ts: str = "") -> str:
    ts = ts or datetime.now(timezone.utc).isoformat()
    return json.dumps({"ts": ts, "thread_id": thread, "tipo": tipo, "dados": dados})


def _criar_trajetorias(tmp_path) -> None:
    """Duas threads com tool pat + conversa (como grava o hook + main/TUI)."""
    registros = [
        _linha("t1", "mensagem_usuario", {"conteudo": "quanto é 8 * 8?"}),
        _linha("t1", "ferramenta_inicio", {"nome": "calculadora", "args": {"expressao": "8*8"}}),
        _linha("t1", "ferramenta_fim", {"saida": "8 * 8 = 64"}),
        _linha("t1", "mensagem_agente", {"conteudo": "O resultado é 64."}),
        _linha("t2", "mensagem_usuario", {"conteudo": "olá!"}),
        _linha("t2", "mensagem_agente", {"conteudo": "olá! como posso ajudar?"}),
    ]
    arquivo = tmp_path / "trajetoria_2026-01-01.jsonl"
    arquivo.write_text("\n".join(registros) + "\n", encoding="utf-8")


def test_carregar_e_agrupar(tmp_path):
    _criar_trajetorias(tmp_path)
    registros = carregar_registros(tmp_path)
    assert len(registros) == 6
    grupos = agrupar_por_thread(registros)
    assert set(grupos) == {"t1", "t2"}


def test_exportar_sharegpt(tmp_path):
    _criar_trajetorias(tmp_path)
    saida = tmp_path / "sharegpt.json"
    resumo = exportar_sharegpt(tmp_path, saida=saida)
    assert resumo["threads"] == 2
    assert resumo["conversas"] == 2

    dados = json.loads(saida.read_text(encoding="utf-8"))
    conv = dados[0]["conversations"]
    origens = [m["from"] for m in conv]
    assert origens[0] == "human" and "gpt" in origens
    # ferramenta mesclada na mensagem do assistente
    valor_gpt = conv[next(i for i, m in enumerate(conv) if m["from"] == "gpt")]["value"]
    assert "calculadora" in valor_gpt
    assert "8 * 8 = 64" in valor_gpt


def test_exportar_openai(tmp_path):
    _criar_trajetorias(tmp_path)
    saida = tmp_path / "openai.jsonl"
    resumo = exportar_openai(tmp_path, saida=saida)
    assert resumo["conversas"] == 2

    linhas = saida.read_text(encoding="utf-8").strip().splitlines()
    primeiro = json.loads(linhas[0])
    roles = [m["role"] for m in primeiro["messages"]]
    assert "user" in roles and "assistant" in roles
    assert primeiro["messages"][0]["content"] == "quanto é 8 * 8?"


def test_exportar_sem_trajetorias(tmp_path):
    vazio = tmp_path / "vazio"
    vazio.mkdir()
    resumo = exportar_sharegpt(vazio, saida=tmp_path / "x.json")
    assert resumo["conversas"] == 0
    assert json.loads((tmp_path / "x.json").read_text(encoding="utf-8")) == []