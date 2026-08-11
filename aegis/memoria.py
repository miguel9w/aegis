"""
Persistência de memória: checkpointer SQLite + Store de longo prazo.

  - `SqliteSaver`: checkpoints por passo (retomada de conversas / múltiplos
    tópicos via `thread_id`), gravação local em `memoria_agente.db`.
  - `SqliteStore`: memória de longo prazo entre sessões (perfil do usuário,
    preferências), no mesmo banco.

Obs.: no LangGraph 1.x, `SqliteSaver.from_conn_string` retorna um context
manager. Para manter o saver vivo por toda a sessão (TUI/REPL), usamos o
construtor direto com uma `sqlite3.Connection` própria.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import ConfigError

from .config_json import carregar_config_json as _cfg_json

# Timeout de busy (ms) — configurável via config/dados/limites.json
_BUSY_TIMEOUT_MS = int(_cfg_json("limites.json", {"busy_timeout_ms": 2000})["busy_timeout_ms"])


# Cache de conexões por (caminho, componente) — SqliteSaver e SqliteStore
# usam conexões SEPARADAS: compartilhar a mesma conexão faz o checkpoint de
# um super-step commitar a transação do store ("cannot commit - no
# transaction is active"). WAL + busy_timeout resolvem a contenção.
_conexoes: dict[str, sqlite3.Connection] = {}


def _conexao(caminho: str | Path, rotulo: str = "") -> sqlite3.Connection:
    """Abre (e reutiliza) uma conexão SQLite persistente por componente.

    `rotulo` isola checkpointer ("ckpt") de store ("store") — cada um é dono
    da própria transação, eliminando o erro de transação aninhada sem
    reintroduzir 'database is locked' (WAL + busy_timeout).
    """
    chave = f"{caminho}::{rotulo}"
    conn = _conexoes.get(chave)
    if conn is not None:
        return conn

    Path(caminho).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(caminho), check_same_thread=False, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")   # leitura/escrita concorrentes
    conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")  # aguarda lock brevemente
    conn.execute("PRAGMA synchronous=NORMAL")
    _conexoes[chave] = conn
    return conn


def _setup(obj) -> None:
    """Chama `setup()` de forma síncrona (aceitando coroutine, se houver)."""
    met = getattr(obj, "setup", None)
    if callable(met):
        import asyncio
        try:
            resultado = met()
            if asyncio.iscoroutine(resultado):
                asyncio.run(resultado)
        except TypeError:  # assinatura diferente no futuro
            pass


def criar_checkpointer_sync(caminho: str | Path):
    """Checkpointer síncrono persistente (CLI/headless e testes)."""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ImportError as exc:  # pragma: no cover
        raise ConfigError(
            "dependência langgraph-checkpoint-sqlite ausente. Execute: pixi install"
        ) from exc
    saver = SqliteSaver(conn=_conexao(caminho, "ckpt"))
    _setup(saver)
    return saver


async def criar_checkpointer_async(caminho: str | Path):
    """Checkpointer assíncrono (TUI — necessário para `astream_events`).

    Nota: langgraph 1.x exige AsyncSqliteSaver (mais aiosqlite) para
    streaming assíncrono. Deve ser construído dentro de um event loop.
    """
    try:
        import aiosqlite
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    except ImportError as exc:  # pragma: no cover
        raise ConfigError(
            "dependência aiosqlite ausente. Execute: pixi install" 
        ) from exc
    conn = await aiosqlite.connect(str(caminho))
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    saver = AsyncSqliteSaver(conn=conn)
    await saver.setup()
    return saver


def criar_store_sync(caminho: str | Path):
    """Store de longo prazo síncrona persistente."""
    try:
        from langgraph.store.sqlite import SqliteStore
    except ImportError:  # pragma: no cover
        from langgraph.store.memory import InMemoryStore  # fallback simples
        return InMemoryStore()
    store = SqliteStore(conn=_conexao(caminho, "store"))
    _setup(store)
    return store


# ---------------------------------------------------------------------
# Namespaces da Store (convenção do Aegis)
# ---------------------------------------------------------------------

def namespace_perfil() -> tuple[str, ...]:
    """Namespace global do perfil do usuário (entre TODAS as sessões)."""
    return ("aegis", "perfil")


def namespace_memoria(thread_id: str) -> tuple[str, ...]:
    """Namespace de memória por tópico/conversa."""
    return ("aegis", "memoria", thread_id)


def namespace_licoes() -> tuple[str, ...]:
    """Namespace das lições aprendidas (memória procedimental global)."""
    return ("aegis", "licoes")


def namespace_resumos(thread_id: str) -> tuple[str, ...]:
    """Namespace dos resumos incrementais por sessão (C4)."""
    return ("aegis", "resumos", thread_id)


def namespace_decisoes(thread_id: str) -> tuple[str, ...]:
    """Namespace das decisões-chave por sessão (C4)."""
    return ("aegis", "decisoes", thread_id)


def namespace_uat(projeto: str) -> tuple[str, ...]:
    """UAT por PROJETO (não thread): sobrevive a `/clear` e a troca de sessão."""
    return ("aegis", "uat", projeto or "default")