"""Testes da recuperação de sessões (paridade Hermes session_search_tool)."""

import json

from aegis.sessoes import SessoesIndex, pesquisar_sessoes


def _montar_trajetorias(tmp_path):
    """Gera trajetórias com conversas conhecidas e retorna o diretório."""
    dir_traj = tmp_path / "trajetorias"
    dir_traj.mkdir()
    linhas = [
        # sessão 'default' — contém "recall de conversas"
        {"ts": "2026-08-05T10:00:00Z", "thread_id": "default", "tipo": "mensagem_usuario",
         "dados": {"conteudo": "Preciso implementar o modulo de recall de conversas"}},
        {"ts": "2026-08-05T10:00:01Z", "thread_id": "default", "tipo": "mensagem_agente",
         "dados": {"conteudo": "Vou criar o SessoesIndex baseado no Hermes e adicionar testes"}},
        {"ts": "2026-08-05T10:00:02Z", "thread_id": "default", "tipo": "mensagem_usuario",
         "dados": {"conteudo": "Confirma que a busca por palavra encontra isso aqui"}},
        # sessão a esconder na NAVEGAR (fonte automatizada)
        {"ts": "2026-08-05T09:00:00Z", "thread_id": "subagente_pesquisa", "tipo": "mensagem_usuario",
         "dados": {"conteudo": "delegado do pesquisador sobre astronomia"}},
        # linha não-mensagem não deve entrar no índice
        {"ts": "2026-08-05T10:00:03Z", "thread_id": "default", "tipo": "ferramenta_fim",
         "dados": {"saida": "ok"}},
    ]
    arquivo = dir_traj / "trajetoria_2026-08-05.jsonl"
    arquivo.write_text("".join(json.dumps(x, ensure_ascii=False) + "\n" for x in linhas), encoding="utf-8")
    return dir_traj


def test_ler_trajetorias_monta_sessoes(tmp_path):
    diretorio = _montar_trajetorias(tmp_path)
    idx = SessoesIndex(diretorio)
    sessoes = {s.sessao_id: s for s in idx.sessoes}
    assert "2026-08-05_default" in sessoes
    # a linha não-mensagem não vira conteúdo
    assert all(m["tipo"] in ("mensagem_usuario", "mensagem_agente") for m in sessoes["2026-08-05_default"].mensagens)


def test_descobrir_por_consulta(tmp_path):
    idx = SessoesIndex(_montar_trajetorias(tmp_path))
    resultado = idx.descobrir("recall")
    assert resultado, "deve achar a sessão default"
    assert resultado[0]["sessao"] == "2026-08-05_default"
    assert "recall" in resultado[0]["trecho"].lower()
    assert resultado[0]["escore"] > 0


def test_descobrir_vazio_quando_sem_match(tmp_path):
    idx = SessoesIndex(_montar_trajetorias(tmp_path))
    assert idx.descobrir("zzzpalavra") == []


def test_determinismo_mesma_saida(tmp_path):
    diretorio = _montar_trajetorias(tmp_path)
    a = SessoesIndex(diretorio).descobrir("recall")
    b = SessoesIndex(diretorio).descobrir("recall")
    assert a == b


def test_rolar_janela(tmp_path):
    idx = SessoesIndex(_montar_trajetorias(tmp_path))
    r = idx.rolar("2026-08-05_default", mensagem=1, janela=1)
    assert r["sessao"] == "2026-08-05_default"
    assert len(r["mensagens"]) == 3  # âncora 1 ± 1 → índices 0..2 (3 msgs)


def test_rolar_sessao_ausente(tmp_path):
    idx = SessoesIndex(_montar_trajetorias(tmp_path))
    assert "erro" in idx.rolar("nao_existe")


def test_navegar_oculta_fonte_automatizada(tmp_path):
    idx = SessoesIndex(_montar_trajetorias(tmp_path))
    navegadas = idx.navegar(limite=10)
    ids = {n["sessao"] for n in navegadas}
    assert "2026-08-05_default" in ids
    assert not any("subagente" in ident for ident in ids)


def test_ferramenta_descobrir_invoke(tmp_path):
    from aegis import sessoes as mod

    mod._index_padrao = lambda: SessoesIndex(_montar_trajetorias(tmp_path))  # noqa: SLF001
    saida = pesquisar_sessoes.invoke({"consulta": "recall"})
    assert "2026-08-05_default" in saida
