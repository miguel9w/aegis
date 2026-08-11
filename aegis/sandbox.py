"""
Abstração de ambiente de execução (sandbox).

Fornece uma interface unificada para executar comandos de forma isolada,
com implementações plugáveis:
  - `ExecutorLocal`  : subprocess com timeout, cwd e captura de saída (funcional)
  - `ExecutorDocker` : container efêmero (`docker run --rm`), rede isolada,
                       denylist de comandos perigosos e volume de artefatos
  - `ExecutorSSH`    : host remoto via `ssh -o BatchMode=yes`, allowlist própria

O padrão permite alternar o executor via `.env` (`AEGIS_SANDBOX_BACKEND`)
sem alterar o grafo. Nenhum backend recebe o ambiente do host (docker não
passa `-e`/`--env-file`; ssh não envia variáveis) — o `.env` nunca vaza.
"""

from __future__ import annotations

import re
import shlex
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class ResultadoExecucao:
    """Resultado de uma execução de comando."""
    saida: str = ""
    codigo: int = 0
    duracao: float = 0.0
    erro: str | None = None
    comando: str = ""
    backend: str = "local"

    @property
    def sucesso(self) -> bool:
        return self.erro is None and self.codigo == 0

    def resumo(self, limite: int = 2000) -> str:
        base = f"backend={self.backend} código={self.codigo} duração={self.duracao:.2f}s"
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


_PADROES_DENYLIST_DOCKER: tuple[tuple[str, str], ...] = (
    (r"\bdocker\b", "docker-in-docker"),
    (r"\bpodman\b", "podman-in-docker"),
    (r"\bnerdctl\b", "nerdctl-in-docker"),
    (r"--privileged", "container privilegiado"),
    (r"--pid=host", "acesso ao namespace de processos do host"),
    (r":\s*\(\s*\)\s*\{", "bomba fork"),
)


def motivo_denylist(comando: str) -> str | None:
    """Primeiro padrão proibido encontrado no comando, ou None."""
    for padrao, nome in _PADROES_DENYLIST_DOCKER:
        if re.search(padrao, comando):
            return nome
    return None


class ExecutorDocker(Executor):
    """Sandbox via container efêmero (`docker run --rm`).

    Rede isolada por padrão (`--network=none`), volume dos artefatos em
    `/artefatos` (cwd do container), denylist de comandos perigosos e
    timeout rígido. O ambiente do host NÃO é passado (sem `-e`/`--env-file`).
    """

    nome = "docker"
    MONTE_ARTEFATOS = "/artefatos"

    def __init__(self, imagem: str = "alpine:latest",
                 artefatos_dir: str | None = None,
                 rede: str = "none") -> None:
        self.imagem = imagem
        self.artefatos = str(Path(artefatos_dir or "~/.aegis_sandbox").expanduser())
        self.rede = rede

    def executar(self, comando: str, *, timeout: int = 30,
                 cwd: str | None = None) -> ResultadoExecucao:
        inicio = time.monotonic()
        bloq = motivo_denylist(comando)
        if bloq:
            return ResultadoExecucao(
                comando=comando, duracao=time.monotonic() - inicio,
                erro=f"comando bloqueado pela denylist docker: {bloq}",
                backend=self.nome,
            )
        try:
            proc = subprocess.run(
                ["docker", "run", "--rm", "-i",
                 f"--network={self.rede}",
                 "-v", f"{self.artefatos}:{self.MONTE_ARTEFATOS}",
                 "-w", self.MONTE_ARTEFATOS,
                 self.imagem, "sh", "-c", comando],
                capture_output=True, text=True, timeout=timeout,
            )
            saida = proc.stdout
            if proc.stderr:
                saida = f"{saida}\n[stderr]\n{proc.stderr}".strip()
            return ResultadoExecucao(
                saida=saida.strip(), codigo=proc.returncode,
                duracao=time.monotonic() - inicio, comando=comando,
                backend=self.nome,
            )
        except subprocess.TimeoutExpired:
            return ResultadoExecucao(
                comando=comando, duracao=time.monotonic() - inicio,
                erro=f"tempo esgotado após {timeout}s", backend=self.nome,
            )
        except FileNotFoundError:
            return ResultadoExecucao(
                comando=comando, duracao=time.monotonic() - inicio,
                erro="docker não instalado ou fora do PATH", backend=self.nome,
            )
        except Exception as exc:  # noqa: BLE001 — erro nunca derruba a ferramenta
            return ResultadoExecucao(
                comando=comando, duracao=time.monotonic() - inicio,
                erro=f"falha ao executar comando no docker: {exc}", backend=self.nome,
            )


