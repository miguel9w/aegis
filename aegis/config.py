"""
Configuração central do Aegis.

Carrega credenciais e parâmetros de `.env` (isolamento total de chaves)
e expõe tudo através de um singleton tipado. Nenhuma chave é commitada.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Diretório raiz do projeto (pasta que contém o pacote `aegis`)
RAIZ = Path(__file__).resolve().parent.parent

# Carrega .env se existir (silencioso se não houver)
load_dotenv(RAIZ / "config" / "env" / ".env")

# Sinalizador global de modo verboso (--dev)
def _dev() -> bool:
    return os.getenv("AEGIS_DEV", "false").strip().lower() in {"1", "true", "yes"}


class ConfigError(RuntimeError):
    """Erro de configuração (ex.: chave de API ausente)."""


class Config:
    """Contém toda a configuração do Aegis."""

    def __init__(self) -> None:
        # --- Provedor cognitivo ---
        self.api_base: str = os.getenv("OPENAI_API_BASE", "https://api.deepseek.com/v1")
        self.api_key: str = os.getenv("OPENAI_API_KEY", "")
        self.modelo: str = os.getenv("MODEL_NAME", "deepseek-chat")
        self.temperatura: float = float(os.getenv("AEGIS_TEMPERATURA", "0.7"))
        self.max_tokens: int = int(os.getenv("AEGIS_MAX_TOKENS", "4096"))
        self.timeout: int = int(os.getenv("AEGIS_TIMEOUT", "60"))

        # --- Sessão / persistência ---
        self.thread_id: str = os.getenv("AEGIS_THREAD_ID", "default")
        self.banco: Path = RAIZ / os.getenv("AEGIS_DB", "config/dados/memoria_agente.db")

        # --- Gestão de contexto ---
        self.limiar_compressao: int = int(os.getenv("AEGIS_LIMIAR_COMPRESSAO", "20"))
        self.manter_apos_compressao: int = int(os.getenv("AEGIS_MANTER_APOS_COMPRESSAO", "8"))
        self.max_tentativas_correcao: int = int(os.getenv("AEGIS_MAX_TENTATIVAS_CORRECAO", "3"))

        # --- Memória de longo prazo ---
        self.memoria_ativa: bool = (
            os.getenv("AEGIS_MEMORIA_ATIVA", "true").strip().lower() in {"1", "true", "yes"}
        )

        # --- Habilidades / trajetória ---
        self.skills_dir: Path = RAIZ / os.getenv("AEGIS_SKILLS_DIR", "extensions/skills")
        # Flag de ativação + diretório (separados — AEGIS_TRAJETORIA é um switch)
        self.trajetoria_ativa: bool = (
            os.getenv("AEGIS_TRAJETORIA", "false").strip().lower() in {"1", "true", "yes"}
        )
        self.trajetorias_dir: Path = RAIZ / os.getenv("AEGIS_TRAJETORIA_DIR", "config/dados/trajetorias")

        # --- Subagentes avançados (agent-as-tool) ---
        self.subagentes_ativos: bool = (
            os.getenv("AEGIS_SUBAGENTES", "true").strip().lower() in {"1", "true", "yes"}
        )

        # --- Agendador (cron interno) ---
        self.agendamentos_path: Path = RAIZ / os.getenv("AEGIS_AGENDAMENTOS", "config/dados/agendamentos.jsonl")
        self.agendador_intervalo: int = int(os.getenv("AEGIS_AGENDADOR_INTERVALO", "60"))
        self.agendador_callback: str = os.getenv("AEGIS_AGENDADOR_CALLBACK_URL", "").strip()

        # --- Busca web alterna (SearXNG) ---
        self.searxng_url: str = os.getenv("AEGIS_SEARXNG_URL", "").strip().rstrip("/")

        # --- Diversos ---
        self.dev: bool = _dev()


# Singleton global (configuração carregada uma vez por processo)
config = Config()