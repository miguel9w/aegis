"""Pacote de ferramentas — representante do registro central."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool

from ..config import config
from .basicas import ferramentas_basicas
from ..skills import carregar_e_expor
from ..plugins import carregar_plugins, erros_carregamento
from ..recuperacao import pesquisar_memoria
from ..subagentes import delegar_pesquisa, delegar_redacao
from ..agendador import agendar, cancelar_agendamento, listar_agendamentos

# Cache em nível de módulo (recarregado sob demanda)
_cache_ferramentas: list[BaseTool] | None = None


def carregar_ferramentas(config_obj: Any = None) -> list[BaseTool]:
    """
    Monta o registro completo de ferramentas:
      built-ins + habilidades (extensions/skills/) + plugins (extensions/plugins/).
    """
    global _cache_ferramentas
    cfg = config_obj or config

    ferramentas: list[BaseTool] = []
    ferramentas.extend(ferramentas_basicas())
    ferramentas.append(pesquisar_memoria)  # RAG-lite sobre Store + extensions/skills
    ferramentas.append(delegar_pesquisa)  # subagente pesquisador
    ferramentas.append(delegar_redacao)   # subagente redator
    ferramentas.append(agendar)  # cron interno
    ferramentas.append(listar_agendamentos)
    ferramentas.append(cancelar_agendamento)
    ferramentas.extend(carregar_e_expor(cfg.skills_dir))
    ferramentas.extend(carregar_plugins())

    _cache_ferramentas = ferramentas
    return ferramentas


def recarregar_tudo(config_obj: Any = None) -> list[BaseTool]:
    """Recarrega habilidades E plugins (auto-evolução em runtime)."""
    from ..plugins import recarregar_plugins as _reload
    from ..skills import carregar_e_expor as _expor

    cfg = config_obj or config
    ferramentas: list[BaseTool] = []
    ferramentas.extend(ferramentas_basicas())
    ferramentas.append(pesquisar_memoria)
    ferramentas.append(delegar_pesquisa)
    ferramentas.append(delegar_redacao)
    ferramentas.append(agendar)
    ferramentas.append(listar_agendamentos)
    ferramentas.append(cancelar_agendamento)
    ferramentas.extend(_expor(cfg.skills_dir))
    ferramentas.extend(_reload())
    _cache_ferramentas = ferramentas
    return ferramentas


def ferramentas_atuais() -> list[BaseTool]:
    """Retorna o registro em cache (ou carrega uma vez)."""
    if _cache_ferramentas is None:
        return carregar_ferramentas()
    return _cache_ferramentas


def avisos_carregamento() -> list[str]:
    """Avisos de plugins/skills com falha de carregamento."""
    return erros_carregamento()