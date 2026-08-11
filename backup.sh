#!/usr/bin/env bash
# backup.sh — Rotina de backup dos arquivos essenciais do projeto Aegis.
#
# Uso: ./backup.sh [destino]
#   destino            diretório onde o backup será criado (default:
#                      config/dados/backups/, fora do versionamento)
#
# Variáveis de ambiente:
#   BACKUP_DESTINO     sobrescreve o destino (mesmo efeito que o 1º arg)
#   BACKUP_RETENCAO    número de backups mantidos (default: 5)
#
# A rotina copia APENAS arquivos rastreados pelo git (via `git ls-files`),
# garantindo que segredos (config/env/.env) e dados de runtime
# (config/dados/*) nunca entrem no backup.

set -euo pipefail

PROJETO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DESTINO="${BACKUP_DESTINO:-${1:-$PROJETO/config/dados/backups}}"
RETENCAO="${BACKUP_RETENCAO:-5}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
BACKUP_DIR="$DESTINO/backup_$TIMESTAMP"

# Valida que o projeto é um repositório git (fonte de verdade da lista).
if ! git -C "$PROJETO" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "ERRO: '$PROJETO' não é um repositório git — impossível listar arquivos." >&2
  exit 1
fi

mkdir -p "$BACKUP_DIR"

# Copia arquivo a arquivo, preservando a estrutura de diretórios.
contador=0
while IFS= read -r -d '' arquivo; do
  dir_destino="$BACKUP_DIR/$(dirname "$arquivo")"
  mkdir -p "$dir_destino"
  cp -a "$PROJETO/$arquivo" "$dir_destino/"
  contador=$((contador + 1))
done < <(git -C "$PROJETO" ls-files -z)

# Manifesto com metadados do backup.
{
  echo "Backup gerado em: $(date -Iseconds)"
  echo "Projeto: $PROJETO"
  echo "Commit de referência: $(git -C "$PROJETO" rev-parse --short HEAD)"
  echo "Arquivos copiados: $contador"
} > "$BACKUP_DIR/MANIFESTO.txt"

# Retenção: apaga backups antigos além do limite configurado.
ls -1dt "$DESTINO"/backup_* 2>/dev/null | tail -n +$((RETENCAO + 1)) | xargs -r rm -rf

echo "Backup concluído: $BACKUP_DIR ($contador arquivos)"
