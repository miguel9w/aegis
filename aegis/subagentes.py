"""
Subagentes avançados do Aegis — delegação via "agent-as-tool" (X1).

Arquitetura: cada subagente é um SUBGRAFO LangGraph compilado (stateless, sem
checkpointer) que reusa o mesmo loop cognitivo do núcleo — agente → ferramentas
→ reflexão (auto-correção) — mas com prompt de sistema ESPECIALISTA (persona)
e um SUBconjunto de ferramentas.

X1 — catálogo sob demanda: os delegados vêm de `config/dados/delegados.json`
(fallback embutido se ausente/corrompido): nome, descrição (para o LLM
escolher), ferramentas permitidas (por nome, resolvidas do registro central) e
`arq_limite` (evita delegação em cascata infinita: um subagente só recebe tools
de delegação no pool se a nova profundidade couber no limite — e `_executar`
bloqueia chamadas acima do limite do alvo).

As tools `delegar_<nome>` são geradas por fábrica a partir do catálogo e
registradas em `TOOLS_DELEGACAO` (o registro central de ferramentas as expõe
via `tools_delegacao()`). Os subagentes são construídos em
`configurar_subagentes(llm, cfg)` (chamada por `montar_grafo`).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.tools import BaseTool, tool
from langgraph.graph import END, START, StateGraph

from .config import Config
from .estado import EstadoAegis
from .nos import _eh_erro, fabricar_nos
from .prompts import (
    sistema_codigo,
    sistema_dados,
    sistema_pesquisador,
    sistema_redator,
    sistema_revisor,
)

# Raiz do repo (config/dados/delegados.json fica versionado junto).
_RAIZ = Path(__file__).resolve().parents[1]
_CAMINHO_CATALOGO = _RAIZ / "config" / "dados" / "delegados.json"

# Registrador global de subagentes (injetado por configurar_subagentes).
SUBAGENTES_ATUAIS: dict[str, Any] = {}
# Limite de profundidade de delegação por delegado (anti-cascata infinita).
ARQ_LIMITES: dict[str, int] = {}
# Tools de delegação geradas por fábrica (uma por delegado do catálogo).
TOOLS_DELEGACAO: dict[str, BaseTool] = {}
# Avisos do catálogo (JSON inválido etc.) — nunca quebram o grafo.
AVISOS_CATALOGO: list[str] = []

# Personas por nome de delegado (o catálogo NÃO carrega prompts — código).
_PERSONAS = {
    "pesquisador": sistema_pesquisador,
    "redator": sistema_redator,
    "codigo": sistema_codigo,
    "dados": sistema_dados,
    "revisao": sistema_revisor,
}

# Catálogo vigente (default no import; configurar_subagentes relê o JSON).
_CATALOGO_ATUAL: list[dict] = []


def _persona_padrao(nome: str) -> str:
    """Persona genérica para delegados custom do catálogo (sem prompt próprio)."""
    return (
        f"Você é o subagente {nome.upper()} do Aegis.\n"
        "Execute a tarefa com as ferramentas disponíveis, com auto-correção\n"
        "em caso de erro, e responda em português (pt-BR), de forma direta."
    )


def _persona(nome: str) -> str:
    fn = _PERSONAS.get(nome)
    return fn() if fn else _persona_padrao(nome)


# -- catálogo ---------------------------------------------------------------

_CATALOGO_PADRAO: list[dict] = [
    {
        "nome": "pesquisador",
        "tool": "delegar_pesquisa",
        "descricao": "Delega uma pesquisa profunda ao subagente PESQUISADOR. "
        "Use para perguntas complexas que exigem buscas na web, cruzamento de "
        "fontes ou raciocínio numérico com evidências. Retorna resposta "
        "sintetizada em pt-BR.",
        "parametro": "pergunta",
        "ferramentas": ["buscar_web", "calculadora", "pesquisar_memoria"],
        "arq_limite": 2,
    },
    {
        "nome": "redator",
        "tool": "delegar_redacao",
        "descricao": "Delega a produção de texto longo ao subagente REDATOR. "
        "Use para escrever/reescrever conteúdo estruturado (artigos, "
        "relatórios, seções, comunicados) em pt-BR.",
        "parametro": "tarefa",
        "ferramentas": [],
        "arq_limite": 1,
    },
    {
        "nome": "codigo",
        "descricao": "Delega a implementação de código ao subagente CODIGO. "
        "Use para implementar funções/módulos COM TESTES no sandbox de "
        "escrita: escreve arquivos, roda comandos e testes, corrige erros em "
        "loop e entrega o resultado verificado.",
        "parametro": "tarefa",
        "ferramentas": [
            "ler_arquivo", "escrever_arquivo", "editar_arquivo",
            "listar_arquivos", "executar_comando", "comando_sandbox",
            "calculadora",
        ],
        "arq_limite": 2,
    },
    {
        "nome": "dados",
        "descricao": "Delega análise de dados ao subagente DADOS. Use para "
        "análise exploratória, estatísticas e transformações com Python "
        "(pandas) no sandbox, a partir de arquivos CSV/JSON.",
        "parametro": "tarefa",
        "ferramentas": [
            "ler_arquivo", "listar_arquivos", "executar_comando",
            "comando_sandbox", "calculadora",
        ],
        "arq_limite": 1,
    },
    {
        "nome": "revisao",
        "descricao": "Delega uma revisão crítica ao subagente REVISOR. Use "
        "para revisar código, texto ou entrega contra critérios (segurança, "
        "testes, documentação, anti-alucinação) — segunda opinião do G3 sob "
        "demanda.",
        "parametro": "tarefa",
        "ferramentas": [
            "ler_arquivo", "listar_arquivos", "executar_comando",
            "calculadora",
        ],
        "arq_limite": 1,
    },
]


def _carregar_catalogo() -> list[dict]:
    """Lê `delegados.json` com fallback para o catálogo embutido.

    JSON ausente/corrompido/estrutura inválida → default + aviso (nunca
    quebra o grafo). Entradas inválidas são descartadas com aviso.
    """
    global AVISOS_CATALOGO
    AVISOS_CATALOGO = []
    try:
        dados = json.loads(_CAMINHO_CATALOGO.read_text(encoding="utf-8"))
        itens = dados.get("delegados") or []
    except Exception as exc:  # noqa: BLE001
        AVISOS_CATALOGO.append(f"catálogo de delegados indisponível ({exc}) — usando default")
        return list(_CATALOGO_PADRAO)
    if not isinstance(itens, list):
        AVISOS_CATALOGO.append("catálogo de delegados sem lista 'delegados' — usando default")
        return list(_CATALOGO_PADRAO)

    validos: list[dict] = []
    for i, d in enumerate(itens):
        if not isinstance(d, dict) or not d.get("nome"):
            AVISOS_CATALOGO.append(f"delegado #{i} ignorado (sem nome válido)")
            continue
        validos.append(
            {
                "nome": str(d["nome"]),
                "tool": (str(d["tool"]) if d.get("tool") else None),
                "descricao": str(d.get("descricao") or f"Delega ao subagente {d['nome']}."),
                "parametro": str(d.get("parametro") or "pergunta"),
                "ferramentas": list(d.get("ferramentas") or []),
                "arq_limite": max(1, int(d.get("arq_limite") or 1)),
            }
        )
    return validos or list(_CATALOGO_PADRAO)


def _registro_por_nome() -> dict[str, BaseTool]:
    """Registro das ferramentas disponíveis para pools, por nome."""
    from .ferramentas.basicas import ferramentas_basicas
    from .ferramentas.sistema import ferramentas_sistema
    from .recuperacao import pesquisar_memoria

    reg: dict[str, BaseTool] = {f.name: f for f in ferramentas_basicas()}
    reg.update({f.name: f for f in ferramentas_sistema()})
    reg["pesquisar_memoria"] = pesquisar_memoria
    reg.update(TOOLS_DELEGACAO)  # delegação aninhada explícita no catálogo
    return reg


def _resolver_pool(nomes: list[str]) -> list[BaseTool]:
    """Resolve os nomes do catálogo para tools reais (desconhecidas ignoradas)."""
    reg = _registro_por_nome()
    return [reg[n] for n in nomes if n in reg]


def _delegado_por_tool(nome_tool: str) -> dict | None:
    """Delegado do catálogo vigente cuja tool exposta tem `nome_tool`."""
    for d in _CATALOGO_ATUAL:
        if (d.get("tool") or f"delegar_{d['nome']}") == nome_tool:
            return d
    return None


# -- fábrica de tools de delegação ------------------------------------------

def _executar(nome: str, tarefa: str, contexto: str | None, _profundidade: int = 1) -> str:
    """Invoca um subagente registrado com a tarefa (e contexto opcional)."""
    grafo = SUBAGENTES_ATUAIS.get(nome)
    if grafo is None:
        return f"ERRO_FERRAMENTA: subagente '{nome}' não configurado."
    limite = ARQ_LIMITES.get(nome, 1)
    if _profundidade > limite:
        return (
            f"ERRO_FERRAMENTA: delegação aninhada bloqueada — arq_limite "
            f"{limite} do subagente '{nome}' excedido (profundidade {_profundidade})."
        )
    tarefa_final = tarefa
    if contexto:
        tarefa_final = f"{tarefa}\n\nContexto adicional:\n{contexto}"
    resultado = grafo.invoke({"mensagens": [HumanMessage(tarefa_final)]})
    return _resposta_final(resultado)


def _tool_delegacao(d: dict, _profundidade: int = 1) -> BaseTool:
    """Cria a tool `delegar_<nome>` para o delegado do catálogo.

    Assinatura pelo campo `parametro` do catálogo ('pergunta' ou 'tarefa'),
    sempre com `contexto` opcional — compatível com o comportamento histórico.
    `_profundidade` é a camada de aninhamento desta instância (núcleo = 1).
    O nome da tool vem do campo `tool` (ex.: delegado 'pesquisador' expõe a
    tool histórica 'delegar_pesquisa') — o decorator recebe o nome explícito,
    porque renomear a função depois não altera o nome interno da tool.
    """
    nome = d["nome"]
    nome_tool = d.get("tool") or f"delegar_{nome}"
    arg_pergunta = d.get("parametro") == "pergunta"

    if arg_pergunta:
        def _delegar(pergunta: str, contexto: str | None = None) -> str:
            """Delega ao subagente especialista."""
            return _executar(nome, pergunta, contexto, _profundidade)
    else:
        def _delegar(tarefa: str, contexto: str | None = None) -> str:
            """Delega ao subagente especialista."""
            return _executar(nome, tarefa, contexto, _profundidade)

    _delegar.__name__ = nome_tool
    _delegar.__doc__ = d["descricao"]
    return tool(nome_tool)(_delegar)


# -- subagente (subgrafo stateless) -----------------------------------------

def criar_subagente(
    nome: str,
    prompt: str,
    ferramentas: list,
    cfg: Config,
    llm,
    *,
    arq_limite: int = 1,
    profundidade: int = 1,
) -> Any:
    """Compila um subagente (subgrafo stateless) com o loop cognitivo do núcleo.

    `arq_limite` + `profundidade` implementam o anti-cascata do X1: tools de
    delegação no pool SÓ entram se a nova profundidade couber no limite
    (`profundidade + 1 <= arq_limite`); fora disso são omitidas — o subagente
    nem enxerga a opção de delegar.
    """
    pool: list[BaseTool] = []
    for f in ferramentas:
        nome_f = getattr(f, "name", "")
        if nome_f.startswith("delegar_"):
            alvo_d = _delegado_por_tool(nome_f)
            if alvo_d is not None and profundidade + 1 <= arq_limite:
                pool.append(
                    _tool_delegacao(
                        {**alvo_d, "descricao": f.description},
                        _profundidade=profundidade + 1,
                    )
                )
            # sem delegado no catálogo ou limite atingido → tool omitida
        else:
            pool.append(f)

    nos = fabricar_nos(
        llm, pool, store=None, cfg=cfg,
        prompt_fn=lambda *_args: prompt,
    )

    def rota_apos_agente(state: EstadoAegis) -> str:
        ultima = state["mensagens"][-1]
        if isinstance(ultima, AIMessage) and ultima.tool_calls:
            return "ferramentas"
        return END

    def rota_apos_ferramentas(state: EstadoAegis) -> str:
        ultima = state["mensagens"][-1]
        erro = isinstance(ultima, BaseMessage) and _eh_erro(ultima)
        tentativas = state.get("tentativas_correcao") or 0
        if erro and tentativas < cfg.max_tentativas_correcao:
            return "reflexao"
        return "agente"

    def rota_apos_reflexao(state: EstadoAegis) -> str:
        ultima = state["mensagens"][-1]
        if isinstance(ultima, AIMessage) and ultima.tool_calls:
            return "ferramentas"
        return END

    grafo = StateGraph(EstadoAegis)
    grafo.add_node("no_agente", nos["no_agente"])
    grafo.add_node("no_ferramentas", nos["no_ferramentas"])
    grafo.add_node("no_reflexao", nos["no_reflexao_auto_correcao"])

    grafo.add_edge(START, "no_agente")
    grafo.add_conditional_edges(
        "no_agente", rota_apos_agente, {"ferramentas": "no_ferramentas", END: END}
    )
    grafo.add_conditional_edges(
        "no_ferramentas", rota_apos_ferramentas,
        {"agente": "no_agente", "reflexao": "no_reflexao"},
    )
    grafo.add_conditional_edges(
        "no_reflexao", rota_apos_reflexao,
        {"ferramentas": "no_ferramentas", END: END},
    )
    return grafo.compile()


def configurar_subagentes(llm, cfg: Config) -> None:
    """Constrói e registra TODOS os delegados do catálogo (JSON → default)."""
    global TOOLS_DELEGACAO, _CATALOGO_ATUAL
    catalogo = _carregar_catalogo()
    _CATALOGO_ATUAL = catalogo

    SUBAGENTES_ATUAIS.clear()
    ARQ_LIMITES.clear()
    TOOLS_DELEGACAO = {}
    for d in catalogo:
        nome = d["nome"]
        pool = _resolver_pool(d["ferramentas"])
        grafo = criar_subagente(
            nome, _persona(nome), pool, cfg, llm,
            arq_limite=d["arq_limite"], profundidade=1,
        )
        SUBAGENTES_ATUAIS[nome] = grafo
        ARQ_LIMITES[nome] = d["arq_limite"]
        TOOLS_DELEGACAO[nome] = _tool_delegacao(d, _profundidade=1)

    # Compat: atributos do módulo usados por testes/imports antigos.
    _sincronizar_atributos()


def _sincronizar_atributos() -> None:
    """Expõe delegar_* como atributos do módulo (imports legados).

    Usa o NOME DA TOOL (ex.: 'delegar_redacao'), não o nome do delegado
    ('redator') — compat com o histórico.
    """
    for _nome, t in TOOLS_DELEGACAO.items():
        globals()[t.name] = t


def tools_delegacao() -> list[BaseTool]:
    """Todas as tools de delegação do catálogo (para o registro central)."""
    return list(TOOLS_DELEGACAO.values())


def _resposta_final(resultado: dict) -> str:
    """Extrai a última AIMessage com conteúdo (a resposta final do subagente)."""
    for m in reversed(resultado.get("mensagens") or []):
        if isinstance(m, AIMessage) and m.content:
            return str(m.content)
    return "(subagente não produziu resposta)"


# Popula o registro no import (catálogo default) — o ferramentas/__init__ o
# consome sem depender de `configurar_subagentes` (que roda no montar_grafo).
_CATALOGO_ATUAL = list(_CATALOGO_PADRAO)
for _d in _CATALOGO_PADRAO:
    TOOLS_DELEGACAO[_d["nome"]] = _tool_delegacao(_d, _profundidade=1)
_sincronizar_atributos()
