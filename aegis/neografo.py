"""
Memória GraphRAG (M1) — grafo de conhecimento em Neo4j com DOIS grafos.

Separação por importância (regras do usuário, determinísticas — zero LLM):

  GRAFO PRIVADO (trivial, efêmero — por execução + TTL):
    - retries de comandos, depuração de sintaxe e logs de passos intermediários;
    - variáveis temporárias com ciclo de vida restrito à execução atual;
    - contextos brutos trazidos de chamadas de API ou arquivos lidos.

  GRAFO UNIVERSAL (importante, durável):
    - estado final da execução de uma tarefa solicitada pelo Orquestrador;
    - modificações persistentes no ambiente (ex.: "Nova dependência instalada");
    - novas capacidades ou falhas estruturais descobertas durante a execução.

Neo4j Community Edition suporta UM database ativo → os dois grafos convivem no
mesmo banco, separados pela propriedade `grafo` em todos os nós (mais o label
comum :Memoria para indexação). Todo Cypher usa parâmetros `$` (nunca
interpolação) — anti-injeção. Quando o Neo4j não está configurado/disponível,
TODAS as chamadas viram no-op com fallback automático: o grafo JSON do G4 e o
RAG-lite seguem funcionando — nada quebra.
"""

from __future__ import annotations

import hashlib
import re
import time
from typing import Any, Optional

# ----------------------------------------------------------------------
# Constantes
# ----------------------------------------------------------------------

GRAFO_PRIVADO = "privado"
GRAFO_UNIVERSAL = "universal"

_TTL_PRIVADO_DEFAULT_H = 24  # horas — expiração lazy dos nós triviais

# ----------------------------------------------------------------------
# Classificação determinística trivial × importante (regras do usuário)
# ----------------------------------------------------------------------

# Padrões que indicam MODIFICAÇÃO PERSISTENTE do ambiente
_PADROES_MODIFICACAO = (
    r"instalad[oa]|desinstalad[oa]|instalar|depend[êe]ncia|pacote instalad",
    r"commit|push|merge|publicad[oa]|lançad[oa]|lancad[oa]",
    r"criad[oa]\s|escrevid[oa]|editad[oa]|atualizad[oa]|alterad[oa]|removid[oa]",
    r"adicionad[oa]\s|configurad[oa]|habilitad[oa]|ativad[oa]",
)

# Novas CAPACIDADES descobertas/criadas
_PADROES_CAPACIDADE = (
    r"nova\s+capacidade|nova\s+ferramenta|nov[oa]\s+m[óo]dulo|nov[oa]\s+skill",
    r"nov[oa]\s+plugin|nov[oa]\s+habilidade|passou\s+a\s+suportar|agora\s+suporta",
)

# FALHAS estruturais (não triviais — afetam o sistema)
_PADROES_FALHA = (
    r"falha\s+estrutural|regress[ãa]o|quebrad[oa]|corrompid[oa]|incompat[ií]vel",
    r"inconsist[êe]ncia|deadlock|perda\s+de\s+dados|bloquei[oa]\s+o\s+sistema",
)

# Depuração de SINTAXE / retries / logs intermediários (triviais)
_PADROES_SINTAXE = (
    r"syntaxerror|erro\s+de\s+sintaxe|unexpected\s+token|compila[çc][ãa]o",
    r"indentationerror|nameerror|typeerror|attributeerror|referência\s+indefinida",
)

_PADROES_VARIAVEL_TEMPORARIA = (
    r"var[ií]vel\s+tempor[áa]ria|vari[áa]vel\s+local|escopo\s+da\s+execu[çc][ãa]o",
    r"\btmp\b|tempor[áa]rio|intermedi[áa]rio|passo\s+intermedi[áa]rio",
)

_PADROES_CONTEXTO_BRUTO = (
    r"resultado\s+bruto|conte[úu]do\s+bruto|resposta\s+da\s+api|conte[úu]do\s+do\s+arquivo",
    r"trecho\s+de\s+log|log\s+de\s+passo|sa[íi]da\s+de\s+comando",
)


