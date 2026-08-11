#!/usr/bin/env bash
# test_backup.sh — Teste automatizado da rotina de backup (backup.sh).
#
# Uso: ./test_backup.sh
# Saída esperada: "OK: backup criado e validado" e código de saída 0.

set -euo pipefail

PROJETO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESTINO_TESTE="$(mktemp -d)"
trap 'rm -rf "$DESTINO_TESTE"' EXIT

# 0. Executa a rotina de backup em um destino temporário.
"$PROJETO/backup.sh" "$DESTINO_TESTE" >/dev/null

BACKUP_DIR="$(ls -1dt "$DESTINO_TESTE"/backup_* 2>/dev/null | head -1)"
if [ -z "$BACKUP_DIR" ] || [ ! -d "$BACKUP_DIR" ]; then
  echo "FALHA: nenhum diretório de backup foi criado em $DESTINO_TESTE" >&2
  exit 1
fi

# 1. O manifesto deve existir e registrar os arquivos copiados.
[ -f "$BACKUP_DIR/MANIFESTO.txt" ] || {
  echo "FALHA: MANIFESTO.txt ausente em $BACKUP_DIR" >&2
  exit 1
}

# 2. Arquivos essenciais do projeto devem estar presentes no backup.
for arquivo in README.md main.py pixi.toml aegis/estado.py tests/conftest.py; do
  [ -f "$BACKUP_DIR/$arquivo" ] || {
    echo "FALHA: '$arquivo' ausente no backup" >&2
    exit 1
  }
done

# 3. Arquivos sensíveis/runtime NÃO devem vazar para o backup.
for excluido in config/env/.env config/dados/memoria_agente.db; do
  [ ! -e "$BACKUP_DIR/$excluido" ] || {
    echo "FALHA: '$excluido' não deveria estar no backup" >&2
    exit 1
  }
done

echo "OK: backup criado e validado em $BACKUP_DIR"
