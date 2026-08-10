"""Autorizações de comandos na sessão (janela de perguntas da web UI).

Quando o agente pede `confirmar=True` para executar um comando, a política
de segurança do `executar_comando` RECUSA por padrão e a web UI abre a
janela de perguntas. Se o usuário aprovar, o comando exato fica aprovado
em memória (válido até o processo reiniciar) e passa a executar sem
confirmação.

IMPORTANTE: a aprovação NÃO contorna a denylist — comandos destrutivos
(recusados pela política) continuam recusados SEMPRE; a aprovação só vale
para o ramo de "exige confirmar=True".
"""

import threading

_aprovados: set[str] = set()
_lock = threading.Lock()


def aprovar_comando(comando: str) -> bool:
    """Registra o comando exato como aprovado na sessão."""
    if not comando.strip():
        return False
    with _lock:
        _aprovados.add(comando)
    return True


def comando_aprovado(comando: str) -> bool:
    with _lock:
        return comando in _aprovados


def aprovados() -> list[str]:
    with _lock:
        return sorted(_aprovados)


def limpar() -> None:
    with _lock:
        _aprovados.clear()
