"""
M1 — Memória GraphRAG (Neo4j, dois grafos): testes do aegis/neografo.py.

Cobre: classificação determinística trivial × importante (regras do usuário),
Cypher 100% parametrizado (anti-injeção), distribuição dos registros do turno
nos dois grafos (driver mockado), fallback automático sem Neo4j configurado e
integração real com container neo4j:5 (opcional — skip quando não está up).
"""

from __future__ import annotations

import pytest

from aegis import neografo
from aegis.neografo import (
    GRAFO_PRIVADO,
    GRAFO_UNIVERSAL,
    GrafoNeo4j,
    classificar_e_tipo,
    classificar_registro,
    consultar_graphrag,
    grafo_neo4j,
    gravar_turno_graphrag,
)


# ----------------------------------------------------------------------
# Fakes do driver (determinístico, sem rede)
# ----------------------------------------------------------------------

class ResultadoFake:
    def __init__(self, linhas: list[dict]) -> None:
        self._linhas = linhas

    def __iter__(self):
        return iter(self._linhas)

    def consume(self) -> None:
        """Driver real tem .consume(); o fake precisa imitar (schema)."""
        pass


class SessionFake:
    def __init__(self, respostas: list[list[dict]]) -> None:
        self.chamadas: list[tuple[str, dict]] = []
        self._respostas = respostas
        self._i = 0

    def run(self, cypher: str, **params):
        self.chamadas.append((cypher, params))
        # respostas em repeat: constraints/schema não consomem as fixtures
        linhas = self._respostas[self._i % len(self._respostas)] if self._respostas else []
        self._i += 1
        return ResultadoFake(linhas)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DriverFake:
    def __init__(self, respostas: list[list[dict]] | None = None) -> None:
        self.sessao = SessionFake(respostas or [])

    def verify_connectivity(self) -> bool:
        return True

    def session(self) -> SessionFake:
        return self.sessao

    def close(self) -> None:
        pass


class CfgFake:
    def __init__(self, uri: str = "bolt://localhost:7687") -> None:
        self.neo4j_uri = uri
        self.neo4j_usuario = "neo4j"
        self.neo4j_senha = "aegis-local"
        self.neo4j_ttl_privado_h = 24


@pytest.fixture(autouse=True)
def _driver_fake(monkeypatch: pytest.MonkeyPatch):
    """Todo teste usa driver fake (sem rede); Neo4j real só no teste explícito."""
    driver_fake = DriverFake()
    monkeypatch.setattr(neografo, "_GRAFO_SINGLETON", None)
    monkeypatch.setattr(neografo, "_GRAFO_CFG_CHAVE", ())
    monkeypatch.setattr(
        "neo4j.GraphDatabase.driver",
        lambda uri, auth=None, **kw: driver_fake,
    )
    return driver_fake


# ----------------------------------------------------------------------
# Classificação determinística (regras do usuário)
# ----------------------------------------------------------------------

def test_retry_de_comando_eh_privado():
    reg = {"nome": "executar_comando", "resultado": "ERRO: pacote ausente", "repetiu": True}
    assert classificar_registro(reg) == GRAFO_PRIVADO


def test_depuracao_de_sintaxe_eh_privada():
    reg = {"nome": "executar_comando", "resultado": "SyntaxError: invalid syntax (linha 12)"}
    assert classificar_registro(reg) == GRAFO_PRIVADO
    grafo_, tipo = classificar_e_tipo(reg)
    assert grafo_ == GRAFO_PRIVADO and tipo == "sintaxe"


def test_log_de_passo_intermediario_eh_privado():
    reg = {"nome": "ler_arquivo", "resultado": "log de passo: parsing ok"}
    assert classificar_registro(reg) == GRAFO_PRIVADO


def test_variavel_temporaria_eh_privada():
    reg = {"nome": "executar_comando", "resultado": "variavel temporaria tmp_x criada"}
    assert classificar_registro(reg) == GRAFO_PRIVADO


def test_contexto_bruto_de_api_eh_privado():
    reg = {"nome": "buscar_web", "resultado": "resultado bruto da API: 42 hits"}
    assert classificar_registro(reg) == GRAFO_PRIVADO


def test_estado_final_de_tarefa_do_orquestrador_eh_universal():
    reg = {"nome": "delegar_codigo", "origem": "orquestrador", "resultado": "tarefa concluída"}
    assert classificar_registro(reg) == GRAFO_UNIVERSAL
    reg2 = {"nome": "delegar", "veredito": "aprovado", "resultado": "ok"}
    assert classificar_registro(reg2) == GRAFO_UNIVERSAL


def test_modificacao_persistente_do_ambiente_eh_universal():
    reg = {"nome": "executar_comando", "resultado": "Nova dependência instalada no sistema"}
    assert classificar_registro(reg) == GRAFO_UNIVERSAL


