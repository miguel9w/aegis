"""Testes da Fase X2 — Skills/playbooks (memória procedimental versionada).

Cobre: registro dinâmico sem reiniciar, tool `carregar_skill` (catálogo por
descrição + injeção do corpo com teto de tokens), frontmatter com gatilho,
skill inválida ignorada com aviso (nunca quebra), RAG-lite ranqueando pela
descrição e o aceite (revisar-codigo do repo carregável).
"""

from __future__ import annotations

from aegis import skills as sk
from aegis.config import config
from aegis.recuperacao import pesquisar_memoria
from aegis.skills import carregar_e_expor, carregar_skills, criar_skill_path

REPO = "extensions/skills"


def _skill(tmp_path, nome="demo", corpo="Passo 1: faça X.\nPasso 2: confira.", gatilho="tarefas de demo"):
    criar_skill_path(tmp_path, nome, f"Faz coisas de {nome}.", corpo, gatilho)
    return tmp_path


def _tool_carregar(diretorio):
    ferramentas = carregar_e_expor(diretorio)
    return {f.name: f for f in ferramentas}


# ---------------------------------------------------------------------
# Registro dinâmico (sem reiniciar)
# ---------------------------------------------------------------------

def test_skill_nova_registrada_sem_reiniciar(tmp_path):
    _skill(tmp_path)
    primeira = carregar_skills(tmp_path)
    assert "demo" in primeira

    # nova skill aparece numa SEGUNDA varredura (sem reiniciar o processo)
    criar_skill_path(tmp_path, "outra", "Outra coisa.", "Corpo.", "gatilho x")
    segunda = carregar_skills(tmp_path)
    assert set(segunda) == {"demo", "outra"}


def test_carregar_skill_tool_injeta_corpo(tmp_path, monkeypatch):
    _skill(tmp_path)
    monkeypatch.setattr(config, "skill_teto_tokens", 4000)
    tools = _tool_carregar(tmp_path)

    saida = tools["carregar_skill"].invoke({"nome": "demo"})
    assert "Passo 1: faça X." in saida
    assert "demo" in saida


# ---------------------------------------------------------------------
# Catálogo por descrição + gatilho
# ---------------------------------------------------------------------

def test_catalogo_lista_descricao_e_gatilho(tmp_path):
    _skill(tmp_path, gatilho="quando pedirem demo")
    tools = _tool_carregar(tmp_path)

    saida = tools["carregar_skill"].invoke({})
    assert "demo" in saida
    assert "Faz coisas de demo." in saida
    assert "quando pedirem demo" in saida


def test_gatilho_no_registro(tmp_path):
    _skill(tmp_path)
    habilidades = carregar_skills(tmp_path)
    assert habilidades["demo"]["gatilho"] == "tarefas de demo"


# ---------------------------------------------------------------------
# Teto de tokens
# ---------------------------------------------------------------------

def test_injecao_respeita_teto(tmp_path, monkeypatch):
    corpo_grande = ("palavra " * 5000)  # ~40k chars → ~10k tokens
    _skill(tmp_path, corpo=corpo_grande)
    monkeypatch.setattr(config, "skill_teto_tokens", 100)  # teto pequeno
    tools = _tool_carregar(tmp_path)

    saida = tools["carregar_skill"].invoke({"nome": "demo"})
    assert "truncado pelo teto de tokens" in saida
    # 100 tokens * 4 chars + marca de truncamento
    assert len(saida) < 100 * 4 + 400


def test_teto_zero_nao_trunca(tmp_path, monkeypatch):
    _skill(tmp_path)
    monkeypatch.setattr(config, "skill_teto_tokens", 0)
    tools = _tool_carregar(tmp_path)
    saida = tools["carregar_skill"].invoke({"nome": "demo"})
    assert "Passo 1: faça X." in saida
    assert "truncado" not in saida


def test_skill_irrelevante_nao_e_carregada(tmp_path):
    _skill(tmp_path)
    tools = _tool_carregar(tmp_path)
    saida = tools["carregar_skill"].invoke({"nome": "inexistente"})
    assert "Não encontrei a skill" in saida
    assert "Passo 1" not in saida


