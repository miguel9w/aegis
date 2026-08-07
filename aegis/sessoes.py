"""
Recuperação de sessões anteriores (Session Recall) — porta da ferramenta
`session_search_tool.py` do Hermes Agent (Nous Research) para o Aegis.

O Hermes busca em sessões passadas gravadas em SQLite (FTS5) e devolve trechos
sem custo de LLM. O Aegis indexa as trajetórias JSONL que já registra
(`mensagem_usuario`/`mensagem_agente` por thread+dia) e oferece a mesma forma
de uso de três modos, inferidos dos argumentos (sem parâmetro "modo"):

  1. DESCUBRIR (discovery) — passa ``consulta``: ranqueia sessões por
     sobreposição IDF de tokens e devolve as top-N com trecho e marcadores
     de início/fim. Sem LLM.
  2. ROLAR (scroll)        — passa ``sessao`` (+ ``mensagem`` opcional).
     Devolve uma janela (±n mensagens) da sessão para leitura em detalhe.
  3. NAVEGAR (browse)      — nada além de ``limite``. Devolve sessões
     recentes em ordem cronológica (título, prévia, data).

Fonte: diretório de trajetórias (``config.trajetorias_dir``). O índice é
reconstruído em memória a cada chamada (trajetórias são leves), garantindo
frescor e determinismo — mesma entrada gera a mesma saída.
"""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any

from langchain_core.tools import tool

# Tipos de registro de trajetória que viram "mensagens" de uma sessão.
_TIPOS_MENSAGEM = ("mensagem_usuario", "mensagem_agente")
# Rótulo humano exibido em cada trecho recuperado.
_ROTULO = {"mensagem_usuario": "usuario", "mensagem_agente": "aegis"}
# Limite de caracteres de cada trecho devolvido — configurável em limites.json
from .config_json import carregar_config_json as _cfg_json

_LIMITE_TRECHO = int(_cfg_json("limites.json", {"limite_trecho_sessao": 300})["limite_trecho_sessao"])
# Sessões cuja thread sugere automatização (agendador/subagente) ficam fora da NAVEGAR.
_FONTES_AUTOMATIZADA = ("subagente", "agendador", "cron")
# Palavras que não contribuem para o ranqueamento.
_STOPWORDS = {
    "de", "da", "do", "em", "com", "para", "uma", "um", "o", "a", "os", "as",
    "que", "e", "é", "no", "na", "não", "se", "este", "isto", "foi", "ser", "como",
}


def _data_iso(ts: str) -> str:
    """Extrai a parte de data (AAAA-MM-DD) de um timestamp ISO; fallback."""
    try:
        return ts[:10] if ts else "desconhecida"
    except Exception:  # noqa: BLE001
        return "desconhecida"


