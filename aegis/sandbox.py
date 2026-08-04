"""
Abstração de ambiente de execução (sandbox).

Fornece uma interface unificada para executar comandos de forma isolada,
com implementações plugáveis:
  - `ExecutorLocal`  : subprocess com timeout, cwd e captura de saída (funcional)
  - `ExecutorDocker` : stub documentado (isola em container) — TODO
  - `ExecutorSSH`    : stub documentado (host remoto via SSH)    — TODO

O padrão permite alternar o executor via `.env` (`AEGIS_EXECUTOR`)
sem alterar o grafo.
"""

from __future__ import annotations

import shlex
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ResultadoExecucao:
    """Resultado de uma execução de comando."""
    saida: str = ""
    codigo: int = 0
    duracao: float = 0.0
    erro: str | None = None
    comando: str = ""

    @property
    def sucesso(self) -> bool:
        return self.erro is None and self.codigo == 0

    def resumo(self, limite: int = 2000) -> str:
        base = f"código={self.codigo} duração={self.duracao:.2f}s"
        corpo = self.saida if self.sucesso else (self.erro or "")
        if len(corpo) > limite:
            corpo = corpo[:limite] + f"\n… ({len(corpo)} caracteres, truncado)"
        return f"{base}\n{corpo}"


class Executor(ABC):
    """Interface base de sandbox."""

    nome = "base"

    @abstractmethod
    def executar(self, comando: str, *, timeout: int = 30, cwd: str | None = None) -> ResultadoExecucao:
        """Executa `comando` e devolve o resultado. Nunca deve lançar na operação."""
        raise NotImplementedError


class ExecutorLocal(Executor):
    """Executa comandos como subprocess local, com timeout e captura."""

    nome = "local"

    def __init__(self, sandbox_dir: str | None = None) -> None:
        # Diretório de trabalho padrão (sandbox isolado), criado se necessário
        self.sandbox_dir = Path(sandbox_dir or "~/.aegis_sandbox").expanduser()
        self.sandbox_dir.mkdir(parents=True, exist_ok=True)

    def executar(self, comando: str, *, timeout: int = 30,
                 cwd: str | None = None) -> ResultadoExecucao:
        inicio = time.monotonic()
        alvo = cwd or str(self.sandbox_dir)
        try:
            proc = subprocess.run(
                shlex.split(comando),
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=alvo,
                env=None,  # herda ambiente da sessão
            )
            saida = proc.stdout
            if proc.stderr:
                saida = f"{saida}\n[stderr]\n{proc.stderr}".strip()
            return ResultadoExecucao(
                saida=saida.strip(),
                codigo=proc.returncode,
                duracao=time.monotonic() - inicio,
                comando=comando,
            )
        except subprocess.TimeoutExpired:
            return ResultadoExecucao(
                comando=comando,
                duracao=time.monotonic() - inicio,
                erro=f"tempo esgotado após {timeout}s",
            )
        except Exception as exc:  # noqa: BLE001 — erro nunca derruba a ferramenta
            return ResultadoExecucao(
                comando=comando,
                duracao=time.monotonic() - inicio,
                erro=f"falha ao executar comando: {exc}",
            )


class ExecutorDocker(Executor):
    """
    Stub de sandbox via Docker (isola a execução em um container).

    TODO — para habilitar: preencher `executar` com `docker run --rm -i`
    (ex.: `docker run --rm -i --network=none alpine sh -c <comando>`).
    Mantido como interface para demonstração da extensibilidade.
    """

    nome = "docker"

    def __init__(self, imagem: str = "alpine:latest") -> None:
        self.imagem = imagem

    def executar(self, comando: str, *, timeout: int = 30,
                 cwd: str | None = None) -> ResultadoExecucao:
        raise NotImplementedError(
            "ExecutorDocker ainda não implementado — veja o docstring para o padrão sugerido."
        )


class ExecutorSSH(Executor):
    """
    Stub de sandbox via SSH (host remoto).

    TODO — para habilitar: usar `paramiko`/`ssh` para executar comandos
    em um host remoto com chave. Mantido como interface demonstrativa.
    """

    nome = "ssh"

    def __init__(self, host: str = "") -> None:
        self.host = host

    def executar(self, comando: str, *, timeout: int = 30,
                 cwd: str | None = None) -> ResultadoExecucao:
        raise NotImplementedError(
            "ExecutorSSH ainda não implementado — veja o docstring para o padrão sugerido."
        )


def criar_executor(nome: str = "local") -> Executor:
    """Fábrica de executors — troca de backend sem tocar no grafo."""
    nome = (nome or "local").lower()
    fabricas = {
        "local": lambda: ExecutorLocal(),
        "docker": lambda: ExecutorDocker(),
        "ssh": lambda: ExecutorSSH(),
    }
    if nome not in fabricas:
        raise ValueError(f"Executor desconhecido: {nome!r}. Opções: {list(fabricas)}")
    return fabricas[nome]()