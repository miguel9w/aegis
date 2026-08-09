"""Configuração compartilhada dos testes: modelo fake determinístico."""

from __future__ import annotations

import threading

from langchain_core.callbacks import CallbackManagerForLLMRun
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import BaseTool
from pydantic import PrivateAttr

# Lock GLOBAL do módulo: nós paralelos (fan-out multiagente) consomem cada
# resposta EXATAMENTE uma vez, em qualquer ordem de scheduling. Global em vez
# de por-instância porque locks não sobrevivem a deepcopy do pydantic.
_LOCK: threading.Lock = threading.Lock()


class ModeloFake(BaseChatModel):
    """ChatModel determinístico — respostas scriptadas, sem rede.

    Util para testar o roteamento do grafo sem depender de API.
    Rejeita SystemMessage de prova? não — ignora tudo e devolve a
    próxima resposta scriptada.
    Thread-safe: nós em paralelo (fan-out multiagente) consomem cada
    resposta EXATAMENTE uma vez, em qualquer ordem de scheduling.
    """

    _saidas: list = PrivateAttr(default_factory=list)
    _i: int = PrivateAttr(default=0)

    @property
    def _llm_type(self) -> str:
        return "fake-aegis"

    def _generate(
        self,
        messages: list,
        stop: list[str] | None = None,
        run_manager: CallbackManagerForLLMRun | None = None,
        **kwargs,
    ) -> ChatResult:
        with _LOCK:
            if not self._saidas:
                msg = AIMessage(content="")
            else:
                msg = self._saidas[min(self._i, len(self._saidas) - 1)]
                self._i += 1
        return ChatResult(generations=[ChatGeneration(message=msg)])

    def bind_tools(self, tools, **kwargs) -> "ModeloFake":
        # Mantém as respostas scriptadas; o roteamento é testado no grafo.
        return self

    def configurar(self, saidas: list[AIMessage]) -> None:
        """Define a sequência de respostas da conversa simulada."""
        self._saidas = list(saidas)
        self._i = 0


def chamada_tool(nome: str, args: dict, id_chamada: str = "call_0") -> AIMessage:
    """Cria um AIMessage com tool_call para rotear para no_ferramentas."""
    return AIMessage(
        content="",
        tool_calls=[{
            "name": nome,
            "args": args,
            "id": id_chamada,
            "type": "tool_call",
        }],
    )


def basico_tools() -> list[BaseTool]:
    from aegis.ferramentas.basicas import ferramentas_basicas
    return ferramentas_basicas()  # calculadora, hora_atual, buscar_web, executar_comando