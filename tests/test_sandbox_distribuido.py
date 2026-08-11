"""C7 — Execução distribuída: sandbox Docker e SSH (paridade Hermes).

Contrato dos backends com subprocess mockado (sem Docker/SSH reais no CI):
montagem do comando, timeout, denylist (docker) e allowlist (ssh), volume de
artefatos, auditoria `comandos.jsonl` com `backend` e ausência de vazamento
do ambiente do host (.env) em qualquer backend.
"""

import json as _json
import shutil
import subprocess

import pytest

from aegis.config import config
from aegis.ferramentas.basicas import comando_sandbox
from aegis.sandbox import (
    ExecutorDocker,
    ExecutorSSH,
    criar_executor,
    motivo_denylist,
)


# ---------------------------------------------------------------------
# Denylist (docker) — regras unitárias
# ---------------------------------------------------------------------

def test_denylist_reconhece_perigos():
    assert motivo_denylist("docker ps") == "docker-in-docker"
    assert motivo_denylist("docker run alpine") == "docker-in-docker"
    assert motivo_denylist("podman info") == "podman-in-docker"
    assert motivo_denylist("sh -c 'x --privileged'") == "container privilegiado"
    assert motivo_denylist(":(){ :|:& };:") == "bomba fork"
    assert motivo_denylist("git status") is None


# ---------------------------------------------------------------------
# ExecutorDocker — contrato com subprocess mockado
# ---------------------------------------------------------------------

def _mock_subprocess(monkeypatch, saida="ok-c7", codigo=0, stderr="", exc=None):
    captura = {}

    def fake_run(args, **kwargs):
        captura["args"] = list(args)
        captura["kwargs"] = kwargs
        if exc is not None:
            raise exc
        return subprocess.CompletedProcess(args, codigo, stdout=saida, stderr=stderr)

    monkeypatch.setattr("aegis.sandbox.subprocess.run", fake_run)
    return captura


def test_docker_monta_comando_completo(monkeypatch):
    captura = _mock_subprocess(monkeypatch, saida="ola-artefato")
    resultado = ExecutorDocker(imagem="alpine:3.20", artefatos_dir="/tmp/artes").executar("echo oi")

    esperado = [
        "docker", "run", "--rm", "-i", "--network=none",
        "-v", "/tmp/artes:/artefatos", "-w", "/artefatos",
        "alpine:3.20", "sh", "-c", "echo oi",
    ]
    assert captura["args"] == esperado
    assert captura["kwargs"]["timeout"] == 30
    assert resultado.sucesso and "ola-artefato" in resultado.saida
    assert resultado.backend == "docker"


def test_docker_denylist_bloqueia_sem_chamar_subprocess(monkeypatch):
    captura = _mock_subprocess(monkeypatch)
    resultado = ExecutorDocker().executar("docker ps")
    assert "denylist" in (resultado.erro or "")
    assert "docker-in-docker" in (resultado.erro or "")
    assert "args" not in captura  # subprocess.run NUNCA foi chamado


def test_docker_timeout(monkeypatch):
    captura = _mock_subprocess(monkeypatch, exc=subprocess.TimeoutExpired("cmd", 1))
    resultado = ExecutorDocker().executar("sleep 9", timeout=1)
    assert "tempo esgotado após 1s" in (resultado.erro or "")
    assert resultado.backend == "docker"


def test_docker_sem_instalacao(monkeypatch):
    _mock_subprocess(monkeypatch, exc=FileNotFoundError())
    resultado = ExecutorDocker().executar("echo oi")
    assert "docker não instalado" in (resultado.erro or "")


def test_docker_nao_vaza_ambiente_do_host(monkeypatch):
    """O container NUNCA recebe env do host: sem `-e`/`--env-file` no comando."""
    captura = _mock_subprocess(monkeypatch)
    ExecutorDocker(artefatos_dir="/a").executar("env")
    assert not any(str(a) in ("-e", "--env", "--env-file") or
                   str(a).startswith("--env") for a in captura["args"])


# ---------------------------------------------------------------------
# ExecutorSSH — contrato e allowlist
# ---------------------------------------------------------------------

def test_ssh_monta_comando_com_allowlist(monkeypatch):
    captura = _mock_subprocess(monkeypatch, saida="M main")
    ex = ExecutorSSH(host="srv", usuario="miguel", allowlist=("git", "ls"))
    resultado = ex.executar("git status --short")

    assert captura["args"][:5] == ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]
    assert captura["args"][5] == "miguel@srv"
    assert captura["args"][6] == "git status --short"
    assert resultado.sucesso and resultado.backend == "ssh"


def test_ssh_fora_da_allowlist_recusa(monkeypatch):
    captura = _mock_subprocess(monkeypatch)
    ex = ExecutorSSH(host="srv", usuario="miguel", allowlist=("git", "ls"))
    resultado = ex.executar("rm -rf /tmp/x")
    assert "fora da allowlist ssh" in (resultado.erro or "")
    assert "args" not in captura


def test_ssh_sem_destino_configurado(monkeypatch):
    resultado = ExecutorSSH(host="", usuario="").executar("git status")
    assert "sem destino" in (resultado.erro or "")
    assert "AEGIS_SSH_HOST" in (resultado.erro or "")