class ExecutorSSH(Executor):
    """Sandbox via host remoto (`ssh -o BatchMode=yes`, sem senha interativa).

    Host/usuário vêm do `.env` (`AEGIS_SSH_HOST`/`AEGIS_SSH_USER`) — nunca
    do repositório. Allowlist própria: apenas comandos que começam com um
    dos prefixos permitidos (`AEGIS_SSH_ALLOWLIST`).
    """

    nome = "ssh"

    def __init__(self, host: str = "", usuario: str = "",
                 allowlist: tuple[str, ...] = ()) -> None:
        self.alvo = f"{usuario}@{host}" if usuario else host
        self.allowlist = tuple(a.lower() for a in (allowlist or ()))

    def executar(self, comando: str, *, timeout: int = 30,
                 cwd: str | None = None) -> ResultadoExecucao:
        inicio = time.monotonic()
        if self.allowlist and not any(
            comando.strip().lower().startswith(a) for a in self.allowlist
        ):
            return ResultadoExecucao(
                comando=comando, duracao=time.monotonic() - inicio,
                erro=("comando fora da allowlist ssh — permitidos apenas prefixos: "
                      f"{', '.join(self.allowlist)}"),
                backend=self.nome,
            )
        if not self.alvo:
            return ResultadoExecucao(
                comando=comando, duracao=time.monotonic() - inicio,
                erro="ssh sem destino (AEGIS_SSH_HOST/AEGIS_SSH_USER ausentes no .env)",
                backend=self.nome,
            )
        try:
            proc = subprocess.run(
                ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                 self.alvo, comando],
                capture_output=True, text=True, timeout=timeout,
            )
            saida = proc.stdout
            if proc.stderr:
                saida = f"{saida}\n[stderr]\n{proc.stderr}".strip()
            return ResultadoExecucao(
                saida=saida.strip(), codigo=proc.returncode,
                duracao=time.monotonic() - inicio, comando=comando,
                backend=self.nome,
            )
        except subprocess.TimeoutExpired:
            return ResultadoExecucao(
                comando=comando, duracao=time.monotonic() - inicio,
                erro=f"tempo esgotado após {timeout}s", backend=self.nome,
            )
        except FileNotFoundError:
            return ResultadoExecucao(
                comando=comando, duracao=time.monotonic() - inicio,
                erro="ssh não instalado ou fora do PATH", backend=self.nome,
            )
        except Exception as exc:  # noqa: BLE001 — erro nunca derruba a ferramenta
            return ResultadoExecucao(
                comando=comando, duracao=time.monotonic() - inicio,
                erro=f"falha ao executar comando via ssh: {exc}", backend=self.nome,
            )


def criar_executor(nome: str = "local", *, cfg: Any = None) -> Executor:
    """Fábrica de executors — troca de backend sem tocar no grafo.

    `cfg` (opcional, `aegis.config.Config`) fornece imagem docker, artefatos,
    host/usuário ssh e allowlist; sem cfg valem os padrões documentados.
    """
    nome = (nome or "local").lower()
    artefatos_dir = getattr(cfg, "artefatos_dir", None) if cfg is not None else None
    fabricas = {
        "local": lambda: ExecutorLocal(),
        "docker": lambda: ExecutorDocker(
            imagem=getattr(cfg, "docker_imagem", "alpine:latest") or "alpine:latest",
            artefatos_dir=artefatos_dir or None,
        ),
        "ssh": lambda: ExecutorSSH(
            host=getattr(cfg, "ssh_host", "") or "",
            usuario=getattr(cfg, "ssh_usuario", "") or "",
            allowlist=tuple(getattr(cfg, "ssh_allowlist", ()) or ()),
        ),
    }
    if nome not in fabricas:
        raise ValueError(f"Executor desconhecido: {nome!r}. Opções: {list(fabricas)}")
    return fabricas[nome]()