"""Multiagente do Aegis — orquestrador + especialistas paralelos + avaliador.

Fluxo (F2 — núcleo):
    no_orquestrador (classificador por regras, zero LLM)
        ├─ domínio ""      → fluxo legado de agente único (byte-idêntico)
        └─ domínio X       → subgrafo do domínio:
              no_fanout ──Send──▶ no_slot_0 (especialista 1)   ┐
                                  no_slot_1 (especialista 2)   ├─ paralelo real
                                  no_slot_2 (especialista 3)   ┘
                                       │ cada um com SUA pool de ferramentas
                                       ▼
              no_integrador (consolida os rascunhos em um artefato)
                                       ▼
              no_avaliador (veredito estruturado {status, nota, feedback})
                    ├─ aprovado                     → no_entrega → fim
                    └─ reprovado (≤ max_tentativas) → no_fanout (nova rodada)
                                                      com o feedback acumulado

Cada especialista é um mini-grafo LangGraph (agente→ferramentas→reflexão) com
prompt de persona e pool REDUZIDA de ferramentas — menos tokens de entrada por
nó (resposta mais rápida) e escopo mais seguro.

O fan-out usa a API `Send` (langgraph.types): paralelismo real do LangGraph,
mesmo na reaprovação. `max_especialistas` limita o fan-out.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph

from .config import Config
from .estado import EstadoAegis
from .ferramentas.pools import pool_da_lista
from .nos import fabricar_nos
from .prompts import sistema_avaliador, sistema_especialista, sistema_integrador
from .subagentes import criar_subagente, _resposta_final

# ---------------------------------------------------------------------
# Registro declarativo de domínios (mesmo espírito de extensions/plugins)
# ---------------------------------------------------------------------


@dataclass(frozen=True)
class Dominio:
    """Registro de um domínio multiagente."""

    nome: str
    gatilhos: tuple[str, ...]
    slots: tuple[tuple[str, str], ...]  # ((slot, papel), ...) — max_especialistas limita
    pool: str = "geral"


DOMINIOS: dict[str, Dominio] = {
    "programacao": Dominio(
        nome="programacao",
        gatilhos=(
            "programe", "programar", "implemente", "implementar", "desenvolva",
            "desenvolver", "app", "site", "script", "código", "codigo", "função",
            "funcionalidade", "html", "bug", "refatore", "refatorar", "api",
            "automação", "automatizar", "projeto",
        ),
        slots=(
            ("estrutura", "arquiteto de estrutura (arquivos, módulos, plano)"),
            ("implementacao", "implementador da lógica (funções, código)"),
            ("testes", "testador (valida, executa e cobre casos-limite)"),
        ),
        pool="programacao",
    ),
    "pesquisa": Dominio(
        nome="pesquisa",
        gatilhos=(
            "pesquise", "pesquisar", "pesquisa", "fontes", "literatura",
            "levantamento", "estado da arte", "comparar", "estudos",
        ),
        slots=(
            ("fontes", "coletor de fontes (busca web/papers)"),
            ("sintese", "sintetizador (cruza fontes e extrai evidências)"),
            ("verificacao", "verificador (checa citações e atribuição)"),
        ),
        pool="pesquisa",
    ),
    "escrita": Dominio(
        nome="escrita",
        gatilhos=(
            "escreva", "escrever", "redija", "redigir", "artigo", "relatório",
            "relatorio", "texto longo", "seção", "comunicado", "post",
        ),
        slots=(
            ("esqueleto", "arquiteto do texto (estrutura, títulos, extensão)"),
            ("redacao", "redator (escreve o corpo em pt-BR)"),
            ("revisao", "revisor (coesão, tom, repetições)"),
        ),
        pool="escrita",
    ),
    "obsidian": Dominio(
        nome="obsidian",
        gatilhos=(
            "nota", "notas", "obsidian", "vault", "liga", "ligar",
            "organize meus", "zettle",
        ),
        slots=(
            ("coleta", "coletor (cria/atualiza as notas-fonte)"),
            ("ligacao", "ligador (conecta notas por tags/links)"),
            ("organizacao", "organizador (índices, tags, estrutura)"),
        ),
        pool="obsidian",
    ),
    "memoria": Dominio(
        nome="memoria",
        gatilhos=(
            "memória", "memoria", "lembre", "lembrar", "fatos", "perfil",
            "preferência", "salve isso", "guarde",
        ),
        slots=(
            ("revisao", "revisor de preferências (o que é durável)"),
            ("fatos", "extrator de fatos (frases canônicas)"),
            ("consolidacao", "consolidador (funde com o perfil existente)"),
        ),
        pool="memoria",
    ),
}

# ---------------------------------------------------------------------
# Classificador + helpers puros (testáveis sem LLM)
# ---------------------------------------------------------------------

_DESEMPATE = list(DOMINIOS.keys())


def classificar_dominio(pergunta: str, *, limiar: int = 1) -> str:
    """Classifica a pergunta em um domínio por regras (zero LLM, rápido).

    Cada gatilho presente soma 1 ponto; vence o domínio com mais pontos.
    Empate → ordem declarada (programacao primeiro). 0 pontos → "" (agente
    único). O orquestrador LLM granular (divisão personalizada) fica para F3.
    """
    texto = (pergunta or "").lower()
    melhor: str = ""
    melhor_pontos = 0
    for nome in _DESEMPATE:
        dominio = DOMINIOS[nome]
        pontos = sum(1 for g in dominio.gatilhos if g in texto)
        if pontos > melhor_pontos:
            melhor, melhor_pontos = nome, pontos
    return melhor if melhor_pontos >= limiar else ""


def divisao_do_dominio(dominio: str, pergunta: str, max_especialistas: int) -> list[dict]:
    """Monta os slots do domínio (template determinístico)."""
    slots = DOMINIOS[dominio].slots[: max(1, max_especialistas)]
    return [
        {
            "slot": nome_slot,
            "tarefa": f"{pergunta}\n\nSua parte: {papel}.",
            "papel": papel,
            "estrategia": "paralelo",
            "status": "pendente",
        }
        for nome_slot, papel in slots
    ]


def parsear_veredito(texto: Any) -> dict | None:
    """Parse tolerante do JSON de veredito do avaliador (estilo APF)."""
    s = str(texto).strip()
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if not m:
        return None
    try:
        dados = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    status = str(dados.get("status", "")).strip().lower()
    if status not in {"aprovado", "reprovado"}:
        return None
    return {
        "status": status,
        "nota": float(dados.get("nota", 0) or 0),
        "confianca": float(dados.get("confianca", 0) or 0),
        "feedback": str(dados.get("feedback", "")).strip(),
        "criterios_checados": list(dados.get("criterios_checados") or []),
    }


def _ultima_pergunta(state: EstadoAegis) -> str:
    for m in reversed(state.get("mensagens") or []):
        if isinstance(m, HumanMessage):
            return str(m.content)
    return ""


# ---------------------------------------------------------------------
# Subgrafo do domínio (o "cérebro" multiagente)
# ---------------------------------------------------------------------

_ORQUESTRACOES: dict[str, Config] = {}


def _registrar_jsonl(cfg: Config, dominio: str, divisao: list[dict]) -> None:
    """Auditoria em config/dados/orquestracoes.jsonl (base para cache F3)."""
    try:
        cfg.orquestracoes_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cfg.orquestracoes_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "dominio": dominio,
                "slots": [d["slot"] for d in divisao],
            }, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 — auditoria nunca bloqueia o turno
        pass


def montar_subgrafo_dominio(dominio: str, llm, ferramentas: list,
                            cfg: Config) -> Any:
    """Compila o subgrafo stateless de um domínio (especialistas + avaliador).

    Estrutura:
        START → no_fanout (Send ×N) → no_slot_i (paralelo)
              → no_integrador → no_avaliador
                    → aprovado/limite → no_entrega → END
                    → reprovado       → no_fanout (com feedback acumulado)
    """
    slots = DOMINIOS[dominio].slots[: max(1, cfg.max_especialistas)]
    pool_ferramentas = pool_da_lista(ferramentas, DOMINIOS[dominio].pool)

    # mini-grafos especialistas (loop agente→ferramentas→reflexão, stateless)
    mini_grafos: dict[str, Any] = {}
    for idx, (nome_slot, papel) in enumerate(slots):
        mini = criar_subagente(
            f"especialista_{dominio}_{nome_slot}",
            sistema_especialista(dominio, nome_slot, papel),
            pool_ferramentas,
            cfg,
            llm,
        )
        mini_grafos[f"no_slot_{idx}"] = mini

    def no_fanout(state: EstadoAegis) -> dict:
        # Âncora do fan-out estático de 3 arestas paralelas (F2). O fan-out
        # DINÂMICO via `Send` fica para F3 (map-reduce por granularidade).
        return {}

    def _fazer_no_slot(idx: int):
        mini = mini_grafos[f"no_slot_{idx}"]

        def no_slot(state: EstadoAegis) -> dict:
            divisao = state.get("divisao") or []
            slot_info = divisao[idx] if idx < len(divisao) else None
            tarefa = slot_info["tarefa"] if slot_info else _ultima_pergunta(state)
            resultado = mini.invoke({"mensagens": [HumanMessage(tarefa)]})
            conteudo = _resposta_final(resultado)
            return {"rascunhos": {f"slot_{idx}": conteudo}}

        return no_slot

    def no_integrador(state: EstadoAegis) -> dict:
        rascunhos = state.get("rascunhos") or {}
        trecho = "\n\n".join(
            f"=== {slot} ===\n{rascunhos.get(f'slot_{i}', '(vazio)')}"
            for i, (slot, _) in enumerate(slots)
        )
        resp = llm.invoke([
            SystemMessage(sistema_integrador()),
            HumanMessage(
                f"Tarefa:\n{_ultima_pergunta(state)}\n\nRascunhos dos especialistas:\n{trecho}"
            ),
        ])
        return {"orquestracao_final": str(getattr(resp, "content", ""))}

    def no_avaliador(state: EstadoAegis) -> dict:
        tarefa = _ultima_pergunta(state)
        artefato = state.get("orquestracao_final") or "(sem artefato)"
        resp = llm.invoke([
            SystemMessage(sistema_avaliador(dominio)),
            HumanMessage(f"Tarefa:\n{tarefa}\n\nArtefato:\n{artefato}"),
        ])
        veredito = parsear_veredito(getattr(resp, "content", "")) or {
            "status": "reprovado",
            "nota": 0.0,
            "confianca": 0.0,
            "feedback": "avaliador não retornou JSON válido",
            "criterios_checados": [],
        }
        return {"vereditos": [veredito]}

    def no_entrega(state: EstadoAegis) -> dict:
        final = state.get("orquestracao_final") or "(multiagente não produziu resposta)"
        return {"mensagens": [AIMessage(content=final)]}

    def rota_apos_avaliador(state: EstadoAegis) -> str:
        vereditos = state.get("vereditos") or []
        if not vereditos:
            return "entrega"
        ultimo = vereditos[-1]
        tentativas = len(vereditos)
        if ultimo.get("status") == "aprovado":
            return "entrega"
        if tentativas < max(1, cfg.max_tentativas_correcao):
            return "especialistas"
        return "entrega"

    grafo = StateGraph(EstadoAegis)
    grafo.add_node("no_fanout", no_fanout)
    for i in range(len(slots)):
        grafo.add_node(f"no_slot_{i}", _fazer_no_slot(i))
    grafo.add_node("no_integrador", no_integrador)
    grafo.add_node("no_avaliador", no_avaliador)
    grafo.add_node("no_entrega", no_entrega)

    grafo.add_edge(START, "no_fanout")
    for i in range(len(slots)):
        grafo.add_edge("no_fanout", f"no_slot_{i}")
    for i in range(len(slots)):
        grafo.add_edge(f"no_slot_{i}", "no_integrador")
    grafo.add_edge("no_integrador", "no_avaliador")
    grafo.add_conditional_edges(
        "no_avaliador",
        rota_apos_avaliador,
        {"especialistas": "no_fanout", "entrega": "no_entrega"},
    )
    grafo.add_edge("no_entrega", END)
    return grafo.compile()


# ---------------------------------------------------------------------
# Orquestrador (nó de entrada)
# ---------------------------------------------------------------------


def montar_orquestrador(cfg: Config) -> dict[str, Any]:
    """Monta o nó orquestrador (classificação por regras) e sua rota."""

    def no_orquestrador(state: EstadoAegis) -> dict:
        pergunta = _ultima_pergunta(state)
        dominio = classificar_dominio(pergunta)
        if not dominio:
            return {"dominio": "", "divisao": []}
        divisao = divisao_do_dominio(dominio, pergunta, cfg.max_especialistas)
        _registrar_jsonl(cfg, dominio, divisao)
        return {"dominio": dominio, "divisao": divisao}

    def rota_apos_orquestrador(state: EstadoAegis) -> str:
        return "multi" if (state.get("dominio") or "") else "legado"

    return {"no_orquestrador": no_orquestrador, "rota_apos_orquestrador": rota_apos_orquestrador}


def montar_multiagente(cfg: Config) -> dict[str, Any]:
    """Monta orquestrador + rota multiagente para o wire do grafo principal.

    A rota mapeia o domínio decidido no turno para o nó de subgrafo nativo
    correspondente (`sub_<dominio>`, registrado pelo grafo.py via
    ``obter_subgrafo``); domínio vazio cai no fluxo legado de agente único.
    """
    orquestrador = montar_orquestrador(cfg)

    def rota_apos_orquestrador(state: EstadoAegis) -> str:
        dominio = state.get("dominio") or ""
        if dominio in DOMINIOS:
            return f"sub_{dominio}"
        return "legado"

    return {
        "no_orquestrador": orquestrador["no_orquestrador"],
        "rota_apos_orquestrador": rota_apos_orquestrador,
        "dominios": tuple(DOMINIOS.keys()),
    }


# Cache por domínio+modelo+config (evita recompilar a cada turno — velocidade).
# A chave inclui id(llm) e id(cfg): o subgrafo CAPTURA o modelo e a config nos
# closures (max_tentativas, pool...); reusá-lo com outro modelo/config vazaria
# estado entre grafos (bug real visto em testes: veredito do fake anterior).
_SUBGRAFOS: dict[tuple[str, int, int], Any] = {}


def obter_subgrafo(dominio: str, llm, ferramentas: list, cfg: Config) -> Any:
    """Compila (ou reusa) o subgrafo compilado de um domínio."""
    if dominio not in DOMINIOS:
        raise KeyError(f"domínio multiagente desconhecido: {dominio}")
    chave = (dominio, id(llm), id(cfg))
    if chave not in _SUBGRAFOS:
        _SUBGRAFOS[chave] = montar_subgrafo_dominio(dominio, llm, ferramentas, cfg)
    return _SUBGRAFOS[chave]