def _contem_algum(texto: str, padroes: tuple[str, ...]) -> bool:
    return any(re.search(p, texto, re.IGNORECASE) for p in padroes)


def classificar_registro(registro: dict) -> str:
    """Classifica um registro do turno como 'privado' (trivial) ou 'universal'.

    Regras do usuário, aplicadas em ordem: marcadores de IMPORTÂNCIA (estado
    final de tarefa do orquestrador, modificação persistente, nova capacidade,
    falha estrutural) sobem para o grafo universal; retries, depuração de
    sintaxe, variáveis temporárias e contextos brutos ficam no privado. O
    default é PRIVADO (conservador — o trivial é o caso comum).

    Campos aceitos: nome (ferramenta), resultado (texto), categoria (G4),
    fase (fluxo_trabalho), repetiu (bool — retry no turno), veredito
    (multiagente/orquestrador), contexto_bruto (bool), origem.
    """
    nome = str(registro.get("nome", ""))
    resultado = str(registro.get("resultado", ""))
    categoria = str(registro.get("categoria", ""))
    fase = str(registro.get("fase", ""))
    origem = str(registro.get("origem", ""))
    texto = f"{nome} {resultado}"

    # --- Importante (universal) -----------------------------------------
    # Estado final de tarefa do Orquestrador / entrega concluída
    if fase == "ship" or origem in ("orquestrador", "avaliador", "entrega"):
        return GRAFO_UNIVERSAL
    if registro.get("veredito") is not None:
        return GRAFO_UNIVERSAL
    if categoria == "decisao":
        return GRAFO_UNIVERSAL
    # Modificação persistente do ambiente / nova capacidade / falha estrutural
    if _contem_algum(texto, _PADROES_MODIFICACAO):
        return GRAFO_UNIVERSAL
    if _contem_algum(texto, _PADROES_CAPACIDADE):
        return GRAFO_UNIVERSAL
    if _contem_algum(texto, _PADROES_FALHA):
        return GRAFO_UNIVERSAL

    # --- Trivial (privado) ----------------------------------------------
    # Retry de comando / depuração de sintaxe / contexto bruto / variável temporária
    if registro.get("repetiu") or registro.get("contexto_bruto"):
        return GRAFO_PRIVADO
    if _contem_algum(resultado, _PADROES_SINTAXE):
        return GRAFO_PRIVADO
    if _contem_algum(texto, _PADROES_VARIAVEL_TEMPORARIA + _PADROES_CONTEXTO_BRUTO):
        return GRAFO_PRIVADO
    return GRAFO_PRIVADO


def _id_de(texto: str, prefixo: str) -> str:
    """Id determinístico (hash) — lições idempotentes; CREATE usa ts_ns."""
    return f"{prefixo}:{hashlib.sha1(texto.encode('utf-8', 'replace')).hexdigest()[:16]}"


def classificar_e_tipo(registro: dict) -> tuple[str, str]:
    """(grafo, subtipo) para gravação — classificação + tipo de nó."""
    if classificar_registro(registro) == GRAFO_UNIVERSAL:
        return GRAFO_UNIVERSAL, "importante"
    if registro.get("repetiu"):
        return GRAFO_PRIVADO, "retry"
    if _contem_algum(str(registro.get("resultado", "")), _PADROES_SINTAXE):
        return GRAFO_PRIVADO, "sintaxe"
    if _contem_algum(str(registro.get("resultado", "")), _PADROES_CONTEXTO_BRUTO):
        return GRAFO_PRIVADO, "contexto"
    return GRAFO_PRIVADO, "log"


def _tokens(texto: str) -> list[str]:
    return sorted(set(re.findall(r"[a-z0-9à-ÿ]+", texto.lower())))[:40]