def test_nova_capacidade_eh_universal():
    reg = {"nome": "executar_comando", "resultado": "nova capacidade: ferramenta criada"}
    assert classificar_registro(reg) == GRAFO_UNIVERSAL


def test_falha_estrutural_eh_universal():
    reg = {"nome": "executar_comando", "resultado": "falha estrutural: módulo corrompido"}
    assert classificar_registro(reg) == GRAFO_UNIVERSAL


def test_fase_ship_eh_universal():
    reg = {"nome": "reverter_entrega", "fase": "ship", "resultado": "entrega revertida"}
    assert classificar_registro(reg) == GRAFO_UNIVERSAL


def test_categoria_decisao_eh_universal():
    reg = {"nome": "escrever_arquivo", "categoria": "decisao", "resultado": "padrão adotado"}
    assert classificar_registro(reg) == GRAFO_UNIVERSAL


def test_default_eh_privado_conservador():
    reg = {"nome": "hora_atual", "resultado": "14:00"}
    assert classificar_registro(reg) == GRAFO_PRIVADO


def test_subtipos_classificar_e_tipo():
    assert classificar_e_tipo({"repetiu": True, "resultado": "x"}) == (GRAFO_PRIVADO, "retry")
    assert classificar_e_tipo({"resultado": "SyntaxError: x"}) == (GRAFO_PRIVADO, "sintaxe")
    assert classificar_e_tipo({"resultado": "conteúdo bruto do arquivo"}) == (GRAFO_PRIVADO, "contexto")
    assert classificar_e_tipo({"resultado": "ok"}) == (GRAFO_PRIVADO, "log")
    assert classificar_e_tipo({"resultado": "instalada nova dependência"}) == (GRAFO_UNIVERSAL, "importante")


# ----------------------------------------------------------------------
# Cypher parametrizado (anti-injeção)
# ----------------------------------------------------------------------

def test_cypher_nunca_interpola_texto(_driver_fake):
    g = GrafoNeo4j("bolt://x", "neo4j", "s")
    texto_malicioso = "x'; MATCH (n) DETACH DELETE n //"
    g.gravar_trivial(texto_malicioso, "log", "tool", "exec-1", "t1")
    for cypher, params in _driver_fake.sessao.chamadas:
        assert texto_malicioso not in cypher
        assert "'" not in cypher.replace("'", "", 1) or cypher.count("'") == 0
    # o texto malicioso deve estar apenas em $params
    textos_nos_params = any(
        texto_malicioso in str(v) for _, params in _driver_fake.sessao.chamadas
        for v in params.values())
    assert textos_nos_params


def test_gravar_licao_usa_id_deterministico(_driver_fake):
    g = GrafoNeo4j("bolt://x", "neo4j", "s")
    g.gravar_licao("lição estável", "decisao", "ferramenta", "fase", "", "alta", "t1")
    cyphers = [c for c, _ in _driver_fake.sessao.chamadas]
    assert any("MERGE (l:Licao:Memoria {id: $id})" in c for c in cyphers)


# ----------------------------------------------------------------------
# Distribuição do turno nos dois grafos (driver mockado)
# ----------------------------------------------------------------------

def test_gravar_turno_distribui_universal_e_privado(_driver_fake):
    cfg = CfgFake()
    registros = [
        {"nome": "executar_comando", "resultado": "SyntaxError: bad token"},
        {"nome": "executar_comando", "resultado": "SyntaxError: bad token"},  # retry
        {"nome": "executar_comando", "resultado": "Nova dependência instalada no sistema"},
    ]
    licoes = [("usar uv em vez de pip", "media", "decisao")]
    gravar_turno_graphrag(cfg, registros, licoes, fase="ship", thread_id="t1")

    cyphers = [c for c, _ in _driver_fake.sessao.chamadas]
    juntos = "\n".join(cyphers)
    assert "MERGE (l:Licao:Memoria" in juntos          # lição → universal
    assert "CREATE (t:Tarefa:Memoria" in juntos        # fase ship → tarefa final
    assert "CREATE (m:Modificacao:Memoria" in juntos   # dependência instalada → universal
    assert "CREATE (t:Trivial:Memoria" in juntos       # sintaxe/retry → privado
    # execucao_id = thread (ciclo de vida restrito à execução atual)
    params_privados = [
        p for c, p in _driver_fake.sessao.chamadas if "Trivial:Memoria" in c]
    assert params_privados and all(
        p.get("execucao_id") == "t1" for p in params_privados)


