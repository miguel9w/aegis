"""
Memória pontuada estilo CAMEL — registros com *importance score* heurístico.

Diferente da memória de perfil (`memoria_tool.py`), aqui cada registro tem
`importancia` (0–10) e um timestamp, e a **recuperação** ranqueia os registros
por uma pontuação combinada (inspirada no CAMEL):

    pontuação = recência × peso + importância × peso + overlap lexical

  - recência: decaimento exponencial com meia-vida (dias configurável);
  - importância: `peso_importancia * (importancia / 10)`;
  - overlap: fração dos tokens da consulta presentes no registro.

Parâmetros (peso_importancia, meia_vida_dias, k_padrao, n_max_registros) vêm de
`config/dados/memoria_camel_config.json` (com fallback no código).
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

from .config import config
from .config_json import carregar_config_json

_CFG = carregar_config_json("memoria_camel_config.json", {
    "peso_importancia": 1.0,
    "meia_vida_dias": 7,
    "k_padrao": 3,
    "n_max_registros": 100,
})

PESO_IMPORTANCIA: float = float(_CFG.get("peso_importancia", 1.0))
MEIA_VIDA_DIAS: float = float(_CFG.get("meia_vida_dias", 7))
K_PADRAO: int = int(_CFG.get("k_padrao", 3))
N_MAX_REGISTROS: int = int(_CFG.get("n_max_registros", 100))

# Palavras que não contribuem para o overlap lexical
_STOPWORDS = {
    "de", "da", "do", "em", "com", "para", "uma", "um", "o", "a", "os", "as",
    "que", "e", "é", "no", "na", "não", "se", "este", "isto", "foi", "ser",
    "como", "por", "ao", "dos", "das", "tem", "ter",
}


@dataclass
class RegistroMemoria:
    """Um registro da memória pontuada."""

    conteudo: str
    importancia: float = 5.0
    fonte: str = "agente"
    ts: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])

    @classmethod
    def de_dict(cls, dados: dict[str, Any]) -> "RegistroMemoria":
        return cls(
            conteudo=str(dados.get("conteudo") or ""),
            importancia=float(dados.get("importancia") or 0.0),
            fonte=str(dados.get("fonte") or "agente"),
            ts=float(dados.get("ts") or time.time()),
            id=str(dados.get("id") or uuid.uuid4().hex[:8]),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "conteudo": self.conteudo,
            "importancia": self.importancia,
            "fonte": self.fonte,
            "ts": self.ts,
            "id": self.id,
        }


def _tokenizar(texto: str) -> set[str]:
    """Tokens minúsculos sem stopwords (para overlap lexical)."""
    tokens = re.findall(r"[a-záàâãéêíóôõúç0-9]+", (texto or "").lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 1}


def _segundos_meia_vida() -> float:
    return MEIA_VIDA_DIAS * 24 * 3600


def pontuacao(
    conteudo: str,
    consulta_tokens: set[str],
    importancia: float,
    ts: float,
    agora: float | None = None,
    peso_importancia: float | None = None,
    meia_vida: float | None = None,
) -> float:
    """Pontua um registro contra a consulta (recência + importância + overlap)."""
    agora = agora if agora is not None else time.time()
    peso = peso_importancia if peso_importancia is not None else PESO_IMPORTANCIA
    vida = meia_vida if meia_vida is not None else _segundos_meia_vida()

    idade = max(0.0, agora - ts)
    recencia = math.exp(-idade / vida) if vida > 0 else 0.0

    importancia = max(0.0, min(10.0, importancia))
    termo_importancia = peso * (importancia / 10.0)

    tokens_registro = _tokenizar(conteudo)
    overlap = 0.0
    if consulta_tokens:
        inter = consulta_tokens & tokens_registro
        overlap = len(inter) / max(1, len(consulta_tokens))

    return round(recencia + termo_importancia + overlap, 4)


# --------------------------------------------------------------------------
# Persistência (JSON) e recuperação top-k
# --------------------------------------------------------------------------

def _caminho_memoria(caminho: str | Path | None) -> Path:
    if caminho is not None:
        return Path(caminho)
    return Path(config.memoria_camel_path)


def carregar_memoria(caminho: str | Path | None = None) -> list[RegistroMemoria]:
    """Carrega os registros do arquivo ([] se ausente/inválido)."""
    alvo = _caminho_memoria(caminho)
    try:
        if not alvo.is_file():
            return []
        with alvo.open(encoding="utf-8") as fh:
            dados = json.load(fh)
        if not isinstance(dados, list):
            return []
        return [RegistroMemoria.de_dict(d) for d in dados if isinstance(d, dict)]
    except Exception:  # noqa: BLE001 — memória nunca derruba o agente
        return []


def salvar_memoria(
    registros: list[RegistroMemoria],
    caminho: str | Path | None = None,
    n_max: int | None = None,
) -> None:
    """Grava os registros (limitados a n_max, do mais recente para o antigo)."""
    alvo = _caminho_memoria(caminho)
    alvo.parent.mkdir(parents=True, exist_ok=True)
    limite = n_max if n_max is not None else N_MAX_REGISTROS
    ordenados = sorted(registros, key=lambda r: r.ts, reverse=True)[:limite]
    alvo.write_text(
        json.dumps([r.as_dict() for r in ordenados], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def consultar_topk(
    consulta: str,
    registros: list[RegistroMemoria] | None = None,
    k: int | None = None,
    caminho: str | Path | None = None,
    agora: float | None = None,
    peso_importancia: float | None = None,
) -> list[tuple[RegistroMemoria, float]]:
    """Top-k registros por pontuação contra `consulta` (ordem decrescente)."""
    if registros is None:
        registros = carregar_memoria(caminho)
    k = k if k is not None else K_PADRAO
    tokens = _tokenizar(consulta)
    pontuados = [
        (r, pontuacao(r.conteudo, tokens, r.importancia, r.ts, agora=agora,
                      peso_importancia=peso_importancia))
        for r in registros
        if r.conteudo
    ]
    pontuados.sort(key=lambda par: par[1], reverse=True)
    return pontuados[:k]


# --------------------------------------------------------------------------
# Ferramentas — registradas em aegis/ferramentas/__init__.py
# --------------------------------------------------------------------------

@tool
def registrar_memoria_camel(conteudo: str, importancia: float = 5.0, fonte: str = "agente") -> str:
    """Registra um fato/nota na memória pontuada (importância 0-10, padrão 5)."""
    if not str(conteudo or "").strip():
        raise ValueError("conteudo é obrigatório")
    registros = carregar_memoria()
    registros.append(RegistroMemoria(
        conteudo=str(conteudo).strip(),
        importancia=float(importancia),
        fonte=str(fonte or "agente"),
    ))
    salvar_memoria(registros)
    return f"Memória registrada (importância {importancia}): {conteudo[:200]}"


@tool
def consultar_memoria_camel(consulta: str, k: int | None = None) -> str:
    """Consulta os k registros mais relevantes da memória pontuada para a consulta."""
    k = k if k is not None else K_PADRAO
    top = consultar_topk(consulta, k=k)
    if not top:
        return "(nenhum registro relevante encontrado)"
    linhas = []
    for r, nota in top:
        linhas.append(
            f"- [{nota:.2f}] ({r.fonte}) {r.conteudo[:300]}")
    return "\n".join(linhas)


@tool
def esquecer_memoria_camel(id_registro: str) -> str:
    """Remove um registro da memória pelo seu id."""
    registros = carregar_memoria()
    alvo = str(id_registro or "").strip()
    restantes = [r for r in registros if r.id != alvo]
    if len(restantes) == len(registros):
        raise ValueError(f"registro '{id_registro}' não encontrado")
    salvar_memoria(restantes)
    return f"Registro '{alvo}' esquecido."