# ----------------------------------------------------------------------
# Cliente Neo4j (lazy, tolerante a falhas — nunca derruba o turno)
# ----------------------------------------------------------------------

class GrafoNeo4j:
    """Cliente mínimo do Neo4j: gravação nos dois grafos + consulta GraphRAG.

    Todo método captura exceções e devolve valores seguros (None/[]) — o grafo
    de memória nunca pode derrubar o fluxo do agente.
    """

    def __init__(
        self,
        uri: str,
        usuario: str,
        senha: str,
        ttl_privado_h: int = _TTL_PRIVADO_DEFAULT_H,
    ) -> None:
        self.uri = uri
        self.usuario = usuario
        self.senha = senha
        self.ttl_privado_h = ttl_privado_h
        self._driver: Any = None
        self._pronto = False
        self._tentou_schema = False

    # -- ciclo de vida ----------------------------------------------------

    def _conectar(self) -> Any:
        if self._driver is None:
            from neo4j import GraphDatabase
            self._driver = GraphDatabase.driver(
                self.uri,
                auth=(self.usuario, self.senha),
                connection_timeout=2.0,
                max_transaction_retry_time=2.0,
            )
        return self._driver

    def saude(self) -> bool:
        """Ping rápido no banco (verifica conectividade + schema)."""
        try:
            d = self._conectar()
            d.verify_connectivity()
            if not self._pronto:
                self._criar_schema(d)
                self._pronto = True
            return True
        except Exception:  # noqa: BLE001 — indisponível ≠ erro fatal
            return False

    def _criar_schema(self, driver: Any) -> None:
        """Constraints + índice (idempotentes, IF NOT EXISTS)."""
        for cypher in (
            "CREATE CONSTRAINT licao_id IF NOT EXISTS FOR (n:Licao) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT tarefa_id IF NOT EXISTS FOR (n:Tarefa) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT modificacao_id IF NOT EXISTS FOR (n:Modificacao) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT trivial_id IF NOT EXISTS FOR (n:Trivial) REQUIRE n.id IS UNIQUE",
            "CREATE INDEX idx_memoria_grafo IF NOT EXISTS FOR (n:Memoria) ON (n.grafo)",
        ):
            with driver.session() as s:
                s.run(cypher).consume()

    def _executar(self, cypher: str, params: dict) -> list[dict]:
        """Executa com parâmetros; falha silenciosa (grafo nunca derruba).

        O schema (constraints/índices) é tentado UMA vez por processo —
        indisponível no primeiro acesso não impede as gravações seguintes.
        """
        try:
            d = self._conectar()
            if not self._pronto and not self._tentou_schema:
                self._tentou_schema = True
                self.saude()
            with d.session() as s:
                return [dict(r) for r in s.run(cypher, **params)]
        except Exception:  # noqa: BLE001
            return []

    def _executar_mutacao(self, cypher: str, params: dict) -> bool:
        """Executa um MERGE/CREATE (sem RETURN) e confirma via .consume().

        Statements sem RETURN produzem zero registros — iterar devolveria []
        e um `bool([])` mentiria sobre o sucesso. O Summary do consume() é a
        prova real de que a escrita aconteceu.
        """
        try:
            d = self._conectar()
            if not self._pronto and not self._tentou_schema:
                self._tentou_schema = True
                self.saude()
            with d.session() as s:
                s.run(cypher, **params).consume()
            return True
        except Exception:  # noqa: BLE001
            return False

    # -- gravação: grafo universal (importante, durável) ------------------

    def gravar_licao(
        self,
        texto: str,
        categoria: str,
        ferramenta: str = "",
        fase: str = "",
        erro: str = "",
        prioridade: str = "media",
        thread_id: str = "",
    ) -> bool:
        """Lições (G4) sobem ao grafo universal — id determinístico (idempotente)."""
        cypher = """
        MERGE (l:Licao:Memoria {id: $id})
        SET l.texto = $texto, l.categoria = $categoria, l.prioridade = $prioridade,
            l.ferramenta = $ferramenta, l.fase = $fase, l.erro = $erro,
            l.ts = $ts, l.grafo = $grafo, l.thread_id = $thread_id,
            l.tokens = $tokens
        """
        params = {
            "id": _id_de(texto, "licao"),
            "texto": texto[:2000],
            "categoria": categoria,
            "prioridade": prioridade,
            "ferramenta": ferramenta[:80],
            "fase": fase[:80],
            "erro": erro[:200],
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "grafo": GRAFO_UNIVERSAL,
            "thread_id": thread_id[:80],
            "tokens": _tokens(texto),
        }
        return self._executar_mutacao(cypher, params)

    def gravar_tarefa_final(
        self,
        texto: str,
        veredito: str = "aprovado",
        origem: str = "orquestrador",
        thread_id: str = "",
    ) -> bool:
        """Estado final de uma tarefa (entrega G1 / orquestrador) → universal."""
        cypher = """
        CREATE (t:Tarefa:Memoria {
            id: $id, texto: $texto, veredito: $veredito, origem: $origem,
            ts: $ts, grafo: $grafo, thread_id: $thread_id, tokens: $tokens
        })
        """
        params = {
            "id": f"tarefa:{time.time_ns()}",
            "texto": texto[:2000],
            "veredito": veredito[:40],
            "origem": origem[:80],
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "grafo": GRAFO_UNIVERSAL,
            "thread_id": thread_id[:80],
            "tokens": _tokens(texto),
        }
        return self._executar_mutacao(cypher, params)

    def gravar_modificacao(
        self,
        texto: str,
        tipo: str = "ambiente",
        ferramenta: str = "",
        thread_id: str = "",
    ) -> bool:
        """Modificação persistente do ambiente (ex.: dependência instalada)."""
        cypher = """
        CREATE (m:Modificacao:Memoria {
            id: $id, texto: $texto, tipo: $tipo, ferramenta: $ferramenta,
            ts: $ts, grafo: $grafo, thread_id: $thread_id, tokens: $tokens
        })
        """
        params = {
            "id": f"modificacao:{time.time_ns()}",
            "texto": texto[:2000],
            "tipo": tipo[:40],
            "ferramenta": ferramenta[:80],
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "grafo": GRAFO_UNIVERSAL,
            "thread_id": thread_id[:80],
            "tokens": _tokens(texto),
        }
        return self._executar_mutacao(cypher, params)

    # -- gravação: grafo privado (trivial, efêmero) -----------------------

    def gravar_trivial(
        self,
        texto: str,
        tipo: str = "log",
        ferramenta: str = "",
        execucao_id: str = "",
        thread_id: str = "",
    ) -> bool:
        """Detalhe trivial (retry/sintaxe/contexto bruto) → privado, com TTL."""
        cypher = """
        CREATE (t:Trivial:Memoria {
            id: $id, texto: $texto, tipo: $tipo, ferramenta: $ferramenta,
            execucao_id: $execucao_id, expira_em: $expira_em,
            ts: $ts, grafo: $grafo, thread_id: $thread_id, tokens: $tokens
        })
        """
        params = {
            "id": f"trivial:{time.time_ns()}",
            "texto": texto[:1500],
            "tipo": tipo[:40],
            "ferramenta": ferramenta[:80],
            "execucao_id": execucao_id[:80],
            "expira_em": time.time() + self.ttl_privado_h * 3600,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
            "grafo": GRAFO_PRIVADO,
            "thread_id": thread_id[:80],
            "tokens": _tokens(texto),
        }
        return self._executar_mutacao(cypher, params)

    def limpar_privado(self, execucao_id: str) -> int:
        """Remove os triviais de uma execução (ciclo de vida restrito)."""
        n = self._executar(
            "MATCH (n:Trivial:Memoria {execucao_id: $execucao_id}) DETACH DELETE n "
            "RETURN count(*) AS removidos",
            {"execucao_id": execucao_id},
        )
        return int(n[0]["removidos"]) if n else 0

    def purga_vencidos(self) -> int:
        """Purga lazy: triviais com expira_em vencido."""
        n = self._executar(
            "MATCH (n:Trivial:Memoria) WHERE n.expira_em < $agora "
            "DETACH DELETE n RETURN count(*) AS removidos",
            {"agora": time.time()},
        )
        return int(n[0]["removidos"]) if n else 0

    # -- consulta GraphRAG -------------------------------------------------

    def consultar(self, termo: str, grafo: str = GRAFO_UNIVERSAL, limite: int = 5) -> list[dict]:
        """Busca por entidade/termo + nós relacionados (1 salto) — GraphRAG.

        Devolve nós diretos (CONTAINS no texto/tokens) e vizinhos que
        compartilham relação — o valor do grafo sobre o RAG-lite.
        """
        if not termo.strip():
            return []
        base = """
        MATCH (n:Memoria)
        WHERE n.grafo = $grafo AND toLower(n.texto) CONTAINS toLower($termo)
        RETURN n.id AS id, n.texto AS texto, n.tipo AS tipo, n.categoria AS categoria,
               n.ferramenta AS ferramenta, n.veredito AS veredito, n.ts AS ts,
               labels(n) AS rotulos
        ORDER BY n.ts DESC LIMIT $limite
        """
        vizinhos = """
        MATCH (n:Memoria)
        WHERE n.grafo = $grafo AND toLower(n.texto) CONTAINS toLower($termo)
        MATCH (n)--(m:Memoria)
        WHERE m.grafo = $grafo AND m.id <> n.id
        RETURN m.id AS id, m.texto AS texto, m.tipo AS tipo, m.categoria AS categoria,
               m.ferramenta AS ferramenta, m.veredito AS veredito, m.ts AS ts,
               labels(m) AS rotulos
        ORDER BY m.ts DESC LIMIT $limite
        """
        params = {"grafo": grafo, "termo": termo.strip()[:200], "limite": int(limite)}
        diretos = self._executar(base, params)
        relacionados = self._executar(vizinhos, params)
        vistos: set[str] = set()
        resultado: list[dict] = []
        for r in diretos + relacionados:
            chave = str(r.get("id", ""))
            if chave and chave not in vistos:
                vistos.add(chave)
                resultado.append(r)
            if len(resultado) >= limite:
                break
        return resultado

    def fechar(self) -> None:
        try:
            if self._driver is not None:
                self._driver.close()
                self._driver = None
                self._pronto = False
        except Exception:  # noqa: BLE001
            pass


