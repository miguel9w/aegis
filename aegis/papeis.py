"""
Papéis (role-playing) estilo CAMEL — personas que mudam identidade e foco.

Inspirado no role-playing do CAMEL (dupla de papéis com instruções), o papel
ativo é persistido em `config/dados/papel_ativo.json` e injetado no prompt de
sistema, junto com a **tarefa especificada** (`config/dados/tarefa_atual.json`):

  - `carregar_papeis`: catálogo = padrões de código + extensões de papeis.json.
  - `resolver_papel`: valida nome contra o catálogo (case-insensitive).
  - Ferramentas: `definir_papel`, `ver_papel`, `listar_papeis`.
  - Especificação de tarefa: `especificar_tarefa`, `estruturar_tarefa`.
  - `montar_bloco_personalidade`: bloco injetado no `sistema()` (prompts.py).
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from .config import config

IDENTIDADE_PADRAO = (
    "Você é o **Aegis**, um agente pessoal autônomo de última geração. "
    "Você é proativo, preciso e determinístico. Responda em português do Brasil."
)


@dataclass
class Papel:
    """Persona configurável: nome, descrição, identidade, instruções e foco."""

    nome: str
    descricao: str = ""
    identidade: str = IDENTIDADE_PADRAO
    instrucoes: str = ""
    ferramentas_focadas: list[str] = field(default_factory=list)


PAPEIS_PADRAO: list[Papel] = [
    Papel(
        nome="assistente",
        descricao="Papel padrão — assistente pessoal autônomo, proativo e preciso.",
        identidade=IDENTIDADE_PADRAO,
        instrucoes="Ajude com qualquer tarefa, priorizando precisão e clareza.",
    ),
    Papel(
        nome="pesquisador",
        descricao="Especialista em pesquisa profunda com evidências e fontes.",
        identidade="Você é o subagente PESQUISADOR do Aegis, especialista em pesquisa.",
        instrucoes=(
            "Baseie a resposta em evidências e cite brevemente as fontes; se a "
            "pergunta exigir múltiplas perspectivas, faça mais de uma busca; "
            "responda em português (pt-BR), de forma concisa e direta."
        ),
        ferramentas_focadas=["buscar_web", "calculadora", "pesquisar_memoria"],
    ),
    Papel(
        nome="redator",
        descricao="Especialista em escrita longa e estruturada.",
        identidade="Você é o subagente REDATOR do Aegis, especialista em escrita.",
        instrucoes=(
            "Produza texto longo e bem estruturado (títulos, listas, parágrafos), "
            "em português (pt-BR), com tom profissional e coeso. Siga os "
            "requisitos de formato, extensão e público dados na tarefa."
        ),
        ferramentas_focadas=["escrever_arquivo", "ler_arquivo"],
    ),
    Papel(
        nome="planejador",
        descricao="Especialista em planejamento e decomposição de tarefas.",
        identidade="Você é o subagente PLANEJADOR do Aegis, especialista em planos.",
        instrucoes=(
            "Decomponha objetivos em passos claros e ordenados usando as "
            "ferramentas de plano; acompanhe o status de cada passo. "
            "Responda em português (pt-BR)."
        ),
        ferramentas_focadas=["planejar_tarefa", "atualizar_plano", "ver_plano"],
    ),
]


def _copiar_padrao() -> list[Papel]:
    """Cópia profunda dos papéis padrão (para não mutar o catálogo original)."""
    return [Papel(**vars(p)) for p in PAPEIS_PADRAO]


def carregar_papeis(caminho: str | Path | None = None) -> list[Papel]:
    """Carrega o catálogo de papéis: padrões + extensões de `papeis.json`.

    - `"substituir_padrao": true` → usa SOMENTE os papéis do JSON.
    - Papéis do JSON com o mesmo nome dos padrões os sobrescrevem.
    - Arquivo ausente/inválido → padrões (fallback seguro).
    """
    papeis = _copiar_padrao()
    try:
        alvo = Path(caminho) if caminho is not None else Path(config.papeis_config_path)
    except Exception:  # noqa: BLE001
        return papeis
    try:
        if not alvo.is_file():
            return papeis
        with alvo.open(encoding="utf-8") as fh:
            dados = json.load(fh)
        if not isinstance(dados, dict):
            return papeis
        lista = dados.get("papeis")
        if not isinstance(lista, list) or not lista:
            return papeis
        nomes_json = {
            str(x.get("nome", "")).strip().lower()
            for x in lista
            if isinstance(x, dict) and str(x.get("nome", "")).strip()
        }
        if dados.get("substituir_padrao"):
            papeis = []
        else:
            papeis = [p for p in papeis if p.nome.lower() not in nomes_json]
        for x in lista:
            if not isinstance(x, dict) or not str(x.get("nome", "")).strip():
                continue
            papeis.append(Papel(
                nome=str(x["nome"]).strip(),
                descricao=str(x.get("descricao") or ""),
                identidade=str(x.get("identidade") or IDENTIDADE_PADRAO),
                instrucoes=str(x.get("instrucoes") or ""),
                ferramentas_focadas=[str(f) for f in (x.get("ferramentas_focadas") or [])],
            ))
    except Exception:  # noqa: BLE001 — catálogo nunca derruba o agente
        return _copiar_padrao()
    return papeis


def resolver_papel(nome: str, papeis: list[Papel] | None = None) -> Papel:
    """Resolve `nome` (case-insensitive) contra o catálogo de papéis."""
    if papeis is None:
        papeis = carregar_papeis()
    alvo = str(nome or "").strip().lower()
    for p in papeis:
        if p.nome.lower() == alvo:
            return p
    disponiveis = ", ".join(p.nome for p in papeis) or "(nenhum)"
    raise ValueError(f"papel '{nome}' não encontrado. Disponíveis: {disponiveis}")


# --------------------------------------------------------------------------
# Persistência do papel ativo e da tarefa especificada
# --------------------------------------------------------------------------

def _carregar_estado(caminho: str | Path | None) -> Any:
    """JSON de estado (papel_ativo/tarefa_atual) com fallback a `None`."""
    try:
        alvo = Path(caminho) if caminho is not None else None
        if alvo is None or not alvo.is_file():
            return None
        with alvo.open(encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:  # noqa: BLE001 — estado auxiliar nunca quebra
        return None


def _agora() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def ler_papel_ativo(caminho: str | Path | None = None) -> str | None:
    """Nome do papel ativo (None se nenhum)."""
    dados = _carregar_estado(caminho if caminho is not None else config.papel_ativo_path)
    if isinstance(dados, dict) and dados.get("nome"):
        return str(dados["nome"])
    return None


def _salvar_papel_ativo(nome: str) -> None:
    alvo = Path(config.papel_ativo_path)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    dados = {"nome": nome, "ts": _agora()}
    alvo.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


def ler_tarefa_atual(caminho: str | Path | None = None) -> dict[str, Any] | None:
    """Tarefa especificada em `tarefa_atual.json` (None se ausente)."""
    dados = _carregar_estado(caminho if caminho is not None else config.tarefa_atual_path)
    return dados if isinstance(dados, dict) else None


def _salvar_tarefa_atual(tarefa: dict[str, Any]) -> None:
    alvo = Path(config.tarefa_atual_path)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    alvo.write_text(json.dumps(tarefa, ensure_ascii=False, indent=2), encoding="utf-8")


def montar_bloco_personalidade() -> str:
    """Bloco injetável no sistema: papel ativo + tarefa especificada (vazio "")."""
    partes: list[str] = []
    nome = ler_papel_ativo()
    if nome:
        try:
            papel = resolver_papel(nome)
            partes.append(
                "## Papel ativo\n"
                f"nome: **{papel.nome}** — {papel.descricao}\n"
                f"identidade: {papel.identidade}"
                + (f"\ninstruções: {papel.instrucoes}" if papel.instrucoes else "")
            )
        except ValueError:
            partes.append(f"## Papel ativo\n{nome} (não encontrado no catálogo)")
    tarefa = ler_tarefa_atual()
    if tarefa and tarefa.get("objetivo"):
        partes.append("## Tarefa especificada\n" + _formatar_tarefa(tarefa))
    return "\n\n".join(partes)


def _formatar_tarefa(tarefa: dict[str, Any]) -> str:
    linhas = [f"- objetivo: {tarefa['objetivo']}"]
    if tarefa.get("restricoes"):
        linhas.append(f"- restrições: {tarefa['restricoes']}")
    if tarefa.get("criterios"):
        linhas.append(f"- critérios de sucesso: {tarefa['criterios']}")
    return "\n".join(linhas)


# --------------------------------------------------------------------------
# Ferramentas — registradas em aegis/ferramentas/__init__.py
# --------------------------------------------------------------------------

@tool
def definir_papel(nome: str) -> str:
    """Define o papel ativo do agente (ex.: pesquisador, redator, planejador). Retorna a identidade ativada."""
    papel = resolver_papel(nome)
    _salvar_papel_ativo(papel.nome)
    return f"Papel ativo: **{papel.nome}** — {papel.descricao}\n\n{papel.identidade}"


@tool
def ver_papel() -> str:
    """Mostra o papel ativo do agente e sua identidade/instruções."""
    nome = ler_papel_ativo()
    if not nome:
        return "Nenhum papel ativo — usando a identidade padrão (assistente)."
    papel = resolver_papel(nome)
    blocos = [f"Papel ativo: **{papel.nome}** — {papel.descricao}", papel.identidade]
    if papel.instrucoes:
        blocos.append(papel.instrucoes)
    return "\n\n".join(blocos)


@tool
def listar_papeis() -> str:
    """Lista todos os papéis disponíveis no catálogo."""
    linhas = [f"- `{p.nome}` — {p.descricao}" for p in carregar_papeis()]
    return "\n".join(linhas) or "(nenhum papel)"


@tool
def especificar_tarefa(objetivo: str, restricoes: str = "", criterios: str = "") -> str:
    """Especifica uma TAREFA formal para o agente executar (objetivo + restrições + critérios de sucesso)."""
    if not str(objetivo or "").strip():
        raise ValueError("objetivo é obrigatório")
    tarefa = {
        "id": uuid.uuid4().hex[:8],
        "objetivo": objetivo.strip(),
        "restricoes": (restricoes or "").strip(),
        "criterios": (criterios or "").strip(),
        "ts": _agora(),
    }
    _salvar_tarefa_atual(tarefa)
    return "Tarefa especificada:\n" + _formatar_tarefa(tarefa)


@tool
def estruturar_tarefa(texto_livre: str) -> str:
    """Converte descrição livre em tarefa estruturada (objetivo; restrições; critérios)."""
    objetivo, restricoes, criterios = _parsear_texto_tarefa(texto_livre)
    if not objetivo:
        raise ValueError("não foi possível extrair um objetivo do texto")
    tarefa = {
        "id": uuid.uuid4().hex[:8],
        "objetivo": objetivo,
        "restricoes": restricoes,
        "criterios": criterios,
        "ts": _agora(),
    }
    _salvar_tarefa_atual(tarefa)
    return "Tarefa estruturada:\n" + _formatar_tarefa(tarefa)


def _parsear_texto_tarefa(texto: str) -> tuple[str, str, str]:
    """Heurística determinística: objetivo na 1ª parte; restrições/critérios por marcadores."""
    linhas = [ln.strip() for ln in texto.replace(";", "\n").splitlines() if ln.strip()]
    objetivo: str = ""
    restricoes: list[str] = []
    criterios: list[str] = []
    for ln in linhas:
        baixo = ln.lower()
        sem_marcador = ln.lstrip("-*• ").strip()
        if not objetivo and not ln.startswith(("-", "*", "•")):
            objetivo = sem_marcador
            continue
        if ln.startswith(("-", "*", "•")) and "criterio" in baixo or "critério" in baixo:
            criterios.append(sem_marcador)
        elif baixo.startswith(("restri", "restri")) or ln.startswith(("-", "*", "•")):
            restricoes.append(sem_marcador)
        elif "criterio" in baixo or "critério" in baixo:
            criterios.append(sem_marcador)
        else:
            restricoes.append(sem_marcador)
    return (
        objetivo.strip(),
        "; ".join(restricoes)[:600],
        "; ".join(criterios)[:600],
    )