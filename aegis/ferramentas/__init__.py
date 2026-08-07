"""Pacote de ferramentas — representante do registro central."""

from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool

from ..config import config
from .basicas import ferramentas_basicas
from ..skills import carregar_e_expor
from ..plugins import carregar_plugins, erros_carregamento
from ..recuperacao import pesquisar_memoria
from ..memoria_tool import gerenciar_memoria
from ..subagentes import delegar_pesquisa, delegar_redacao
from ..agendador import agendar, cancelar_agendamento, listar_agendamentos
from ..sessoes import pesquisar_sessoes
from ..tarefas import tarefas
from ..papeis import definir_papel, especificar_tarefa, estruturar_tarefa, listar_papeis, ver_papel
from ..memoria_camel import consultar_memoria_camel, esquecer_memoria_camel, registrar_memoria_camel
from ..camel_kit import (anotar, atualizar_plano, pensar, planejar_tarefa,
                         ver_notas, ver_pensamento, ver_plano)
from ..cientificas import (buscar_papers_arxiv, gerar_citacao_bibtex,
                           revisar_literatura, salvar_paper)
from ..obsidian import (buscar_notas, criar_nota, ler_nota, ligar_nota,
                        limpar_obsidian, listar_obsidian, notas_conectadas,
                        notas_por_tag)

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
    ferramentas.append(gerenciar_memoria)  # memória explícita salvar/esquecer/listar
    ferramentas.append(delegar_pesquisa)  # subagente pesquisador
    ferramentas.append(delegar_redacao)   # subagente redator
    ferramentas.append(agendar)  # cron interno
    ferramentas.append(listar_agendamentos)
    ferramentas.append(cancelar_agendamento)
    ferramentas.append(pesquisar_sessoes)  # recall de sessões passadas (Hermes)
    ferramentas.append(tarefas)  # todo/planejamento (Hermes)
    ferramentas.append(definir_papel)  # papel ativo (CAMEL role-playing)
    ferramentas.append(ver_papel)
    ferramentas.append(listar_papeis)
    ferramentas.append(especificar_tarefa)  # task specification (CAMEL)
    ferramentas.append(estruturar_tarefa)
    ferramentas.append(registrar_memoria_camel)  # memória pontuada (CAMEL)
    ferramentas.append(consultar_memoria_camel)
    ferramentas.append(esquecer_memoria_camel)
    ferramentas.append(pensar)  # toolkits CAMEL (thinking/planning/notes)
    ferramentas.append(ver_pensamento)
    ferramentas.append(planejar_tarefa)
    ferramentas.append(atualizar_plano)
    ferramentas.append(ver_plano)
    ferramentas.append(anotar)
    ferramentas.append(ver_notas)
    ferramentas.append(buscar_papers_arxiv)  # científico (arXiv)
    ferramentas.append(gerar_citacao_bibtex)
    ferramentas.append(salvar_paper)
    ferramentas.append(revisar_literatura)
    ferramentas.append(criar_nota)  # vault Obsidian
    ferramentas.append(ler_nota)
    ferramentas.append(ligar_nota)
    ferramentas.append(buscar_notas)
    ferramentas.append(notas_por_tag)
    ferramentas.append(notas_conectadas)
    ferramentas.append(listar_obsidian)
    ferramentas.append(limpar_obsidian)
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
    ferramentas.append(gerenciar_memoria)
    ferramentas.append(delegar_pesquisa)
    ferramentas.append(delegar_redacao)
    ferramentas.append(agendar)
    ferramentas.append(listar_agendamentos)
    ferramentas.append(cancelar_agendamento)
    ferramentas.append(pesquisar_sessoes)
    ferramentas.append(tarefas)
    ferramentas.append(definir_papel)
    ferramentas.append(ver_papel)
    ferramentas.append(listar_papeis)
    ferramentas.append(especificar_tarefa)
    ferramentas.append(estruturar_tarefa)
    ferramentas.append(registrar_memoria_camel)
    ferramentas.append(consultar_memoria_camel)
    ferramentas.append(esquecer_memoria_camel)
    ferramentas.append(pensar)
    ferramentas.append(ver_pensamento)
    ferramentas.append(planejar_tarefa)
    ferramentas.append(atualizar_plano)
    ferramentas.append(ver_plano)
    ferramentas.append(anotar)
    ferramentas.append(ver_notas)
    ferramentas.append(buscar_papers_arxiv)
    ferramentas.append(gerar_citacao_bibtex)
    ferramentas.append(salvar_paper)
    ferramentas.append(revisar_literatura)
    ferramentas.append(criar_nota)
    ferramentas.append(ler_nota)
    ferramentas.append(ligar_nota)
    ferramentas.append(buscar_notas)
    ferramentas.append(notas_por_tag)
    ferramentas.append(notas_conectadas)
    ferramentas.append(listar_obsidian)
    ferramentas.append(limpar_obsidian)
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