# ----------------------------------------------------------------------
# Fachada de alto nível (fallback automático — nada quebra sem Neo4j)
# ----------------------------------------------------------------------

_GRAFO_SINGLETON: Optional[GrafoNeo4j] = None
_GRAFO_CFG_CHAVE: tuple = ()


def _chave_cfg(cfg: Any) -> tuple:
    return (cfg.neo4j_uri, cfg.neo4j_usuario, cfg.neo4j_senha, cfg.neo4j_ttl_privado_h)


def grafo_neo4j(cfg: Any) -> Optional[GrafoNeo4j]:
    """Singleton do cliente, sob a configuração atual (None se desativado)."""
    global _GRAFO_SINGLETON, _GRAFO_CFG_CHAVE
    if not cfg.neo4j_uri.strip():
        _GRAFO_SINGLETON = None
        return None
    chave = _chave_cfg(cfg)
    if _GRAFO_SINGLETON is not None and _GRAFO_CFG_CHAVE == chave:
        return _GRAFO_SINGLETON
    _GRAFO_CFG_CHAVE = chave
    _GRAFO_SINGLETON = GrafoNeo4j(
        cfg.neo4j_uri, cfg.neo4j_usuario, cfg.neo4j_senha, cfg.neo4j_ttl_privado_h)
    return _GRAFO_SINGLETON


