"""Testes do agendador interno (cron). Determinísticos — sem rede nem relógio."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from langchain_core.messages import AIMessage

import aegis.agendador as ag
from aegis.agendador import agendar_tarefa

AGORA = datetime(2026, 8, 5, 9, 0, tzinfo=timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def test_agendar_cria_registro(tmp_path):
    item = agendar_tarefa("lembrar de comprar", "2026-08-05T10:00:00+00:00",
                          caminho=tmp_path / "a.jsonl")
    assert item["estado"] == "agendado"
    assert item["frequencia"] == "nenhuma"
    assert item["id"]
    # persiste no arquivo
    assert len(ag._store(tmp_path / "a.jsonl").carregar()) == 1


def test_vencidos_deterministico(tmp_path):
    c = tmp_path / "a.jsonl"
    agendar_tarefa("futuro", "2026-08-05T11:00:00+00:00", caminho=c)
    agendar_tarefa("vencido", "2026-08-05T08:00:00+00:00", caminho=c)

    assert ag.vencidos(AGORA - timedelta(hours=2), caminho=c) == []  # antes de tudo
    venc = ag.vencidos(AGORA, caminho=c)
    assert [i["tarefa"] for i in venc] == ["vencido"]


def test_cancelar(tmp_path):
    c = tmp_path / "a.jsonl"
    item = agendar_tarefa("x", "2026-08-05T08:00:00+00:00", caminho=c)
    assert ag.cancelar(item["id"], c) is True
    assert ag.cancelar(item["id"], c) is False
    assert ag.vencidos(AGORA + timedelta(hours=2), c) == []
    assert ag.listar(c) == []  # cancelado sai do "ativo"


def test_quando_relativo(tmp_path):
    base = AGORA
    r = ag._parsear_quando("em 5 min", base)
    assert r == base + timedelta(minutes=5)
    r = ag._parsear_quando("agora", base)
    assert r == base


class AppStub:
    """Substituto do grafo via `executar_headless` (contrato `.invoke()`)."""

    falhar = False

    def __init__(self, falhar: bool = False) -> None:
        self.falhar = falhar

    def invoke(self, entrada: dict, config: dict | None = None) -> dict:
        if self.falhar:
            raise RuntimeError("grace falhou")
        return {"mensagens": [AIMessage(content="resultado do cron")]}


def test_executar_vencidos_conclui(tmp_path):
    c = tmp_path / "a.jsonl"
    item = agendar_tarefa("rodar relatorio", "2026-08-05T08:00:00+00:00", caminho=c)
    processados = ag.executar_vencidos(AppStub(), AGORA, caminho=c)
    assert processados[0]["estado"] == "concluido"
    assert processados[0]["resultado"] == "resultado do cron"

    gravado = ag._store(c).carregar()
    assert gravado[0]["estado"] == "concluido"
    assert len(ag.vencidos(AGORA, c)) == 0


def test_recorrente_reagenda(tmp_path):
    c = tmp_path / "a.jsonl"
    item = agendar_tarefa("diaria", "2026-08-05T08:00:00+00:00", frequencia="diaria", caminho=c)
    original = item["quando_iso"]

    processados = ag.executar_vencidos(AppStub(), AGORA, caminho=c)
    p = processados[0]
    assert p["estado"] == "agendado"  # reagendado, não concluído
    assert ag._store(c).carregar()[0]["quando_iso"] != original
    data_nova = datetime.fromisoformat(ag._store(c).carregar()[0]["quando_iso"])
    assert data_nova == datetime.fromisoformat(original) + timedelta(days=1)  # +1 dia


def test_erro_nao_derruba_lote(tmp_path):
    c = tmp_path / "a.jsonl"
    agendar_tarefa("vai falhar", "2026-08-05T08:00:00+00:00", caminho=c)
    agendar_tarefa("ok", "2026-08-05T08:00:00+00:00", caminho=c)

    processados = ag.executar_vencidos(AppStub(falhar=True), AGORA, caminho=c)
    assert all(p["estado"] == "falhou" for p in processados)
    gravados = ag._store(c).carregar()
    assert all(g["estado"] == "falhou" for g in gravados)
    assert all(g["erro"] for g in gravados)


def test_notificacao_webhook(tmp_path, monkeypatch):
    chamadas: list[dict] = []

    import aegis.agendador as mod
    def _post(url, json=None, timeout=None):  # noqa: ANN001
        chamadas.append(json)
        class R:  # noqa: D106
            status_code = 200
        return R()

    monkeypatch.setattr(mod.requests, "post", _post)
    c = tmp_path / "a.jsonl"
    agendar_tarefa("notificar", "2026-08-05T08:00:00+00:00", caminho=c)
    ag.executar_vencidos(AppStub(), AGORA, caminho=c, webhook_url="http://hook/x")

    assert len(chamadas) == 1
    assert chamadas[0]["evento"] == "agendamento"
    assert chamadas[0]["estado"] == "concluido"


def test_ferramentas_registradas():
    from aegis.ferramentas import carregar_ferramentas
    nomes = {t.name for t in carregar_ferramentas()}
    assert {"agendar", "listar_agendamentos", "cancelar_agendamento"} <= nomes