"""G5 — Pausa/retomada com handoff e reversão segura.

Paridade `gsd-pause-work`/`gsd-resume-work` + `gsd-undo` + `gsd-forensics`:
- handoff persistido (Store, namespace `handoffs/`) com fase/plano/critérios
  e próximos passos derivados por regra (sem LLM);
- retomada com contexto completo — ciclo G1 continua do ponto exato
  (invariante: nenhum passo concluído é re-executado);
- `reverter_entrega` via `git revert` em repo de teste (sem afetar o resto);
- `replay_turno` reprodutor determinístico (sem rede).
"""

import json
import subprocess

from langchain_core.messages import AIMessage, HumanMessage

from aegis.config import Config
from aegis.grafo import montar_grafo
from aegis.memoria import criar_checkpointer_sync, criar_store_sync
from conftest import ModeloFake, basico_tools, chamada_tool


def _cfg(tmp_path):
    c = Config()
    c.banco = tmp_path / "g5.db"
    c.thread_id = "t-g5"
    c.limiar_compressao = 100
    c.memoria_ativa = True
    c.learnings_dir = tmp_path / "docs" / "learnings"
    c.grafo_path = tmp_path / "grafo.json"
    return c


def _app(tmp_path, modelo):
    cfg = _cfg(tmp_path)
    checkpointer = criar_checkpointer_sync(cfg.banco)
    st = criar_store_sync(cfg.banco)
    app = montar_grafo(modelo, basico_tools(), checkpointer=checkpointer, store=st, cfg=cfg)
    return app, cfg


def _resposta_plano(passos):
    return AIMessage(content=json.dumps({"plano": passos}, ensure_ascii=False))


def _resposta_verify_entrega(vereditos):
    return AIMessage(content=json.dumps({"criterios": vereditos}, ensure_ascii=False))


def _resposta_revisao(itens):
    return AIMessage(content=json.dumps({"itens": itens}, ensure_ascii=False))


def _resposta_licoes(licoes):
    return AIMessage(content=json.dumps({"licoes": licoes}, ensure_ascii=False))


def _resposta_verificacao(veredito, evidencias):
    return AIMessage(content=json.dumps({"veredito": veredito, "evidencias": evidencias},
                                        ensure_ascii=False))


# ---------------------------------------------------------------------
# Handoff (pausa/retomada)
# ---------------------------------------------------------------------

def test_pausa_grava_handoff_e_retomada_continua_ciclo(tmp_path, monkeypatch):
    """ENTREGAR vago → interrupt (fase discuss); pausa grava o handoff na
    Store; retomada com a resposta da pergunta continua discuss→plan→
    execute→verify→revisar→ship SEM re-executar fases anteriores."""
    from langgraph.types import Command

    from aegis.config import config
    from aegis.ferramentas.trabalho import pausar_trabalho, retomar_trabalho
    from aegis.memoria import namespace_handoff_thread

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(config, "banco", cfg.banco)
    monkeypatch.setattr(config, "thread_id", cfg.thread_id)
    modelo = ModeloFake()
    modelo.configurar([
        _resposta_plano([{"passo": "criar tool somador", "objetivo": "entrega",
                          "status": "pendente"}]),
        chamada_tool("calculadora", {"expressao": "1 + 1"}, id_chamada="call_g5"),
        AIMessage(content="Tool somador criada e testada."),
        _resposta_verify_entrega([{"indice": 0, "verificado": True,
                                   "evidencia": "tool existe e roda"}]),
        _resposta_revisao([
            {"item": "seguranca", "veredito": "aprovado", "apontamento": ""},
            {"item": "sandbox de escrita", "veredito": "aprovado", "apontamento": ""},
            {"item": "testes", "veredito": "aprovado", "apontamento": ""},
            {"item": "documentacao", "veredito": "aprovado", "apontamento": ""},
            {"item": "anti-alucinacao", "veredito": "aprovado", "apontamento": ""},
        ]),
        _resposta_licoes([]),
    ])
    app = montar_grafo(modelo, basico_tools(),
                       checkpointer=criar_checkpointer_sync(cfg.banco),
                       store=criar_store_sync(cfg.banco), cfg=cfg)
    config_exec = {"configurable": {"thread_id": cfg.thread_id}}

    # turno 1 — pedido vago → discuss pergunta (interrupt = pausa natural)
    r1 = app.invoke({"mensagens": [HumanMessage("ENTREGAR criar uma ferramenta somador")],
                     "metadados_sessao": {"thread_id": cfg.thread_id}},
                    config=config_exec)
    assert r1.get("__interrupt__"), "discuss vago deveria perguntar"
    assert r1["fluxo_trabalho"]["fase"] == "discuss"

    # handoff — a tool usa a thread ativa do processo (config.thread_id)
    texto = pausar_trabalho.invoke({})
    assert "Pausa registrada" in texto
    assert "DISCUSS" in texto
    store = criar_store_sync(cfg.banco)
    itens = list(store.search(namespace_handoff_thread(cfg.thread_id)))
    assert itens, "handoff não persistido na Store"
    handoff = itens[-1].value
    assert handoff["fase_atual"] == "discuss"
    assert handoff["proximos_passos"], "próximos passos derivados por regra"
    assert any("plano" in p.lower() for p in handoff["proximos_passos"])

    # retomada — contexto para o agente
    ctx = retomar_trabalho.invoke({})
    assert "CONTEXTO DE RETOMADA" in ctx
    assert "DISCUSS" in ctx

    # turno 2 — responde a pergunta do discuss: o ciclo avança ATÉ o ship
    r2 = app.invoke(Command(resume="criar o somador em aegis com testes e push"),
                    config=config_exec)
    assert r2.get("__interrupt__"), "ship deveria abrir o UAT"
    r3 = app.invoke(Command(resume="aprovado"), config=config_exec)
    ft = r3["fluxo_trabalho"]
    assert ft["fase"] == "ship", f"fase final: {ft.get('fase')}"
    assert ft["ship"]["criterios_verificados"] == 1
    # invariante: o plano não foi recriado do zero (1 único passo)
    assert len(ft["plano"]) == 1