def _fmt_nos(nos: list[dict]) -> str:
    linhas = []
    for n in nos:
        rotulos = ",".join(str(x) for x in n.get("rotulos", []) if x not in ("Memoria",))
        base = str(n.get("texto", ""))[:240].replace("\n", " ")
        extras = []
        if n.get("categoria"):
            extras.append(f"categoria: {n['categoria']}")
        if n.get("tipo"):
            extras.append(f"tipo: {n['tipo']}")
        if n.get("veredito"):
            extras.append(f"veredito: {n['veredito']}")
        if n.get("ferramenta"):
            extras.append(f"ferramenta: {n['ferramenta']}")
        sufixo = f" ({', '.join(extras)})" if extras else ""
        linhas.append(f"- [{rotulos}] {base}{sufixo}")
    return "\n".join(linhas)


def consultar_graphrag(
    cfg: Any, termo: str, grafo: str = GRAFO_UNIVERSAL, limite: int = 5
) -> Optional[str]:
    """Consulta o grafo Neo4j; None quando inativo (fallback → RAG-lite)."""
    g = grafo_neo4j(cfg)
    if g is None:
        return None
    nos = g.consultar(termo, grafo=grafo, limite=limite)
    if not nos:
        return ""
    cab = "grafo universal" if grafo == GRAFO_UNIVERSAL else "grafo privado"
    return f"## Memória GraphRAG ({cab})\n{_fmt_nos(nos)}"


