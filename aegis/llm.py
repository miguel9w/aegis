"""
Provedor Cognitivo (agnóstico de fornecedor).

Usa a abstração `ChatOpenAI` do LangChain configurada via variáveis de
ambiente, o que torna o motor compatível com DeepSeek, OpenRouter,
NVIDIA NIM e qualquer endpoint OpenAI-compatível com Function Calling.

Inclui tratamento resiliente de *rate limiting* / limites de cota com
backoff exponencial + jitter e respeito ao header `Retry-After`.
"""

from __future__ import annotations

import random
import time
from typing import Any, Callable, TypeVar

from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI

from .config import Config, ConfigError

T = TypeVar("T")


def criar_llm(config: Config = None, *, streaming: bool = True, **extra: Any) -> ChatOpenAI:
    """Cria um `ChatOpenAI` a partir da configuração (.env).

    `streaming=True` faz o modelo emitir eventos de token para a TUI
    (`astream_events`); `invoke()` ainda agrega a resposta completa.
    """
    config = config or _global_config()
    if not config.api_key:
        raise ConfigError(
            "OPENAI_API_KEY não definida. Copie .env.example para .env e preencha "
            "as credenciais (DeepSeek/OpenRouter)."
        )
    return ChatOpenAI(
        model=config.modelo,
        api_key=config.api_key,
        base_url=config.api_base,
        temperature=config.temperatura,
        max_tokens=config.max_tokens,
        timeout=config.timeout,
        streaming=streaming,
        **extra,
    )


def _global_config() -> Config:
    from .config import config  # import tardio p/ evitar ciclo
    return config


# ---------------------------------------------------------------------
# Resiliência a rate-limit / indisponibilidade transitória
# ---------------------------------------------------------------------

def _eh_erro_transitorio(exc: Exception) -> bool:
    """True para erros 429/5xx / de conexão — merecem retry com backoff."""
    nome = type(exc).__name__.lower()
    texto = str(exc).lower()
    # Exceções do SDK OpenAI (RateLimitError, APIConnectionError, ...)
    if "ratelimit" in nome or "apiconnectionerror" in nome or "apitimeouterror" in nome:
        return True
    if "internalservererror" in nome:
        return True
    # Códigos HTTP explícitos na mensagem
    for codigo in ("429", "500", "502", "503", "504"):
        if codigo in texto:
            return True
    return False


def _espera_retry(exc: Exception, base_espera: float = 2.0, tentativa: int = 0) -> float:
    """Calcula o backoff, respeitando `Retry-After` quando presente."""
    retry_after = getattr(exc, "headers", None) or {}
    if isinstance(retry_after, dict):
        ra = retry_after.get("retry-after")
        try:
            if ra is not None:
                return min(float(ra), 60.0)
        except (TypeError, ValueError):
            pass
    jitter = random.uniform(0, 0.5)
    return base_espera * (2 ** tentativa) + jitter


def com_retry(fn: Callable[[], T], *, tentativas: int = 4, base_espera: float = 2.0) -> T:
    """
    Executa `fn` com retry em erros transitórios (rate-limit / 5xx).

    Levanta o último erro caso ele não seja transitório ou as tentativas
    se esgotem — nunca mascara falhas reais.
    """
    ultimo: Exception | None = None
    for tentativa in range(tentativas):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — capturamos p/ inspecionar
            ultimo = exc
            if not _eh_erro_transitorio(exc) or tentativa == tentativas - 1:
                raise
            espera = _espera_retry(exc, base_espera, tentativa)
            time.sleep(espera)
    assert ultimo is not None
    raise ultimo


def invocar_com_retry(llm: BaseChatModel, mensagens: list, **kwargs: Any):
    """Wraper que chama o modelo com retry e isolamento de falhas de cota."""
    return com_retry(lambda: llm.invoke(mensagens, **kwargs))