# ---------------------------------------------------------------------
# Frontmatter inválido → aviso, nunca quebra
# ---------------------------------------------------------------------

def test_frontmatter_invalido_ignorado_com_aviso(tmp_path):
    (tmp_path / "quebrada").mkdir()
    (tmp_path / "quebrada" / "SKILL.md").write_text(
        "---\nname: quebrada\ndescription: sem fechamento\ncorpo solto\n", encoding="utf-8"
    )
    _skill(tmp_path, nome="saudavel")

    avisos: list[str] = []
    habilidades = carregar_skills(tmp_path, avisos=avisos)
    assert "saudavel" in habilidades
    assert "quebrada" not in habilidades
    assert avisos and any("quebrada" in a for a in avisos)


def test_frontmatter_invalido_sem_avisos_nao_quebra(tmp_path):
    (tmp_path / "quebrada").mkdir()
    (tmp_path / "quebrada" / "SKILL.md").write_text("corpo sem frontmatter", encoding="utf-8")
    habilidades = carregar_skills(tmp_path)  # sem avisos: nunca levanta
    assert "quebrada" in habilidades or habilidades == {}  # usa o nome do diretório


# ---------------------------------------------------------------------
# RAG-lite ranqueia pela descrição (sem ler corpos)
# ---------------------------------------------------------------------

def test_raglite_ranqueia_pela_descricao(tmp_path, monkeypatch):
    import aegis.recuperacao as rec
    from aegis.memoria import criar_store_sync

    monkeypatch.setattr(rec, "STORE_ATUAL", criar_store_sync(tmp_path / "store.db"))
    # corpo VÁZIO: só a descrição existe — a busca deve achar mesmo assim
    monkeypatch.setattr(
        "aegis.skills.HABILIDADES_REGISTRADAS",
        {"analise-dados": {"descricao": "Limpeza e análise de dados com pandas.", "gatilho": "", "conteudo": ""}},
    )
    saida = rec.pesquisar_memoria.invoke({"consulta": "análise de dados pandas"})
    assert "skill:analise-dados" in saida


# ---------------------------------------------------------------------
# Aceite: revisar-codigo do repo é carregável
# ---------------------------------------------------------------------

def test_aceite_skills_do_repo_carregaveis():
    habilidades = carregar_skills(REPO)
    assert "revisar-codigo" in habilidades
    assert "pesquisa-tecnica" in habilidades
    assert "Veredito" in habilidades["revisar-codigo"]["conteudo"]
    # gatilho presente nas duas skills versionadas
    assert habilidades["revisar-codigo"]["gatilho"]
    assert habilidades["pesquisa-tecnica"]["gatilho"]

    tools = _tool_carregar(REPO)
    saida = tools["carregar_skill"].invoke({"nome": "revisar-codigo"})
    assert "bloqueadores" in saida


# ---------------------------------------------------------------------
# Exposição: só carregar_skill + criar_skill (sem N tools usar_skill_*)
# ---------------------------------------------------------------------

def test_exposicao_uma_tool_por_catalogo(tmp_path):
    _skill(tmp_path)
    _skill(tmp_path, nome="segunda")
    nomes = [f.name for f in carregar_e_expor(tmp_path)]
    assert nomes.count("carregar_skill") == 1
    assert nomes.count("criar_skill") == 1
    assert not any(n.startswith("usar_skill_") for n in nomes)


def test_criar_skill_com_gatilho_grava_frontmatter(tmp_path):
    destino = criar_skill_path(tmp_path, "nova-skill", "Desc.", "Corpo.", "disparo x")
    texto = destino.read_text(encoding="utf-8")
    assert "gatilho: disparo x" in texto
    habilidades = carregar_skills(tmp_path)
    assert habilidades["nova-skill"]["gatilho"] == "disparo x"


def test_ferramentas_skills_sem_habilidades(tmp_path):
    nomes = [f.name for f in carregar_e_expor(tmp_path)]
    assert "carregar_skill" in nomes  # catálogo vazio não quebra