def _normalizar(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


class Sessao:
    """Uma sessão = um dia + uma thread_id (recorte de troca)."""

    def __init__(self, sessao_id: str, thread_id: str, data: str) -> None:
        self.sessao_id = sessao_id
        self.thread_id = thread_id
        self.data = data
        self.mensagens: list[dict[str, Any]] = []

    def adicionar(self, tipo: str, conteudo: str, ts: str) -> None:
        self.mensagens.append({"tipo": tipo, "conteudo": conteudo, "ts": ts})

    def texto(self) -> str:
        return " ".join(_normalizar(m["conteudo"]) for m in self.mensagens)


def _ler_trajetorias(diretorio: str | Path) -> list[Sessao]:
    """Lê todos os JSONL de trajetória e monta sessões (thread+dia)."""
    diretorio = Path(diretorio)
    if not diretorio.exists():
        return []
    sessoes: dict[str, Sessao] = {}
    for arquivo in sorted(diretorio.glob("*.jsonl")):
        try:
            with arquivo.open(encoding="utf-8") as fh:
                for linha in fh:
                    linha = linha.strip()
                    if not linha:
                        continue
                    reg = json.loads(linha)
                    tipo = reg.get("tipo")
                    if tipo not in _TIPOS_MENSAGEM:
                        continue
                    conteudo = (reg.get("dados") or {}).get("conteudo") or ""
                    if not conteudo:
                        continue
                    thread = reg.get("thread_id") or "?"
                    data = _data_iso(reg.get("ts") or "")
                    chave = f"{data}_{thread}"
                    if chave not in sessoes:
                        sessoes[chave] = Sessao(chave, thread, data)
                    sessoes[chave].adicionar(tipo, str(conteudo), reg.get("ts") or "")
        except Exception:  # noqa: BLE001 — um arquivo ruim nunca quebra a consulta
            continue
    return sorted(sessoes.values(), key=lambda s: (s.data, s.sessao_id))


def _tokens(frase: str) -> list[str]:
    """Tokeniza, remove acentos e margessa a STOPWORDS."""
    f = _normalizar(frase.lower())
    return [t for t in re.findall(r"[a-z0-9]+", f) if len(t) > 2 and t not in _STOPWORDS]


def _ranquear(consulta: str, sessao: Sessao) -> tuple[float, int]:
    """Escore por cobertura de tokens + bônus de frequência (IDF-like)."""
    q = _tokens(consulta)
    if not q:
        return 0.0, 0
    corpus = _tokens(sessao.texto())
    set_corpus = set(corpus)
    ocorr = sum(1 for t in q if t in set_corpus)
    cobertura = ocorr / len(q)
    freq = sum(corpus.count(t) for t in q)
    return round(cobertura * 10 + min(freq, 20) * 0.5, 3), ocorr


def _trecho_com(query: str, sessao: Sessao) -> str:
    """Retorna a 1ª mensagem da sessão que contém a consulta (ou a última)."""
    q = _tokens(query)
    for m in sessao.mensagens:
        normal = _normalizar(m["conteudo"].lower())
        if any(t in normal for t in q):
            return m["conteudo"][:_LIMITE_TRECHO]
    if sessao.mensagens:
        return sessao.mensagens[-1]["conteudo"][:_LIMITE_TRECHO]
    return ""


def _marcadores(sessao: Sessao) -> list[dict[str, Any]]:
    """Primeiras e últimas 3 mensagens (marcador de braço), como no Hermes."""
    msgs = sessao.mensagens
    if len(msgs) <= 6:
        return msgs
    return msgs[:3] + msgs[-3:]


class SessoesIndex:
    """Índice em memória (recuperável) sobre as trajetórias de um diretório."""

    def __init__(self, diretorio: str | Path) -> None:
        self.sessoes = _ler_trajetorias(diretorio)

    def descobrir(self, consulta: str, limite: int = 5) -> list[dict[str, Any]]:
        """Top-N sessões ranqueadas por relevância, com trecho destacado."""
        ranque = []
        for s in self.sessoes:
            escore, ocorr = _ranquear(consulta, s)
            if ocorr > 0:
                ranque.append((escore, ocorr, s))
        ranque.sort(key=lambda t: (t[0], t[1]), reverse=True)
        resultado = []
        for escore, _ocorr, s in ranque[:limite]:
            resultado.append({
                "sessao": s.sessao_id,
                "thread": s.thread_id,
                "data": s.data,
                "escore": escore,
                "trecho": _trecho_com(consulta, s),
                "mensagens": len(s.mensagens),
                "marcadores": _marcadores(s),
            })
        return resultado

    def rolar(self, sessao_id: str, mensagem: int = 0, janela: int = 3) -> dict[str, Any]:
        """Janela de mensagens ao redor de ``mensagem`` (scroll)."""
        sessao = next((s for s in self.sessoes if s.sessao_id == sessao_id), None)
        if not sessao:
            return {"erro": f"Sessão '{sessao_id}' não encontrada."}
        msgs = sessao.mensagens
        if not msgs:
            return {"sessao": sessao_id, "thread": sessao.thread_id, "data": sessao.data, "mensagens": []}
        ini = max(0, mensagem - janela)
        fim = min(len(msgs), mensagem + janela + 1)
        return {
            "sessao": sessao_id,
            "thread": sessao.thread_id,
            "data": sessao.data,
            "mensagens": msgs[ini:fim],
        }

    def navegar(self, limite: int = 8) -> list[dict[str, Any]]:
        """Sessões recentes (data desc), com prévia, ignorando fontes automáticas."""
        if not self.sessoes:
            return []
        ordenado = sorted(self.sessoes, key=lambda s: (s.data, s.sessao_id), reverse=True)
        resultado = []
        for s in ordenado:
            if limite <= 0:
                break
            if any(origem in s.thread_id for origem in _FONTES_AUTOMATIZADA):
                continue
            usuario = [m["conteudo"] for m in s.mensagens if m["tipo"] == "mensagem_usuario"]
            previa = usuario[-1][:140] if usuario else (s.mensagens[-1]["conteudo"][:140] if s.mensagens else "")
            resultado.append({"sessao": s.sessao_id, "thread": s.thread_id, "data": s.data, "previa": previa})
            limite -= 1
        return resultado


def _index_padrao() -> SessoesIndex:
    from .config import config
    return SessoesIndex(config.trajetorias_dir)


@tool
def pesquisar_sessoes(
    consulta: str | None = None,
    sessao: str | None = None,
    mensagem: int = 0,
    janela: int = 3,
    limite: int = 5,
) -> str:
    """
    Pesquisa em conversas ANTERIORES armazenadas nas trajetórias do agente.

    Use quando a resposta depender de algo já dito em sessão passada (o usuário
    retomou um assunto tratado antes, ou você precisa de contexto de tarefa antiga).

    Três formatos (inferidos dos argumentos, sem um parâmetro 'modo'):
      - Informe 'consulta': busca por palavras-chave em todas as sessões e devolve
        as mais relevantes com trecho (equivalente ao 'descobrir do Hermes).
      - Informe 'sessao' (+ opcional 'mensagem'): devolve uma janela de
        mensagens ao redor desse índice (equivalente 'rolar do Hermes).
      - Informe apenas 'limite': devolve sessões recentes cronologicamente
        (equivalente 'navegar' do Hermes).

    Args:
        consulta: palavras-chave para procurar (modo 'descubirra').
        sessao: id de sessão (ex.: '2026-08-05_default'; modo 'rolar').
        mensagem: índice da mensagem âncora para rolagem (0 por padrão).
        janela: nº de mensagens ao redor do âncora para rolagem.
        limite: quantos itens devolver (descubirra/navegar).

    Returns:
        Texto formatado e legível com os resultados.
    """
    indice = _index_padrao()
    if consulta:
        resultado = indice.descobrir(consulta, limite)
        if not resultado:
            return "Nenhuma sessão encontrada para a consulta."
        linhas = [f"- {r['sessao']} ({r['data']}) — {r['trecho']}" for r in resultado]
        return "Sessões relevantes:\n" + "\n".join(linhas)
    if sessao:
        r = indice.rolar(sessao, mensagem or 0, janela)
        if "erro" in r:
            return r["erro"]
        blocos = [f"[{_ROTULO.get(m['tipo'], m['tipo'])}] {m['conteudo'][:_LIMITE_TRECHO]}" for m in r["mensagens"]]
        return f"Sessão {r['sessao']} ({r['data']}):\n" + ("\n".join(blocos) if blocos else "(vazia)")
    navegadas = indice.navegar(limite)
    if not navegadas:
        return "Nenhuma sessão disponível."
    linhas = [f"- {s['sessao']} ({s['data']}) — {s['previa']}" for s in navegadas]
    return "Sessões recentes:\n" + "\n".join(linhas)