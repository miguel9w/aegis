"""
Memória explícita e curada — porta da ferramenta `memory_tool.py` do Hermes
Agent (Nous Research) para o Aegis.

O Hermes expõe UM `memory` (com ação add/replace/remove) sobre dois arquivos
duratáveis (`MEMORY.md` para o agente, `USER.md` para o perfil do usuário),
injetados no prompt no início da sessão.

Aqui o equivalente usa a mesma Store de longo prazo que o `pesquisar_memoria`
(recall RAG-lite) já lê, garantindo que tudo que o agente grava de forma
explícita é imediatamente recuperável:

  - alvo "memoria"  -> namespace ("aegis", "fatos")  (notas do agente)
  - alvo "perfil"   -> namespace ("aegis", "perfil") (fatos sobre o usuário)

Ferramenta única `gerenciar_memoria(acao, ...)` com ação "salvar" | "esquecer"
| "listar" — cada salvamento é durável no banco; "esquecer" remove por
substring na chave (como o replace/remove por substring do Hermes).
"""

from __future__ import annotations

import time
from typing import Any

from langchain_core.tools import tool

NS_FATOS = ("aegis", "fatos")
NS_PERFIL = ("aegis", "perfil")

# Store de longo prazo injetada em montar_grafo (restrito ao validate)
STORE_ATUAL: Any = None


def definir_store(store: Any) -> None:
    """Vincula a Store de longo prazo à ferramenta de memória explícita."""
    global STORE_ATUAL
    STORE_ATUAL = store


def _fatos_todos() -> list[str]:
    """Chaves dos fatos gravados em ("aegis", "fatos")."""
    if STORE_ATUAL is None:
        return []
    try:
        return [i.key for i in STORE_ATUAL.search(NS_FATOS, limit=100)]
    except Exception:  # noqa: BLE001
        return []


@tool
def gerenciar_memoria(acao: str, conteudo: str = "", chave: str = "", alvo: str = "memoria") -> str:
    """
    Grava, esquece ou lista memória de longo prazo de forma EXPLÍCITA e durável.

    - acao: "salvar" grava um fato; "esquecer" remove (por chave/substring de conteúdo);
    "listar" lista o alvo.
    - conteudo: o fato a salvar (obrigatório em "salvar").
    - chave: rótulo opcional do fato (auto-gerado se vazio).
    - alvo: "memoria" (notas do agente) ou "perfil" (fatos do usuário, fundidos em dict).

    Use "salvar" para registrar preferências, decisões e fatos estáveis que devem
    sobreviver além desta conversa. Use "esquecer" para remover informação obsoleta.
    """
    if STORE_ATUAL is None:
        return "Memória indisponível: a Store de longo prazo não foi vinculada."
    acao = acao.strip().lower()

    if acao == "salvar":
        return _salvar(conteudo, chave, alvo)
    if acao == "esquecer":
        return _esquecer(conteudo, chave, alvo)
    if acao == "listar":
        return _listar(alvo)
    return f"Ação inválida '{acao}'. Use salvar, esquecer ou listar."


def _salvar(conteudo: str, chave: str, alvo: str) -> str:
    texto = conteudo.strip()
    if not texto:
        return "Nada a salvar: forneça 'conteudo'."
    if alvo == "perfil":
        return _salvar_perfil(conteudo, chave)
    chave = chave.strip() or f"fato-{int(time.time())}"
    STORE_ATUAL.put(NS_FATOS, chave, {"conteudo": texto, "ts": time.strftime("%Y-%m-%d %H:%M")})
    return f"Memória salva [{chave}] ({alvo})."


def _salvar_perfil(conteudo: str, chave: str) -> str:
    item = STORE_ATUAL.get(NS_PERFIL, "perfil")
    dados = dict(item.value) if item and item.value else {}
    rotulo = chave.strip() or f"fato_{int(time.time())}"
    dados[rotulo] = conteudo
    STORE_ATUAL.put(NS_PERFIL, "perfil", dados)
    return f"Perfil atualizado ({rotulo})."


def _esquecer(conteudo: str, chave: str, alvo: str) -> str:
    alvo_ns = NS_PERFIL if alvo == "perfil" else NS_FATOS
    alvo = alvo or "memoria"
    if alvo == "perfil":
        item = STORE_ATUAL.get(NS_PERFIL, "perfil")
        dados = dict(item.value) if item and item.value else {}
        alvos_remover = [k for k in dados if not chave or chave in k]
        if not alvos_remover:
            return "Nenhum fato do perfil corresponde a essa chave."
        for k in alvos_remover:
            dados.pop(k, None)
        STORE_ATUAL.put(NS_PERFIL, "perfil", dados)
        return f"Esquecidos do perfil: {', '.join(alvos_remover)}."

    removidos = 0
    for ch in _fatos_todos():
        # remove por chave-substring (Hermes usa substring matching p/ replace/remove)
        if chave and chave in ch:
            STORE_ATUAL.delete(NS_FATOS, ch)
            removidos += 1
        elif not chave:
            valor = STORE_ATUAL.get(NS_FATOS, ch)
            texto = (valor.value.get("conteudo") or "") if valor and isinstance(valor.value, dict) else ""
            if conteudo and conteudo in texto:
                STORE_ATUAL.delete(NS_FATOS, ch)
                removidos += 1
    return f"Esquecidos: {removidos} fato(s) de memória."


def _listar(alvo: str) -> str:
    if alvo == "perfil":
        item = STORE_ATUAL.get(NS_PERFIL, "perfil")
        if not item or not item.value:
            return "Perfil vazio."
        dados = item.value if isinstance(item.value, dict) else {}
        return "Perfil do usuário:\n" + "\n".join(f"- {k}: {v}" for k, v in dados.items())
    fatos = _fatos_todos()
    if not fatos:
        return "Nenhuma memória explícita gravada."
    linhas = []
    for ch in fatos:
        valor = STORE_ATUAL.get(NS_FATOS, ch)
        conteudo = (valor.value.get("conteudo") if valor and isinstance(valor.value, dict) else str(valor.value)) if valor else ""
        linhas.append(f"- {ch}: {conteudo}")
    return "Memória explícita:\n" + "\n".join(linhas)