def test_pausa_sem_entrega_avisada(tmp_path, monkeypatch):
    from aegis.config import config
    from aegis.ferramentas.trabalho import pausar_trabalho

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(config, "banco", cfg.banco)
    monkeypatch.setattr(config, "thread_id", cfg.thread_id)
    saida = pausar_trabalho.invoke({})
    assert "Não há entrega em andamento" in saida


def test_retomar_sem_handoff_avisado(tmp_path, monkeypatch):
    from aegis.config import config
    from aegis.ferramentas.trabalho import retomar_trabalho

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(config, "banco", cfg.banco)
    monkeypatch.setattr(config, "thread_id", cfg.thread_id)
    saida = retomar_trabalho.invoke({})
    assert "Nenhum handoff encontrado" in saida


# ---------------------------------------------------------------------
# Reversão segura (gsd-undo)
# ---------------------------------------------------------------------

def _repo_git(tmp_path) -> "object":
    """Cria um repo git de teste com 3 commits (a → b → c) e retorna (repo, shas)."""
    import shlex

    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        p = subprocess.run(["git", *args], cwd=repo, check=True,
                           capture_output=True, text=True)
        return p.stdout.strip()
    git("init", "-q", "-b", "master")
    git("config", "user.email", "teste@local")
    git("config", "user.name", "Teste")
    shas = []
    for nome, conteudo in (("a.txt", "a\n"), ("b.txt", "b\n"), ("c.txt", "c\n")):
        (repo / nome).write_text(conteudo)
        git("add", ".")
        git("commit", "-q", "-m", nome)
        shas.append(git("rev-parse", "HEAD").strip())
    return repo, shas


def test_reverter_entrega_reverte_commit_especifico(tmp_path, monkeypatch):
    from aegis.ferramentas import trabalho as mod_trabalho
    from aegis.ferramentas.trabalho import reverter_entrega

    repo, (sha_a, sha_b, sha_c) = _repo_git(tmp_path)
    monkeypatch.setattr(mod_trabalho, "RAIZ", repo)
    saida = reverter_entrega.invoke({"sha": sha_b})
    assert "revertida" in saida
    assert not (repo / "b.txt").exists(), "b.txt removido pelo revert"
    assert (repo / "a.txt").exists() and (repo / "c.txt").exists(), "resto intacto"
    assert "Revert" in subprocess.run(["git", "log", "-1", "--format=%s"], cwd=repo,
                                      capture_output=True, text=True).stdout