def test_gravar_turno_sem_licoes_ainda_grava_triviais(_driver_fake):
    cfg = CfgFake()
    gravar_turno_graphrag(
        cfg, [{"nome": "ler_arquivo", "resultado": "conteúdo bruto do arquivo"}],
        [], thread_id="t2")
    juntos = "\n".join(c for c, _ in _driver_fake.sessao.chamadas)
    assert "CREATE (t:Trivial:Memoria" in juntos


# ----------------------------------------------------------------------
# Consulta GraphRAG (diretos + relacionados, dedup)
# ----------------------------------------------------------------------

def test_consultar_une_diretos_e_relacionados(_driver_fake):
    _driver_fake.sessao._respostas = [
        [{"id": "a", "texto": "lição direta", "rotulos": ["Licao", "Memoria"]}],
        [{"id": "a", "texto": "lição direta", "rotulos": ["Licao", "Memoria"]},
         {"id": "b", "texto": "vizinho", "rotulos": ["Tarefa", "Memoria"]}],
    ]
    g = GrafoNeo4j("bolt://x", "neo4j", "s")
    nos = g.consultar("termo", grafo=GRAFO_UNIVERSAL, limite=5)
    ids = [n["id"] for n in nos]
    assert ids == ["a", "b"]  # dedup do nó direto repetido nos vizinhos


def test_consultar_sem_termo_devolve_vazio(_driver_fake):
    g = GrafoNeo4j("bolt://x", "neo4j", "s")
    assert g.consultar("   ", GRAFO_UNIVERSAL) == []


def test_limpar_privado_retorna_contagem(_driver_fake):
    _driver_fake.sessao._respostas = [[{"removidos": 3}]]
    g = GrafoNeo4j("bolt://x", "neo4j", "s")
    assert g.limpar_privado("exec-9") == 3


# ----------------------------------------------------------------------
# Fallback automático (sem Neo4j configurado — nada quebra)
# ----------------------------------------------------------------------

def test_fallback_sem_uri_eh_noop(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(neografo, "_GRAFO_SINGLETON", None)
    cfg = CfgFake(uri="")
    assert grafo_neo4j(cfg) is None
    assert consultar_graphrag(cfg, "qualquer") is None
    assert not gravar_turno_graphrag(cfg, [{"nome": "x", "resultado": "y"}], [])


def test_fallback_consulta_graphrag_sem_resultados(monkeypatch: pytest.MonkeyPatch):
    """Neo4j ativo mas sem hits → bloco vazio ("" — não None), para o fallback
    da tool consultar_grafo retornar a mensagem amigável."""
    monkeypatch.setattr(neografo, "_GRAFO_SINGLETON", None)
    cfg = CfgFake()
    assert consultar_graphrag(cfg, "termo", limite=5) == ""


# ----------------------------------------------------------------------
# Integração real (opcional — container neo4j:5 via `pixi run neo4j-up`)
# ----------------------------------------------------------------------

def test_integracao_neo4j_docker_grafos_reais(monkeypatch: pytest.MonkeyPatch):
    """Prova real: gravação/consulta/limpeza num Neo4j de verdade.

    SKIP automático quando o container não está up (CI, máquina sem docker).
    Para rodar: `pixi run neo4j-up` (credenciais locais neo4j/aegis-local).
    """
    monkeypatch.undo()  # desfaz o fixture autouse — driver REAL, não o fake
    neografo._GRAFO_SINGLETON = None
    neografo._GRAFO_CFG_CHAVE = ()
    g = GrafoNeo4j("bolt://localhost:7687", "neo4j", "aegis-local")
    if not g.saude():
        pytest.skip("Neo4j não está rodando — `pixi run neo4j-up` para ativar")
    try:
        execucao = "teste-integracao"
        g.limpar_privado(execucao)
        # universal: lição + tarefa final + modificação
        assert g.gravar_licao("lição de integração M1", "decisao", "teste", "ship", "", "alta", "t-integ")
        assert g.gravar_tarefa_final("tarefa final de integração", "aprovado", "orquestrador", "t-integ")
        assert g.gravar_modificacao("Nova dependência instalada no sistema", "ambiente", "teste", "t-integ")
        # privado: retry + sintaxe, escopo da execução
        assert g.gravar_trivial("retry de comando", "retry", "teste", execucao, "t-integ")
        assert g.gravar_trivial("SyntaxError: bad token", "sintaxe", "teste", execucao, "t-integ")
        # consulta GraphRAG no universal
        nos = g.consultar("integração", grafo=GRAFO_UNIVERSAL, limite=10)
        assert nos, "consulta universal deve achar a lição gravada"
        assert any("tarefa final" in str(n.get("texto", "")) for n in nos)
        # limpeza do privado (ciclo de vida restrito à execução)
        assert g.limpar_privado(execucao) >= 2
        # purga lazy não levanta
        g.purga_vencidos()
    finally:
        g.fechar()
