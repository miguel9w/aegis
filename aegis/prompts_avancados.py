"""
Formato de Prompt Avançado (APF) — aegis/prompts_avancados.py.

Um "prompt avançado" é uma ficha estruturada que compõe um bloco de prompt
completo (sistema + instruções + restrições + formato de saída + exemplos)
para ser injetado no prompt de sistema do agente.

Formato: JSON5-lite (extensão `.apf`) — JSON estrito com:
  - comentários de linha  ``//`` e ``#`` (fora de strings);
  - vírgulas pendentes (antes de ``}``/``]``);
  - variáveis ``${chave}`` interpoladas a partir do bloco ``variaveis``
    (o chamador pode sobrescrever via `extras`).

Esquema de uma ficha (config/prompts_avancados/*.apf):

    {
      "id": "revisor-codigo",        // obrigatório, único
      "versao": "1.0.0",             // opcional (default "1.0.0")
      "descricao": "Revisa código",  // opcional — aparece em /prompts
      "papel": "revisor",            // opcional — metadado (papel sugerido)
      "sistema": "Você é...",        // opcional — missão principal
      "instrucoes": ["...", "..."],  // opcional — regras numeradas
      "variaveis": {...},            // opcional — valores de ${...}
      "restricoes": ["..."],         // opcional
      "formato_saida": "markdown",   // opcional — str ou dict
      "exemplos": [{"entrada": "?", "saida": "?"}]  // opcional
    }

Exige pelo menos um de ``sistema`` ou ``instrucoes`` não vazio.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .config import config

# ---------------------------------------------------------------------------
# Erros e máquina de sanitização (JSON5-lite)
# ---------------------------------------------------------------------------


class PromptFormatoErro(ValueError):
    """Erro de formato ou uso dos prompts avançados (APF)."""


def sanitizar_json5(texto: str) -> str:
    """Remove comentários (`//`, `#`) e vírgulas pendentes fora de strings.

    Mantém intacto o conteúdo de qualquer string JSON (URLs com `//`/`#`,
    vírgulas literais etc.). Aceita JSON puro sem alteração.
    """
    saida: list[str] = []
    em_string = False
    escapado = False
    i = 0
    n = len(texto)
    while i < n:
        ch = texto[i]
        if em_string:
            saida.append(ch)
            if escapado:
                escapado = False
            elif ch == "\\":
                escapado = True
            elif ch == '"':
                em_string = False
            i += 1
            continue
        if ch == '"':
            em_string = True
            saida.append(ch)
            i += 1
            continue
        if ch == "#" or (ch == "/" and i + 1 < n and texto[i + 1] == "/"):
            # comentário até o fim da linha (fora de string)
            while i < n and texto[i] != "\n":
                i += 1
            continue
        if ch in "}]":
            # remove vírgula pendente (ex.: `"a": 1,}` -> `"a": 1}`)
            j = len(saida) - 1
            while j >= 0 and saida[j] in " \t\r\n":
                j -= 1
            if j >= 0 and saida[j] == ",":
                del saida[j:]
            saida.append(ch)
            i += 1
            continue
        saida.append(ch)
        i += 1
    return "".join(saida)


# ---------------------------------------------------------------------------
# Validação e normalização das fichas
# ---------------------------------------------------------------------------

_OBRIGATORIOS = ("id",)
_OPCIONAIS = (
    "versao", "descricao", "papel", "sistema", "instrucoes",
    "variaveis", "restricoes", "formato_saida", "exemplos",
)
_TIPOS = {
    "versao": str, "descricao": str, "papel": str, "sistema": str,
    "instrucoes": list, "variaveis": dict, "restricoes": list,
    "exemplos": list,
}


def _validar_ficha(ficha: dict, origem: Path) -> dict:
    """Valida e normaliza uma ficha bruta (do JSON). Erros viram PromptFormatoErro."""
    chaves_extra = sorted(set(ficha) - set(_OBRIGATORIOS) - set(_OPCIONAIS))
    if chaves_extra:
        raise PromptFormatoErro(
            f"{origem.name}: campos desconhecidos {', '.join(chaves_extra)}")

    falhas: list[str] = []
    for chave in _OBRIGATORIOS:
        if chave not in ficha:
            falhas.append(f"faltando '{chave}'")
    for chave, tipo in _TIPOS.items():
        if chave in ficha and not isinstance(ficha[chave], tipo):
            falhas.append(f"'{chave}' deve ser {tipo.__name__}")

    id_ = ficha.get("id", "")
    if id_ in (None, "") or not isinstance(id_, str):
        falhas.append("'id' deve ser uma string não vazia")

    sistema = ficha.get("sistema", "")
    instrucoes = ficha.get("instrucoes", [])
    if not (isinstance(sistema, str) and sistema.strip()) and not instrucoes:
        falhas.append("exige 'sistema' ou 'instrucoes' preenchidos")

    if falhas:
        raise PromptFormatoErro(f"{origem.name}: {'; '.join(falhas)}")

    # Normaliza defaults
    return {
        "id": str(id_),
        "versao": str(ficha.get("versao", "1.0.0")),
        "descricao": str(ficha.get("descricao", "")),
        "papel": str(ficha.get("papel", "")),
        "sistema": sistema or "",
        "instrucoes": list(instrucoes),
        "variaveis": dict(ficha.get("variaveis", {})),
        "restricoes": list(ficha.get("restricoes", [])),
        "formato_saida": ficha.get("formato_saida", ""),
        "exemplos": list(ficha.get("exemplos", [])),
    }


def _ler_ficha(arquivo: Path) -> dict:
    try:
        cru = arquivo.read_text(encoding="utf-8")
    except OSError as erro:
        raise PromptFormatoErro(f"{arquivo.name}: {erro}") from erro
    try:
        dados = json.loads(sanitizar_json5(cru))
    except json.JSONDecodeError as erro:
        raise PromptFormatoErro(
            f"{arquivo.name}: JSON inválido (linha {erro.lineno}: {erro.msg})"
        ) from erro
    if not isinstance(dados, dict):
        raise PromptFormatoErro(f"{arquivo.name}: a raiz deve ser um objeto JSON")
    return _validar_ficha(dados, arquivo)


# ---------------------------------------------------------------------------
# Catálogo (com cache por chamada — leitura de disco a cada invocação é barata)
# ---------------------------------------------------------------------------

_ERROS_CARGA: list[str] = []


def carregar_prompts_avancados() -> dict[str, dict]:
    """Carrega e valida todas as fichas `.apf` válidas do diretório config.

    Fichas com erro são ignoradas (não derrubam o agente) e os motivos ficam
    disponíveis em :func:`erros_de_carga`.
    """
    global _ERROS_CARGA  # noqa: PLW0603
    _ERROS_CARGA = []
    diretorio = Path(config.prompts_avancados_dir)
    if not diretorio.is_dir():
        return {}
    fichas: dict[str, dict] = {}
    for arquivo in sorted(diretorio.glob("*.apf")):
        try:
            ficha = _ler_ficha(arquivo)
        except PromptFormatoErro as erro:
            _ERROS_CARGA.append(str(erro))
            continue
        fichas[ficha["id"]] = ficha
    return fichas


def erros_de_carga() -> list[str]:
    """Motivos das fichas rejeitadas na última chamada de carga."""
    return list(_ERROS_CARGA)


def _ficha_por_id(nome: str) -> dict:
    fichas = carregar_prompts_avancados()
    ficha = fichas.get(nome) or fichas.get(nome.replace("-", "_")) or \
        fichas.get(nome.replace("_", "-"))
    if ficha is None:
        raise PromptFormatoErro(
            f"prompt avançado '{nome}' não encontrado (veja /prompts)")
    return ficha


# ---------------------------------------------------------------------------
# Interpolação e compilação
# ---------------------------------------------------------------------------

_REG_INTERPOLACAO = re.compile(r"\$\{([A-Za-z0-9_.]+)\}")


def _interpolar(texto: str, variaveis: dict) -> str:
    def _por_chave(m: re.Match) -> str:
        chave = m.group(1)
        if chave in variaveis:
            return str(variaveis[chave])
        return m.group(0)  # mantém ${...} sem valor
    return _REG_INTERPOLACAO.sub(_por_chave, texto)


def _formatar_variado(valor: Any, variaveis: dict) -> str:
    """Serializa `formato_saida` (str ou dict) já interpolado."""
    if isinstance(valor, str):
        return _interpolar(valor, variaveis)
    if isinstance(valor, dict):
        linhas = []
        for chave, item in valor.items():
            texto = _interpolar(str(item), variaveis) if isinstance(item, str) else str(item)
            linhas.append(f"- {chave}: {texto}")
        return "\n".join(linhas)
    return str(valor)


def compilar_prompt(nome: str, extras: dict | None = None) -> str:
    """Compila uma ficha em blocão final (prompt avançado injetável)."""
    ficha = _ficha_por_id(nome)
    variaveis = {**ficha["variaveis"], **(extras or {})}
    linhas: list[str] = [
        f"【Prompt Avançado: {ficha['id']} v{ficha['versao']}】"
    ]
    sistema = _interpolar(ficha["sistema"], variaveis)
    if sistema.strip():
        linhas.append(sistema)
    if ficha["instrucoes"]:
        linhas.append("Instruções:")
        for indice, instrucao in enumerate(ficha["instrucoes"], 1):
            linhas.append(f"{indice}. {_interpolar(str(instrucao), variaveis)}")
    if ficha["restricoes"]:
        linhas.append("Restrições:")
        for restricao in ficha["restricoes"]:
            linhas.append(f"- {_interpolar(str(restricao), variaveis)}")
    formato = _formatar_variado(ficha["formato_saida"], variaveis)
    if formato:
        linhas.append(f"Formato da saída:\n{formato}")
    if ficha["exemplos"]:
        linhas.append("Exemplos:")
        for exemplo in ficha["exemplos"]:
            entrada = _interpolar(str(exemplo.get("entrada", "")), variaveis)
            saida = _interpolar(str(exemplo.get("saida", "")), variaveis)
            linhas.append(f"- Entrada: {entrada}\n  Saída: {saida}")
    return "\n".join(linhas)


def listar_prompts() -> str:
    """Lista as fichas válidas (id, versão, descrição) + avisos de erro."""
    fichas = carregar_prompts_avancados()
    linhas = ["Prompts avançados (APF):"]
    if not fichas:
        linhas.append("  (nenhum prompt avançado em "
                      f"{config.prompts_avancados_dir})")
    for id_, ficha in sorted(fichas.items()):
        descricao = ficha["descricao"] or ficha["sistema"][:60]
        linhas.append(f"  - `{id_}` v{ficha['versao']} — {descricao}")
    if _ERROS_CARGA:
        plural = "" if len(_ERROS_CARGA) == 1 else "s"
        linhas.append(f"  ⚠ {len(_ERROS_CARGA)} ficha{plural} com erro:")
        for erro in _ERROS_CARGA:
            linhas.append(f"    - {erro}")
    return "\n".join(linhas)


def ver_prompt(nome: str) -> str:
    """Mostra o bloco compilado de um prompt, marcando o ativo."""
    try:
        bloco = compilar_prompt(nome)
    except PromptFormatoErro as erro:
        return str(erro)
    ativo = " (ativo)" if prompt_ativo_id() == nome else ""
    return f"Prompt avançado: `{nome}`{ativo}\n\n{bloco}"


# ---------------------------------------------------------------------------
# Ativação (persistida em config/dados/prompt_ativo.json)
# ---------------------------------------------------------------------------

def prompt_ativo_id() -> str | None:
    """Id do prompt avançado ativo, ou `None` se nenhum."""
    arquivo = Path(config.prompt_ativo_path)
    if not arquivo.is_file():
        return None
    try:
        dados = json.loads(arquivo.read_text(encoding="utf-8"))
        return str(dados.get("id", "")) or None
    except (OSError, ValueError):
        return None


def prompt_ativo_compilado() -> str:
    """Bloco compilado do prompt ativo; "" se nenhum/indisponível."""
    id_ = prompt_ativo_id()
    if not id_:
        return ""
    try:
        return compilar_prompt(id_)
    except PromptFormatoErro:
        return ""


def usar_prompt(nome: str) -> str:
    """Ativa um prompt avançado (persiste o id). `nenhum` desativa."""
    if nome in ("nenhum", "off", "desativar"):
        return desativar_prompt()
    _ficha_por_id(nome)  # valida existência (criação simples)
    arquivo = Path(config.prompt_ativo_path)
    arquivo.parent.mkdir(parents=True, exist_ok=True)
    arquivo.write_text(
        json.dumps({"id": nome}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return f"✅ prompt avançado '{nome}' ativo no sistema."


def desativar_prompt() -> str:
    """Desativa o prompt avançado atual."""
    arquivo = Path(config.prompt_ativo_path)
    if arquivo.is_file():
        arquivo.unlink()
    return "Prompts avançados desativado."


# ---------------------------------------------------------------------------
# Ferramentas LangChain (expostas ao agente)
# ---------------------------------------------------------------------------

from langchain_core.tools import tool


@tool
def listar_prompts_avancados() -> str:
    """Lista os prompts avançados (APF) disponíveis (id, versão, descrição)."""
    return listar_prompts()


@tool
def usar_prompt_avancado(nome: str) -> str:
    """Ativa um prompt avançado por id (nome "nenhum" desativa)."""
    return usar_prompt(nome)


@tool
def ver_prompt_avancado(nome: str) -> str:
    """Mostra o conteúdo compilado de um prompt avançado."""
    return ver_prompt(nome)