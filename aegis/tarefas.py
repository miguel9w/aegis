"""
Planejamento & acompanhamento de tarefas (Todo) — porta da ferramenta
`todo_tool.py` do Hermes Agent (Nous Research) para o Aegis.

Mesma ideia do Hermes: uma lista de tarefas que o agente usa para decompor
tarefas complexas, acompanhar progresso e manter foco em conversas longas.

  - Uma única ferramenta ``tarefas``: informe ``tarefas`` para ESCREVER (lista de
    itens); omita para LER a lista atual. Toda chamada devolve a lista completa.
  - Statuses: ``pendente | executando | concluida | cancelada``.
  - Limites (como no Hermes) coibem que um item averso infle a re-injeção pós
    compressão: conteúdo máx 4000 chars/item e máx 256 itens.
  - Re-injeção: após uma compressão de contexto, as tarefas ativas (pendentes/
    executando) são anexadas ao resumo para o agente não perder o foco.
  - Persistência opcional em JSON (config/dados/tarefas.json) para sobreviver
    entre invocações do grafo (o Hermes mantém em memória; o Aegis também,
    gravando em disco se houver caminho configurado).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

VALIDOS = {"pendente", "executando", "concluida", "cancelada"}
# Limites configuráveis via config/dados/tarefas_config.json
from .config_json import carregar_config_json as _ccj

_TAREFAS_CFG = _ccj("tarefas_config.json", {"limite_conteudo": 4000, "limite_itens": 256})
LIMITE_CONTEUDO = int(_TAREFAS_CFG["limite_conteudo"])  # chars por item (como Hermes)
LIMITE_ITENS = int(_TAREFAS_CFG["limite_itens"])        # máx de itens (como Hermes)
_CABECALHO_REINJECAO = "[Sua lista de tarefas ativa foi preservada na compressão de contexto]"


class TarefasStore:
    """Lista de tarefas ordenada por prioridade (posição = prioridade)."""

    def __init__(self, caminho: str | None = None) -> None:
        self.itens: list[dict[str, str]] = []
        self.caminho = caminho
        if caminho:
            self._carregar()

    # --- persistência -------------------------------------------------------
    def _carregar(self) -> None:
        if not self.caminho or not Path(self.caminho).exists():
            return
        try:
            with Path(self.caminho).open(encoding="utf-8") as fh:
                dados = json.load(fh)
            if not isinstance(dados, list):
                return
            normalizados = []
            for item in dados:
                if not isinstance(item, dict):
                    continue
                normalizados.append({
                    "id": str(item.get("id") or "?"),
                    "conteudo": str(item.get("conteudo") or "")[:LIMITE_CONTEUDO],
                    "status": item.get("status") if item.get("status") in VALIDOS else "pendente",
                })
            self.itens = normalizados
        except Exception:  # noqa: BLE001
            self.itens = []

    def _salvar(self) -> None:
        if not self.caminho:
            return
        Path(self.caminho).parent.mkdir(parents=True, exist_ok=True)
        with Path(self.caminho).open("w", encoding="utf-8") as fh:
            json.dump(self.itens, fh, ensure_ascii=False, indent=2)

    # --- operações ------------------------------------------------------------
    def escrever(self, itens: list[dict[str, Any]] | None, merge: bool = False) -> list[dict[str, str]]:
        """Substitui (ou faz merge na) da lista. Cada item: id, conteudo, status."""
        if itens:
            if not merge:
                self.itens = []
            for item in itens:
                if not isinstance(item, dict):
                    continue
                if len(self.itens) >= LIMITE_ITENS:
                    break
                _id = str(item.get("id") or f"tarefa-{len(self.itens) + 1}")
                _conteudo = str(item.get("conteudo") or "")[:LIMITE_CONTEUDO]
                _status = item.get("status") if item.get("status") in VALIDOS else "pendente"
                self._atualizar_item(_id, _conteudo, _status)
            self._salvar()
        return self.listar()

    def listar(self) -> list[dict[str, str]]:
        return list(self.itens)

    def ativas(self) -> list[dict[str, str]]:
        """Pendente/executando (para re-injeção pós compressão)."""
        return [i for i in self.itens if i["status"] in ("pendente", "executando")]

    def formato_para_reinjecar(self) -> str:
        """Bloco a anexar após uma compressão; vazio se não há ativas."""
        ativas = self.ativas()
        if not ativas:
            return ""
        corpo = "; ".join(f"[{i['status']}] {i['conteudo'][:120]}" for i in ativas)
        return f"{_CABECALHO_REINJECAO}\n{corpo}"

    def limpar(self) -> None:
        self.itens = []
        self._salvar()

    # --- convenções internas -------------------------------------------------
    def _atualizar_item(self, item_id: str, conteudo: str, status: str) -> None:
        """Insere novo item ou atualiza o existente (reseta status se conteúdo mudou)."""
        for i in self.itens:
            if i["id"] == item_id:
                if i["conteudo"] != conteudo:
                    i["status"] = status
                return
        self.itens.append({"id": item_id, "conteudo": conteudo, "status": status})


# Singletons (por processo). Usados pela ferramenta e pela re-injeção pós compressão.
_STORE_PADRAO: TarefasStore | None = None


def _obter_store() -> TarefasStore:
    global _STORE_PADRAO
    if _STORE_PADRAO is None:
        from .config import config
        _STORE_PADRAO = TarefasStore(caminho=str(getattr(config, "tarefas_path", None)))
    return _STORE_PADRAO


def resumo_ativo_para_reinjecao() -> str:
    """Export para o nó de compressão (os.py). Retorna '' quando não há ativas."""
    if _STORE_PADRAO is None:
        return ""
    return _STORE_PADRAO.formato_para_reinjecar()


@tool
def tarefas(tarefas: list[dict[str, Any]] | None = None) -> str:
    """
    Lista de tarefas do agenâte (planejamento e acompanhamento de progresso).

    Use para decompor uma tarefa complexa em passos rastreáveis, marcar o que já
    terminou e retomar o que ficou parado. A lista sobrevive a compressões de
    contexto (re-injetada no resumo).

    - Para ESCREVER: informe 'tarefas', uma lista de itens com 'id' (texto curto),
      'conteudo' (descrição), 'status' (uma de: pendente, executando, concluida,
      cancelada). Itens sem 'status' ficam 'pendente'.
    - Para LER: omita o argumento. A chamada sempre devolve a lista atual completa.

    Args:
        tarefas: lista opcional de itens a gravar (substitui a atual).

    Returns:
        Lista formatada em linhas, com os itens e statuses.
    """
    if tarefas is not None:
        _obter_store().escrever(tarefas)
    lista = _obter_store().listar()
    if not lista:
        return "Lista de tarefas vazia."
    return "\n".join(f"- [{i['status']}] ({i['id']}) {i['conteudo']}" for i in lista)