def test_reverter_entrega_default_reverte_head(tmp_path, monkeypatch):
    from aegis.ferramentas import trabalho as mod_trabalho
    from aegis.ferramentas.trabalho import reverter_entrega

    repo, _ = _repo_git(tmp_path)
    monkeypatch.setattr(mod_trabalho, "RAIZ", repo)
    saida = reverter_entrega.invoke({})
    assert "revertida" in saida
    assert not (repo / "c.txt").exists()
    assert (repo / "b.txt").exists()


def test_reverter_entrega_sha_invalido_bloqueado(tmp_path, monkeypatch):
    from aegis.ferramentas import trabalho as mod_trabalho
    from aegis.ferramentas.trabalho import reverter_entrega

    chamado = {"n": 0}

    def _run_nao_chamado(*args, **kwargs):
        chamado["n"] += 1
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    repo, _ = _repo_git(tmp_path)
    monkeypatch.setattr(mod_trabalho, "RAIZ", repo)
    monkeypatch.setattr(mod_trabalho.subprocess, "run", _run_nao_chamado)
    saida = reverter_entrega.invoke({"sha": "abc; rm -rf /"})
    assert "ERRO_FERRAMENTA" in saida and "sha inválido" in saida
    assert chamado["n"] == 0, "git não pode ser chamado com sha não validado"


# ---------------------------------------------------------------------
# Replay do turno (gsd-forensics)
# ---------------------------------------------------------------------

def test_replay_turno_deterministico(tmp_path, monkeypatch):
    """Re-executa calculadora(2+2) com os MESMOS args — saída idêntica."""
    from aegis.config import config
    from aegis.ferramentas.trabalho import replay_turno

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(config, "banco", cfg.banco)
    monkeypatch.setattr(config, "thread_id", cfg.thread_id)
    modelo = ModeloFake()
    modelo.configurar([
        chamada_tool("calculadora", {"expressao": "2+2"}, id_chamada="call_r1"),
        AIMessage(content="2 + 2 = 4"),
        _resposta_verificacao("ok", [{"fonte": "calculadora", "conferida": True,
                                      "observacao": "bate"}]),
        _resposta_licoes([]),
    ])
    app = montar_grafo(modelo, basico_tools(),
                       checkpointer=criar_checkpointer_sync(cfg.banco),
                       store=criar_store_sync(cfg.banco), cfg=cfg)
    app.invoke({"mensagens": [HumanMessage("calcule 2+2")],
                "metadados_sessao": {"thread_id": cfg.thread_id}},
               config={"configurable": {"thread_id": cfg.thread_id}})
    saida = replay_turno.invoke({"limite": 8})
    assert "✓ igual" in saida
    assert "Resumo: 1 idêntica(s), 0 diferente(s)" in saida


def test_replay_turno_detecta_diferenca(tmp_path, monkeypatch):
    """Estado com resultado forjado → o reprodutor aponta o DIFERENTE."""
    from aegis.ferramentas import trabalho as mod_trabalho
    from aegis.ferramentas.trabalho import replay_turno

    monkeypatch.setattr(mod_trabalho, "_estado_da_thread", lambda thread: {
        "registros_ferramentas": [
            {"nome": "calculadora", "args": {"expressao": "2+2"},
             "resultado": "6 (forjado no estado)"},
        ],
    })
    saida = replay_turno.invoke({"limite": 8})
    assert "✗ DIFERENTE" in saida
    assert "Resumo: 0 idêntica(s), 1 diferente(s)" in saida


def test_replay_turno_sem_registros(tmp_path, monkeypatch):
    from aegis.ferramentas import trabalho as mod_trabalho
    from aegis.ferramentas.trabalho import replay_turno

    monkeypatch.setattr(mod_trabalho, "_estado_da_thread", lambda thread: {})
    saida = replay_turno.invoke({"limite": 8})
    assert "Nenhum registro" in saida


def test_registro_ferramentas_trabalho_tem_4_tools():
    from aegis.ferramentas.trabalho import ferramentas_trabalho

    nomes = [t.name for t in ferramentas_trabalho()]
    assert nomes == ["pausar_trabalho", "retomar_trabalho",
                     "reverter_entrega", "replay_turno"]