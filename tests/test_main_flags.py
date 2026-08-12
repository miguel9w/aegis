"""Flags do main.py: execução única (pergunta) ganha thread própria.

Uma pergunta passada na linha de comando é uma execução ÚNICA — não deve
acumular histórico na thread default (foi o que fez a sanidade contaminar
o run seguinte e disparar o 400 do zen). Sem `--thread`/`--novo-thread`
explícitos, o one-shot agora cria uma thread UUID própria.
"""

from __future__ import annotations

import uuid

import main as main_mod
from aegis.config import config


def _args(*av: str):
    return main_mod.novo_argumentos().parse_args(list(av))


def _restaurar_thread(monkeypatch):
    original = config.thread_id
    monkeypatch.setattr(config, "thread_id", original)


def test_one_shot_sem_flag_ganha_thread_nova(monkeypatch):
    _restaurar_thread(monkeypatch)
    main_mod._aplicar_flags(_args("--headless", "pergunta qualquer"))
    novo = config.thread_id
    assert novo != "smoke"
    assert len(novo) == 12 and int(novo, 16) >= 0  # UUID hex[:12]

    # cada execução é uma conversa ISOLADA (novo UUID por vez)
    main_mod._aplicar_flags(_args("--headless", "outra pergunta"))
    assert config.thread_id != novo


def test_one_shot_com_thread_explicito_respeita(monkeypatch):
    _restaurar_thread(monkeypatch)
    main_mod._aplicar_flags(_args("--thread", "meu_topico", "pergunta"))
    assert config.thread_id == "meu_topico"


def test_one_shot_com_novo_thread_prioritario(monkeypatch):
    _restaurar_thread(monkeypatch)
    main_mod._aplicar_flags(_args("--novo-thread", "--thread", "x", "pergunta"))
    assert len(config.thread_id) == 12 and config.thread_id != "x"


def test_tui_sem_pergunta_preserva_thread_default(monkeypatch):
    _restaurar_thread(monkeypatch)
    antes = config.thread_id
    main_mod._aplicar_flags(_args())
    assert config.thread_id == antes  # conversa contínua na TUI
