"""
Configuração central do Aegis.

Carrega credenciais e parâmetros de `.env` (isolamento total de chaves)
e expõe tudo através de um singleton tipado. Nenhuma chave é commitada.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config_json import carregar_config_json as _cfg_json

from dotenv import load_dotenv

# Diretório raiz do projeto (pasta que contém o pacote `aegis`)
RAIZ = Path(__file__).resolve().parent.parent

# Checklist padrão da revisão por pares (G3) — sobreescrito por limites.json
_CHECKLIST_REVISAO_PADRAO: list[str] = [
    "seguranca", "sandbox de escrita", "testes", "documentacao", "anti-alucinacao",
]

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
        # Limite de recursão do grafo (loop agente↔ferramentas); vem de limites.json
        self.recursion_limit: int = int(
            _cfg_json("limites.json", {"recursion_limit": 50})["recursion_limit"])
        # Checklist fixo da revisão por pares (G3) — vem de limites.json
        self.checklist_revisao: list[str] = list(
            _cfg_json("limites.json", {"checklist_revisao": _CHECKLIST_REVISAO_PADRAO})
            .get("checklist_revisao") or _CHECKLIST_REVISAO_PADRAO)

        # --- Orçamento (C6) — preços (R$/1M tokens) e tetos por turno/sessão
        _limites = _cfg_json("limites.json", {})
        self.precos_por_token: dict[str, float] = dict(
            _limites.get("precos_por_token") or {"entrada": 0.55, "saida": 2.2, "reasoning": 3.0})
        self.orcamento_por_turno: dict[str, float] = dict(
            _limites.get("orcamento_por_turno") or {})
        self.orcamento_por_sessao: dict[str, float] = dict(
            _limites.get("orcamento_por_sessao") or {})

        # --- Gestão de contexto ---
        self.limiar_compressao: int = int(os.getenv("AEGIS_LIMIAR_COMPRESSAO", "20"))
        self.manter_apos_compressao: int = int(os.getenv("AEGIS_MANTER_APOS_COMPRESSAO", "8"))
        self.max_tentativas_correcao: int = int(os.getenv("AEGIS_MAX_TENTATIVAS_CORRECAO", "3"))

        # --- Memória de longo prazo ---
        self.memoria_ativa: bool = (
            os.getenv("AEGIS_MEMORIA_ATIVA", "true").strip().lower() in {"1", "true", "yes"}
        )

        # --- Verify-then-answer (C3): conferir resposta contra evidências ---
        self.verificacao_estrita: bool = (
            os.getenv("AEGIS_VERIFICACAO_ESTRITA", "true").strip().lower()
            in {"1", "true", "yes"}
        )

        # --- Memória estrutural (C4) ---
        # A cada N turnos o fim da sessão grava resumo incremental + decisões
        self.intervalo_resumo_sessao: int = int(os.getenv("AEGIS_INTERVALO_RESUMO", "5"))
        # Teto de caracteres por nível no recall hierárquico injetado no system
        self.teto_bloco_contexto: int = int(os.getenv("AEGIS_TETO_CONTEXTO", "600"))

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

        # --- Multiagente (orquestrador + especialistas + avaliador) ---
        self.multiagente_ativos: bool = (
            os.getenv("AEGIS_MULTIAGENTE", "true").strip().lower() in {"1", "true", "yes"}
        )
        self.max_especialistas: int = int(os.getenv("AEGIS_MAX_ESPECIALISTAS", "3"))
        self.modelo_orquestrador: str = os.getenv("AEGIS_MODELO_ORQUESTRADOR", "").strip()
        self.modelo_avaliador: str = os.getenv("AEGIS_MODELO_AVALIADOR", "").strip()
        self.timeout_no: int = int(os.getenv("AEGIS_TIMEOUT_NO", "120"))
        self.orquestracoes_path: Path = RAIZ / os.getenv(
            "AEGIS_ORQUESTRACOES", "config/dados/orquestracoes.jsonl")

        # --- Agendador (cron interno) ---
        self.agendamentos_path: Path = RAIZ / os.getenv("AEGIS_AGENDAMENTOS", "config/dados/agendamentos.jsonl")
        # --- Tarefas (todo) ---
        self.tarefas_path: Path = RAIZ / os.getenv("AEGIS_TAREFAS", "config/dados/tarefas.json")
        # --- Contexto do projeto (AGENTS.md) ---
        self.contexto_path: Path = RAIZ / os.getenv("AEGIS_CONTEXTO", "AGENTS.md")
        self.agendador_intervalo: int = int(os.getenv("AEGIS_AGENDADOR_INTERVALO", "60"))
        self.agendador_callback: str = os.getenv("AEGIS_AGENDADOR_CALLBACK_URL", "").strip()

        # --- Busca web alterna (SearXNG) ---
        self.searxng_url: str = os.getenv("AEGIS_SEARXNG_URL", "").strip().rstrip("/")

        # --- JSON de configuração (externaliza hardcodes) e estado CAMEL ---
        self.dados_dir: Path = RAIZ / os.getenv("AEGIS_DADOS_DIR", "config/dados")
        self.limites_config_path: Path = RAIZ / os.getenv("AEGIS_LIMITES", "config/dados/limites.json")
        self.tarefas_config_path: Path = RAIZ / os.getenv("AEGIS_TAREFAS_CONFIG", "config/dados/tarefas_config.json")
        self.agendador_config_path: Path = RAIZ / os.getenv("AEGIS_AGENDADOR_CONFIG", "config/dados/agendador_config.json")
        # Papéis (roles) e especificação de tarefa
        self.papeis_config_path: Path = RAIZ / os.getenv("AEGIS_PAPEIS", "config/dados/papeis.json")
        self.papel_ativo_path: Path = RAIZ / os.getenv("AEGIS_PAPEL_ATIVO", "config/dados/papel_ativo.json")
        self.tarefa_atual_path: Path = RAIZ / os.getenv("AEGIS_TAREFA_ATUAL", "config/dados/tarefa_atual.json")
        # Memória pontuada (estilo CAMEL)
        self.memoria_camel_path: Path = RAIZ / os.getenv("AEGIS_MEMORIA_CAMEL", "config/dados/memoria_camel.json")
        self.memoria_camel_config_path: Path = RAIZ / os.getenv(
            "AEGIS_MEMORIA_CAMEL_CONFIG", "config/dados/memoria_camel_config.json")
        # Toolkits CAMEL (thinking/task-planning/note-taking)
        self.pensamento_path: Path = RAIZ / os.getenv("AEGIS_PENSAMENTO", "config/dados/pensamento_atual.json")
        self.plano_tarefas_path: Path = RAIZ / os.getenv("AEGIS_PLANO_TAREFAS", "config/dados/plano_tarefas.json")
        self.notas_path: Path = RAIZ / os.getenv("AEGIS_NOTAS", "config/dados/notas.json")
        # Features científicas (arXiv) + vault estilo Obsidian
        self.biblioteca_path: Path = RAIZ / os.getenv("AEGIS_BIBLIOTECA", "config/dados/biblioteca.json")
        self.arxiv_max_resultados: int = int(os.getenv("AEGIS_ARXIV_MAX_RESULTADOS", "5"))
        self.obsidian_dir: Path = RAIZ / os.getenv("AEGIS_OBSIDIAN_DIR", "config/dados/obsidian")
        # Formato de prompt avançado (.apf) — fichas versionadas + estado ativo
        self.prompts_avancados_dir: Path = RAIZ / os.getenv("AEGIS_PROMPTS_DIR", "config/prompts_avancados")
        self.prompt_ativo_path: Path = RAIZ / os.getenv("AEGIS_PROMPT_ATIVO", "config/dados/prompt_ativo.json")

        # --- Ferramentas do sistema (arquivos + comandos) ---
        self.artefatos_dir: Path = RAIZ / os.getenv(
            "AEGIS_ARTEFATOS_DIR", "config/dados/artefatos")
        self.exec_timeout: int = int(os.getenv("AEGIS_EXEC_TIMEOUT", "120"))
        self.exec_cwd: Path = Path(os.getenv("AEGIS_EXEC_CWD", "")).expanduser() or RAIZ

        # --- Sandbox distribuído (C7) — backends docker/ssh via .env ---
        self.sandbox_backend: str = os.getenv("AEGIS_SANDBOX_BACKEND", "local").lower()
        self.docker_imagem: str = os.getenv("AEGIS_DOCKER_IMAGEM", "alpine:latest")
        # SSH: host/usuário NUNCA no repo — apenas .env (chave via agent/ssh-agent)
        self.ssh_host: str = os.getenv("AEGIS_SSH_HOST", "")
        self.ssh_usuario: str = os.getenv("AEGIS_SSH_USER", "")
        self.ssh_allowlist: tuple[str, ...] = tuple(
            x.strip() for x in os.getenv(
                "AEGIS_SSH_ALLOWLIST",
                "git,ls,df,du,cat,echo,pwd,whoami,uname,stat,head,tail").split(",")
            if x.strip())
        # Auditoria de comandos (JSONL) — cada execução com o backend usado
        self.comandos_path: Path = RAIZ / os.getenv(
            "AEGIS_COMANDOS", "config/dados/comandos.jsonl")

        # --- Aprendizados (G4) — versionados + grafo de conhecimento ---
        self.learnings_dir: Path = RAIZ / os.getenv(
            "AEGIS_LEARNINGS_DIR", "docs/learnings")
        self.grafo_path: Path = RAIZ / os.getenv(
            "AEGIS_GRAFO", "config/dados/grafo_conhecimento.json")

        # --- Diversos ---
        self.dev: bool = _dev()


# Singleton global (configuração carregada uma vez por processo)
config = Config()