def gravar_turno_graphrag(
    cfg: Any,
    registros: list[dict],
    licoes_com_categoria: list[tuple[str, str, str]],
    fase: str = "",
    erro: str = "",
    thread_id: str = "",
) -> bool:
    """Grava o turno nos dois grafos Neo4j — no-op completo sem Neo4j ativo.

    - UNIVERSAL: lições (G4, idempotentes) + estado final de tarefa (fase ship)
      + modificações persistentes detectadas nos registros.
    - PRIVADO: retries, depuração de sintaxe, contextos brutos e logs
      intermediários, com TTL e escopo `execucao_id` (a execução atual).
    """
    g = grafo_neo4j(cfg)
    if g is None:
        return False
    from collections import Counter
    try:
        # Universal: lições com categoria (id determinístico — idempotente)
        ferramenta_turno = registros[-1].get("nome", "") if registros else ""
        for texto, prioridade, categoria in licoes_com_categoria:
            g.gravar_licao(
                texto, categoria, ferramenta=ferramenta_turno,
                fase=fase, erro=erro, prioridade=prioridade, thread_id=thread_id)
        # Universal: estado final de tarefa solicitada (entrega G1 / ship)
        if fase == "ship":
            g.gravar_tarefa_final(
                "Entrega concluída (fase ship) — estado final verificado pelo usuário.",
                "aprovado", "entrega", thread_id)
        # Registros: classificação determinística trivial × importante
        # (a fase NÃO entra na classificação dos registros — "ship" marca a
        # TAREFA, não transforma qualquer registro do turno em importante)
        ocorrencias = Counter(
            (str(r.get("nome", "")), str(r.get("resultado", ""))[:80])
            for r in registros)
        for r in registros:
            nome = str(r.get("nome", ""))
            resultado = str(r.get("resultado", ""))
            reg = {
                **r,
                "repetiu": ocorrencias[(nome, resultado[:80])] > 1,
            }
            grafo_, tipo = classificar_e_tipo(reg)
            texto = resultado[:1500] if resultado else f"{nome} executada sem resultado"
            if grafo_ == GRAFO_UNIVERSAL:
                g.gravar_modificacao(
                    texto, tipo="ambiente", ferramenta=nome, thread_id=thread_id)
            else:
                g.gravar_trivial(
                    texto, tipo=tipo, ferramenta=nome,
                    execucao_id=thread_id or "default", thread_id=thread_id)
        return True
    except Exception:  # noqa: BLE001 — memória nunca derruba o turno
        return False
