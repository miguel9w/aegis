"""Teste automatizado da rotina de backup (backup.sh).

Executa o script real num destino temporário e valida:
  1. o diretório de backup é criado;
  2. o manifesto (MANIFESTO.txt) é gerado;
  3. arquivos essenciais do projeto são copiados;
  4. arquivos sensíveis/runtime NÃO vazam para o backup.
"""

import glob
import os
import subprocess
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
BACKUP_SH = RAIZ / "backup.sh"

ESSENCIAIS = [
    "README.md",
    "main.py",
    "pixi.toml",
    "aegis/estado.py",
    "tests/conftest.py",
]

SENSIVEIS = [
    "config/env/.env",
    "config/dados/memoria_agente.db",
]


def _executar_backup(destino: Path) -> Path:
    """Roda backup.sh em destino temporário e devolve o diretório criado."""
    subprocess.run(
        [str(BACKUP_SH), str(destino)],
        check=True,
        capture_output=True,
        text=True,
    )
    backups = sorted(glob.glob(str(destino / "backup_*")))
    assert backups, "nenhum diretório backup_* foi criado"
    return Path(backups[-1])


def test_backup_cria_diretorio_e_manifesto(tmp_path: Path) -> None:
    backup_dir = _executar_backup(tmp_path)
    assert backup_dir.is_dir()
    manifesto = backup_dir / "MANIFESTO.txt"
    assert manifesto.is_file(), "MANIFESTO.txt ausente"
    conteudo = manifesto.read_text(encoding="utf-8")
    assert "Arquivos copiados:" in conteudo


def test_backup_copia_arquivos_essenciais(tmp_path: Path) -> None:
    backup_dir = _executar_backup(tmp_path)
    for rel in ESSENCIAIS:
        assert (backup_dir / rel).is_file(), f"'{rel}' ausente no backup"


def test_backup_nao_vaza_arquivos_sensiveis(tmp_path: Path) -> None:
    backup_dir = _executar_backup(tmp_path)
    for rel in SENSIVEIS:
        assert not (backup_dir / rel).exists(), f"'{rel}' não deveria estar no backup"
