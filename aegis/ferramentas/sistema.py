"""
Ferramentas do sistema — arquivos (sandbox) e comandos (política).

- `escrever_arquivo` / `editar_arquivo` / `ler_arquivo` / `listar_arquivos`:
  acesso a arquivos RESTRITO a `config.artefatos_dir` (default
  `config/dados/artefatos/`) e à raiz do projeto (anti path-traversal).
  Escrita/edição devolvem **diff unified** (difflib) para o LLM e a UI.
- `executar_comando`: comandos do sistema **sem sandbox** (pipes/redirects,
  shell), mas com **política de segurança**: allowlist de leitura roda direto;
  denylist absoluta recusa SEMPRE (destruição, escalada, exfiltração);
  demais exigem `confirmar=True`; toda execução é auditada em
  `config/dados/comandos.jsonl` com env limpo (sem segredos) e timeout.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
import shlex
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from langchain_core.tools import tool

from ..config import config, RAIZ
from ..config_json import carregar_config_json as _cfg_json

# ---------------------------------------------------------------------
# Helpers de caminho (sandbox de arquivos)
# ---------------------------------------------------------------------


def _permitidos(escrita: bool = False) -> list[Path]:
    """Diretórios raiz permitidos.

    - escrita: APENAS `config.artefatos_dir` (sandbox real — o agente não
      mexe no projeto; o usuário reclamou uma vez do chat escrever na raiz);
    - leitura: artefatos + raiz do projeto (o agente pode se orientar no
      código do próprio projeto sem poder alterá-lo).
    """
    raizes = [config.artefatos_dir.resolve()]
    if not escrita:
        raizes.append(RAIZ.resolve())
    return raizes


def _resolver(caminho: str, escrita: bool = False) -> Path:
    """Resolve o caminho (relativos contra a raiz do projeto) e valida o sandbox."""
    base = Path(caminho).expanduser()
    if not base.is_absolute():
        base = RAIZ / base
    alvo = base.resolve()
    permitidos = _permitidos(escrita)
    for raiz in permitidos:
        try:
            alvo.relative_to(raiz)
            return alvo
        except ValueError:
            continue
    if escrita:
        raise ValueError(
            f"escrita permitida apenas em {config.artefatos_dir} "
            f"(fora do sandbox: {caminho!r})"
        )
    raise ValueError(
        f"caminho fora do permitido: {caminho!r} "
        f"(permitido: {', '.join(str(p) for p in permitidos)})"
    )


def _diferenciar(antes: str, depois: str, caminho: str) -> str:
    """Diff unified entre dois conteúdos (vazio se idênticos)."""
    linhas_a = antes.splitlines(keepends=True)
    linhas_b = depois.splitlines(keepends=True)
    diff = "".join(
        difflib.unified_diff(
            linhas_a, linhas_b, fromfile=caminho, tofile=caminho, lineterm="\n"
        )
    )
    return diff


# ---------------------------------------------------------------------
# Ferramentas de arquivo
# ---------------------------------------------------------------------


@tool
def ler_arquivo(caminho: str, limite: int = 4000) -> str:
    """Lê um arquivo de texto (truncado). Use para inspecionar arquivos do
    projeto ou dos artefatos antes de editar.

    Args:
        caminho: caminho do arquivo (relativo ao projeto ou absoluto permitido).
        limite: máximo de caracteres retornados (default 4000).
    """
    try:
        alvo = _resolver(caminho)
        if not alvo.is_file():
            return f"erro: arquivo não encontrado: {caminho}"
        conteudo = alvo.read_text(encoding="utf-8", errors="replace")
        if len(conteudo) > limite:
            conteudo = conteudo[:limite] + f"\n… ({len(conteudo)} caracteres, truncado)"
        return f"{alvo}\n---\n{conteudo}"
    except ValueError as exc:
        return f"erro: {exc}"


@tool
def escrever_arquivo(caminho: str, conteudo: str) -> str:
    """Cria ou sobrescreve um arquivo (somente dentro do projeto ou de
    `config/dados/artefatos/`). Retorna o diff unified das mudanças — a UI
    mostra a edição em tempo real.

    Args:
        caminho: caminho do arquivo (relativo ao projeto ou absoluto permitido).
        conteudo: conteúdo completo do arquivo.
    """
    try:
        alvo = _resolver(caminho, escrita=True)
    except ValueError as exc:
        return f"erro: {exc}"
    try:
        antes = alvo.read_text(encoding="utf-8") if alvo.exists() else ""
        alvo.parent.mkdir(parents=True, exist_ok=True)
        alvo.write_text(conteudo, encoding="utf-8")
        diff = _diferenciar(antes, conteudo, str(alvo))
        if not diff:
            return f"ok (inalterado — conteúdo idêntico): {alvo}"
        return f"ok — {len(conteudo)} caracteres\n{diff}"
    except OSError as exc:
        return f"erro ao escrever {alvo}: {exc}"


@tool
def editar_arquivo(caminho: str, trecho_antigo: str, trecho_novo: str) -> str:
    """Edita um arquivo substituindo UM trecho exato por outro (única
    ocorrência; se ambíguo, inclua mais contexto). Retorna o diff unified.

    Args:
        caminho: caminho do arquivo (relativo ao projeto ou absoluto permitido).
        trecho_antigo: texto exato a substituir (única ocorrência obrigatória).
        trecho_novo: texto que substitui o trecho antigo.
    """
    try:
        alvo = _resolver(caminho, escrita=True)
    except ValueError as exc:
        return f"erro: {exc}"
    if not alvo.is_file():
        return f"erro: arquivo não encontrado: {caminho}"
    try:
        antes = alvo.read_text(encoding="utf-8")
    except OSError as exc:
        return f"erro ao ler {alvo}: {exc}"
    ocorrencias = antes.count(trecho_antigo)
    if ocorrencias == 0:
        return f"erro: trecho antigo não encontrado em {caminho}"
    if ocorrencias > 1:
        return (
            f"erro: trecho antigo é ambíguo ({ocorrencias} ocorrências em "
            f"{caminho}) — inclua mais contexto ao redor do trecho"
        )
    depois = antes.replace(trecho_antigo, trecho_novo, 1)
    try:
        alvo.write_text(depois, encoding="utf-8")
    except OSError as exc:
        return f"erro ao escrever {alvo}: {exc}"
    diff = _diferenciar(antes, depois, str(alvo))
    return f"ok — 1 ocorrência substituída\n{diff}"


@tool
def listar_arquivos(diretorio: str = ".", limite: int = 50) -> str:
    """Lista os arquivos de um diretório do projeto/artefatos (árvore rasa
    limitada). Use para descobrir a estrutura antes de ler/editar.

    Args:
        diretorio: diretório relativo ao projeto (default raiz).
        limite: máximo de entradas (default 50).
    """
    try:
        alvo = _resolver(diretorio)
    except ValueError as exc:
        return f"erro: {exc}"
    if not alvo.is_dir():
        return f"erro: diretório não encontrado: {diretorio}"
    entradas: list[str] = []
    for raiz, pastas, arquivos in os.walk(alvo):
        nivel = Path(raiz).relative_to(alvo)
        pastas.sort()
        arquivos.sort()
        for p in pastas:
            entradas.append(f"{'  ' * len(nivel.parts)}{p}/")
        for a in arquivos:
            entradas.append(f"{'  ' * len(nivel.parts)}{a}")
        if len(entradas) >= limite:
            break
    corpo = "\n".join(entradas[:limite])
    if len(entradas) > limite:
        corpo += f"\n… ({len(entradas) - limite} entradas omitidas)"
    return f"{alvo}\n{corpo}"


# ---------------------------------------------------------------------
# Política de comandos do sistema
# ---------------------------------------------------------------------

# Comandos de LEITURA (podem rodar sem confirmação) — primeiro token
_LEITURA = {
    "ls", "cat", "head", "tail", "grep", "find", "pwd", "echo", "printf",
    "wc", "sort", "uniq", "du", "df", "free", "ps", "uname", "which", "env",
    "file", "date", "cal", "whoami", "id", "tree", "basename", "dirname",
    "stat", "sha256sum", "md5sum", "realpath", "history", "xargs", "tr",
}

# Subcomandos de LEITURA do git (demais: escrita → exigem confirmação)
_GIT_LEITURA = {"status", "log", "diff", "show", "branch", "stash", "remote", "ls-files"}

# Binários SEMPRE proibidos (destruição / escalada / exfiltração / sistema)
_BINARIOS_PROIBIDOS = {
    "mkfs", "dd", "shutdown", "reboot", "halt", "poweroff", "fdisk", "parted",
    "mount", "umount", "chown", "su", "sudo", "docker", "systemctl", "journalctl",
    "pkill", "killall", "ssh", "scp", "nc", "ncat", "telnet", "openssl", "gpg",
}

# Padrões destrutivos detectados em QUALQUER posição do comando
_DENYLIST_PADROES = [
    r"\brm\s+(?:-\w+\s+)*(\.|~|/)(\s|$)",      # rm -rf /, ~ ou . (cwd)
    r"\brm\b[^|;]*\s/(etc|usr|var|boot|bin|sbin|lib|home|root|opt|dev)\b",  # rm em path de sistema
    r"\bsudo\s+rm\b",
    r"\bchmod\s+-R\s+777\s+/\b",
    r">\s*/dev/sd[a-z]?\d*",                   # escrita crua em disco
    r">\s*/etc/",
    r"\b(curl|wget)\b[^|;]*\|\s*(ba|z|k)?sh\b",  # baixar e executar
    r":\(\)\s*\{",                             # fork bomb
    r"\bkill\s+-9\s+\d+\b",
]

_segredos_re = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH|CREDENTIAL)", re.I)


def _verificar_politica(comando: str) -> tuple[bool, str | None]:
    """Retorna (permitido, motivo_de_recusa). Denylist sempre vence."""
    for padrao in _DENYLIST_PADROES:
        if re.search(padrao, comando):
            return False, f"comando recusado pela política de segurança (padrão destrutivo: {padrao})"
    primeiro = shlex.split(comando)[0] if shlex.split(comando) else ""
    token = primeiro.lower().split("/")[-1]
    for proibido in _BINARIOS_PROIBIDOS:
        if token == proibido or token.startswith(proibido + "."):
            return False, f"comando recusado pela política de segurança (binário proibido: {token})"
    if token in _LEITURA:
        return True, None
    if token == "git":
        partes = shlex.split(comando)
        if len(partes) > 1 and partes[1] in _GIT_LEITURA:
            return True, None
    return True, "exige confirmar=True (comando fora da allowlist de leitura)"


def _env_limpo() -> dict[str, str]:
    """Ambiente do subprocesso SEM segredos (chaves/tokens nunca vazam)."""
    permitidas = {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "USER", "SHELL", "TERM"}
    return {k: v for k, v in os.environ.items() if k in permitidas and not _segredos_re.search(k)}


def _registrar_comando(comando: str, *, confirmado: bool, status: str,
                       codigo: int | None, duracao_ms: int, motivo: str | None = "") -> None:
    """Auditoria em config/dados/comandos.jsonl (gitignored)."""
    caminho = config.comandos_path
    caminho.parent.mkdir(parents=True, exist_ok=True)
    registro = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "cmd": comando,
        "sha256": hashlib.sha256(comando.encode()).hexdigest()[:16],
        "confirmado": confirmado,
        "status": status,
        "codigo": codigo,
        "duracao_ms": duracao_ms,
        "motivo": motivo,
    }
    try:
        with caminho.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(registro, ensure_ascii=False) + "\n")
    except OSError:
        pass  # auditoria nunca derruba a ferramenta


@tool
def executar_comando(comando: str, confirmar: bool = False) -> str:
    """Executa um comando do sistema (shell, com pipes/redirects) — SEM
    sandbox: roda com o seu usuário. Política de segurança obrigatória:
    leitura (ls/cat/git status etc.) roda direto; comandos de escrita
    (instalar, git commit/push, mover, apagar…) exigem `confirmar=True`;
    comandos destrutivos (formatação de disco, shutdown, chown, sudo, curl|sh,
    exfiltração ssh/nc…) são recusados SEMPRE. Toda execução é auditada em
    `config/dados/comandos.jsonl` com timeout e ambiente sem segredos.

    Args:
        comando: comando shell completo (ex.: "git status --short").
        confirmar: True para comandos fora da allowlist de leitura.
    """
    inicio = time.monotonic()
    permitido, motivo = _verificar_politica(comando)
    if not permitido:
        _registrar_comando(comando, confirmado=confirmar, status="recusado",
                           codigo=None, duracao_ms=0, motivo=motivo)
        return f"erro: {motivo}"
    if motivo is not None and not confirmar:
        _registrar_comando(comando, confirmado=False, status="exige_confirmacao",
                           codigo=None, duracao_ms=0, motivo=motivo)
        return f"erro: {motivo} — refaça a chamada com confirmar=True"
    timeout = int(config.exec_timeout)
    cwd = str(config.exec_cwd or RAIZ)
    try:
        proc = subprocess.run(
            comando, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=cwd, env=_env_limpo(),
        )
        saida = proc.stdout
        if proc.stderr:
            saida = f"{saida}\n[stderr]\n{proc.stderr}".strip()
        limite = int(_cfg_json("limites.json", {"limite_resultado": 8000})["limite_resultado"])
        if len(saida) > limite:
            saida = saida[:limite] + f"\n… ({len(saida)} caracteres, truncado)"
        status = "ok" if proc.returncode == 0 else "erro"
        duracao_ms = int((time.monotonic() - inicio) * 1000)
        _registrar_comando(comando, confirmado=confirmar, status=status,
                           codigo=proc.returncode, duracao_ms=duracao_ms)
        return f"código={proc.returncode} duração={duracao_ms}ms\n{saida}"
    except subprocess.TimeoutExpired:
        duracao_ms = int((time.monotonic() - inicio) * 1000)
        _registrar_comando(comando, confirmado=confirmar, status="timeout",
                           codigo=None, duracao_ms=duracao_ms)
        return f"erro: tempo esgotado após {timeout}s"
    except Exception as exc:  # noqa: BLE001 — nunca derruba a ferramenta
        duracao_ms = int((time.monotonic() - inicio) * 1000)
        _registrar_comando(comando, confirmado=confirmar, status="erro",
                           codigo=None, duracao_ms=duracao_ms, motivo=str(exc))
        return f"erro ao executar comando: {exc}"


def ferramentas_sistema() -> list:
    """Lista de ferramentas do sistema (arquivo + comando)."""
    return [escrever_arquivo, editar_arquivo, ler_arquivo, listar_arquivos, executar_comando]