def test_ssh_timeout(monkeypatch):
    _mock_subprocess(monkeypatch, exc=subprocess.TimeoutExpired("ssh", 1))
    resultado = ExecutorSSH(host="h", usuario="u").executar("git status", timeout=1)
    assert "tempo esgotado após 1s" in (resultado.erro or "")
    assert resultado.backend == "ssh"


def test_ssh_nao_vaza_ambiente_do_host(monkeypatch):
    captura = _mock_subprocess(monkeypatch)
    ExecutorSSH(host="h", usuario="u").executar("env")
    assert not any("-e" == a or str(a).startswith("--env") for a in captura["args"])


# ---------------------------------------------------------------------
# criar_executor — seleção por config/backend
# ---------------------------------------------------------------------

def test_criar_executor_respeita_cfg(monkeypatch):
    cfg_obj = type("Cfg", (), {
        "docker_imagem": "python:3.12",
        "artefatos_dir": "/x/artefatos",
        "ssh_host": "srv", "ssh_usuario": "deploy",
        "ssh_allowlist": ("git",),
    })()
    docker = criar_executor("docker", cfg=cfg_obj)
    assert isinstance(docker, ExecutorDocker)
    assert docker.imagem == "python:3.12" and docker.artefatos == "/x/artefatos"
    ssh = criar_executor("ssh", cfg=cfg_obj)
    assert ssh.alvo == "deploy@srv" and ssh.allowlist == ("git",)
    with pytest.raises(ValueError):
        criar_executor("k8s")


# ---------------------------------------------------------------------
# comando_sandbox — backend docker via config + auditoria comandos.jsonl
# ---------------------------------------------------------------------

def test_comando_sandbox_backend_docker_audita(monkeypatch, tmp_path):
    import aegis.ferramentas.basicas as mod
    captura = _mock_subprocess(monkeypatch, saida="git status ok")

    monkeypatch.setattr(config, "sandbox_backend", "docker")
    monkeypatch.setattr(config, "comandos_path", tmp_path / "comandos.jsonl")
    monkeypatch.setattr(config, "docker_imagem", "alpine:latest")
    monkeypatch.setattr(config, "artefatos_dir", tmp_path / "artefatos")

    saida = comando_sandbox.invoke({"comando": "git status"})

    assert "backend=docker" in saida
    assert captura["args"][4] == f"--network=none"
    assert captura["kwargs"]["timeout"] == 30
    # auditoria: mesma linha JSONL com backend
    linhas = (tmp_path / "comandos.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(linhas) >= 1
    ultimo = _json.loads(linhas[-1])
    assert ultimo["backend"] == "docker" and ultimo["cmd"] == "git status"
    assert ultimo["status"] == "ok"


def test_comando_sandbox_local_audita_backend_local(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "sandbox_backend", "local")
    monkeypatch.setattr(config, "comandos_path", tmp_path / "comandos.jsonl")
    saida = comando_sandbox.invoke({"comando": "echo c7-local"})
    assert "backend=local" in saida and "c7-local" in saida
    ultimo = _json.loads((tmp_path / "comandos.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1])
    assert ultimo["backend"] == "local" and ultimo["status"] == "ok"


def test_auditoria_do_comando_com_politica_ganha_backend(monkeypatch, tmp_path):
    """A auditoria existente (tool `comando`) também carrega backend=local."""
    import aegis.ferramentas.sistema as mod_sistema
    if not hasattr(mod_sistema, "_registrar_comando"):
        pytest.skip("_registrar_comando ausente")
    monkeypatch.setattr(config, "comandos_path", tmp_path / "comandos.jsonl")
    mod_sistema._registrar_comando("git status", confirmado=False, status="ok",
                                   codigo=0, duracao_ms=12)
    ultimo = _json.loads((tmp_path / "comandos.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1])
    assert ultimo["backend"] == "local"


# ---------------------------------------------------------------------
# Integração opcional: Docker real (skip se ausente)
# ---------------------------------------------------------------------

@pytest.mark.skipif(shutil.which("docker") is None, reason="docker não está instalado")
def test_integracao_docker_real(monkeypatch, tmp_path):
    """Critério de aceite: `echo` roda no container efêmero com artefatos montados."""
    monkeypatch.setattr(config, "sandbox_backend", "docker")
    monkeypatch.setattr(config, "docker_imagem", "alpine:latest")
    monkeypatch.setattr(config, "artefatos_dir", tmp_path)
    monkeypatch.setattr(config, "comandos_path", tmp_path / "comandos.jsonl")

    saida = comando_sandbox.invoke({"comando": "echo c7-prova-real && ls /artefatos"})
    assert "backend=docker" in saida
    assert "c7-prova-real" in saida
    # volume montado: artefato criado no host visível dentro do container
    (tmp_path / "prova-volume.txt").write_text("x", encoding="utf-8")
    saida2 = comando_sandbox.invoke({"comando": "ls /artefatos"})
    assert "prova-volume.txt" in saida2


def test_comando_sandbox_denylist_por_docker(monkeypatch, tmp_path):
    """Denylist vale pela ferramenta também (backend docker)."""
    import aegis.ferramentas.basicas as mod
    captura = _mock_subprocess(monkeypatch)
    monkeypatch.setattr(config, "sandbox_backend", "docker")
    monkeypatch.setattr(config, "comandos_path", tmp_path / "comandos.jsonl")
    saida = comando_sandbox.invoke({"comando": "docker ps"})
    assert "ERRO_FERRAMENTA" in saida and "denylist" in saida
    assert "args" not in captura