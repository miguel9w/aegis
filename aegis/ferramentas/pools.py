"""Pools de ferramentas do Aegis — fatias por domínio para especialistas.

Cada domínio multiagente (programacao, pesquisa, escrita, obsidian, memoria)
expõe um SUBCONJUNTO das ferramentas registradas. O agente principal continua
recebendo TUDO; os nós especialistas recebem apenas a sua pool — menos ruído
no tool_calls (menos tokens, resposta mais rápida) e escopo mais seguro.

As pools são declarativas por NOME estável da ferramenta (atributo ``name``
dos ``BaseTool``). ``registrar_pool`` permite a plugins/domínios declararem
pools próprias (mesmo padrão de ``extensions/plugins``).
"""

from __future__ import annotations

# Pools padrão — nomes verificados contra a lista real de ferramentas (47).
# A ordem é ALFABÉTICA de propósito: mantém o prompt de sistema estável entre
# execuções, favorecendo o cache do provedor (prompt caching).
_GERAL = {
    "anotar",
    "calculadora",
    "definir_papel",
    "executar_comando",
    "gerenciar_memoria",
    "hora_atual",
    "listar_papeis",
    "pensar",
    "pesquisar_memoria",
    "pesquisar_sessoes",
    "tarefas",
    "ver_notas",
    "ver_papel",
    "ver_pensamento",
}

POOLS: dict[str, set[str]] = {
    "programacao": _GERAL | {
        "buscar_web",
        "contar_palavras",
        "criar_skill",
        "usar_skill:pesquisa-tecnica",
    },
    "pesquisa": _GERAL | {
        "buscar_papers_arxiv",
        "buscar_web",
        "delegar_pesquisa",
        "gerar_citacao_bibtex",
        "revisar_literatura",
        "salvar_paper",
    },
    "escrita": _GERAL | {
        "buscar_web",
        "contar_palavras",
        "delegar_redacao",
        "reverter_texto",
    },
    "obsidian": _GERAL | {
        "buscar_notas",
        "criar_nota",
        "ler_nota",
        "ligar_nota",
        "limpar_obsidian",
        "listar_obsidian",
        "notas_conectadas",
        "notas_por_tag",
    },
    "memoria": _GERAL | {
        "consultar_memoria_camel",
        "esquecer_memoria_camel",
        "listar_agendamentos",
        "registrar_memoria_camel",
        "ver_plano",
    },
    "prompt": _GERAL | {
        "listar_prompts_avancados",
        "usar_prompt_avancado",
        "ver_prompt_avancado",
    },
}

# Pools extras registradas em runtime (plugins/domínios declarativos).
_POOLS_EXTRA: dict[str, set[str]] = {}


def registrar_pool(nome: str, nomes: set[str]) -> None:
    """Registra (ou substitui) uma pool de ferramentas em runtime."""
    _POOLS_EXTRA[nome] = set(nomes)


def nomes_de_pool(pool: str) -> set[str]:
    """Nomes da pool (estendida se `pool` está entre as extras)."""
    base = POOLS.get(pool, set())
    return base | _POOLS_EXTRA.get(pool, set())


def pool_da_lista(ferramentas: list, dominio: str | None) -> list:
    """Filtra uma lista de ferramentas pela pool do domínio.

    `dominio=None` devolve a lista inteira (agente principal). Nomes que não
    pertencem à pool cadastrada são EXCLUÍDOS; nomes órfãos (referenciados mas
    inexistentes) são ignorados silenciosamente — o teste de integridade
    garante que isso não aconteça em produção.
    """
    if not dominio or dominio not in POOLS and dominio not in _POOLS_EXTRA:
        return list(ferramentas)
    permitidos = nomes_de_pool(dominio)
    return [f for f in ferramentas if f.name in permitidos]


def nomes_das_ferramentas(ferramentas: list) -> set[str]:
    """Conjunto de nomes de uma lista de ferramentas (para validação)."""
    return {f.name for f in ferramentas}


def integridade(nomes_reais: set[str]) -> list[str]:
    """Valida as pools contra a lista real de ferramentas.

    Retorna os nomes órfãos (referenciados em pools mas que não existem).
    Vazio = íntegro.
    """
    orfaos: list[str] = []
    for pool, nomes in POOLS.items():
        for nome in sorted(nomes):
            if nome not in nomes_reais:
                orfaos.append(f"{pool}/{nome}")
    for pool, nomes in _POOLS_EXTRA.items():
        for nome in sorted(nomes):
            if nome not in nomes_reais:
                orfaos.append(f"{pool}(extra)/{nome}")
    return orfaos