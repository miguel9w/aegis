"""
Testes do Formato de Prompt Avançado (APF — aegis/prompts_avancados.py).

O formato é JSON5-lite: JSON válido + comentários (`//` e `#`), vírgulas
pendentes e variáveis `${chave}` interpoladas a partir do bloco `variaveis`.
"""

from __future__ import annotations

import json

import pytest

from aegis import prompts_avancados as paf
from aegis.config import config

# ---------------------------------------------------------------- fixadores

FICHA_VALIDA = """\
{
  // identificador único — usado em /prompt <id>
  "id": "revisor-codigo",
  "versao": "1.1.0",
  "descricao": "Revisa código com foco em bugs e segurança",
  "sistema": "Você é um revisor sênior. Idioma-alvo: ${linguagem}.",
  "instrucoes": [
    "Aponte bugs concretos com trecho do código.",  # lista
    "Sugira correções para cada ponto."
  ],
  "variaveis": { "linguagem": "pt-BR" },
  "restricoes": ["Não invente APIs inexistentes.", "Sem elogios genéricos."],
  "formato_saida": { "tipo": "markdown", "secoes": ["Problemas", "Sugestões"] },
  "exemplos": [
    { "entrada": "def f(): pass", "saida": "Sem bug aparente." }
  ],  // trailing comma ok
}
"""


@pytest.fixture
def dir_prompts(monkeypatch, tmp_path):
    d = tmp_path / "prompts_avancados"
    d.mkdir()
    monkeypatch.setattr(config, "prompts_avancados_dir", d)
    monkeypatch.setattr(config, "prompt_ativo_path", tmp_path / "prompt_ativo.json")
    return d


def _escrever(d, nome: str, texto: str):
    (d / nome).write_text(texto, encoding="utf-8")


def _mudar(variaveis: dict, extra: dict) -> dict:
    return {**variaveis, **extra}


# ------------------------------------------------------------ sanitizador
class TesteSanitizador:
    def test_remove_comentarios_e_virgulas_pendentes(self):
        saida = paf.sanitizar_json5(FICHA_VALIDA)
        assert "//" not in saida
        assert "#" not in saida
        assert ", }" not in saida
        assert json.loads(saida)["id"] == "revisor-codigo"

    def test_preserva_url_com_slashes_e_hash_em_string(self):
        texto = '{"url": "https://x.org/a//b#frag", "nota": "ok"}'
        saida = paf.sanitizar_json5(texto)
        assert "https://x.org/a//b#frag" in saida
        assert json.loads(saida)["url"] == "https://x.org/a//b#frag"

    def test_virgula_pendente_dentro_de_string_preservada(self):
        texto = '{"a": "b, c", "d": 1,}'
        saida = paf.sanitizar_json5(texto)
        assert json.loads(saida) == {"a": "b, c", "d": 1}

    def test_aceita_json_puro(self):
        saida = paf.sanitizar_json5('{"id": "x"}')
        assert json.loads(saida)["id"] == "x"


# ----------------------------------------------------------------- carga
class TesteCarga:
    def test_carrega_ficha_valida(self, dir_prompts):
        _escrever(dir_prompts, "revisor-codigo.apf", FICHA_VALIDA)
        catalog = paf.carregar_prompts_avancados()
        assert "revisor-codigo" in catalog
        assert catalog["revisor-codigo"]["versao"] == "1.1.0"
        assert paf.erros_de_carga() == []

    def test_ficha_quebrada_nao_derruba_catalogo(self, dir_prompts):
        _escrever(dir_prompts, "revisor-codigo.apf", FICHA_VALIDA)
        _escrever(dir_prompts, "quebrada.apf", '{"id": "quebrada",')
        _escrever(dir_prompts, "sem_conteudo.apf", '{"id": "vazia", "versa": "1.0.0"}')
        catalog = paf.carregar_prompts_avancados()
        assert "revisor-codigo" in catalog
        assert "quebrada" not in catalog
        assert "sem_conteudo" not in catalog
        erros = paf.erros_de_carga()
        assert len(erros) == 2
        assert any("quebrada" in e for e in erros)
        assert any("sem_conteudo" in e for e in erros)

    def test_tipos_invalidos_viram_erro(self, dir_prompts):
        _escrever(dir_prompts, "errada.apf", '{"id": "errada", "sistema": "x", "instrucoes": "nao-lista"}')
        paf.carregar_prompts_avancados()
        assert any("instrucoes" in e for e in paf.erros_de_carga())

    def test_diretorio_ausente_retorna_vazio(self, monkeypatch, tmp_path):
        monkeypatch.setattr(config, "prompts_avancados_dir", tmp_path / "nao_existe")
        assert paf.carregar_prompts_avancados() == {}


# --------------------------------------------------------- compilação
class TesteCompilar:
    def test_bloco_contem_todo_o_conteudo(self, dir_prompts):
        _escrever(dir_prompts, "revisor-codigo.apf", FICHA_VALIDA)
        bloco = paf.compilar_prompt("revisor-codigo")
        assert "revisor-codigo" in bloco and "1.1.0" in bloco
        assert "Idioma-alvo: pt-BR" in bloco
        assert "1. Aponte bugs concretos" in bloco
        assert "2. Sugira correções" in bloco
        assert "- Não invente APIs" in bloco
        assert "markdown" in bloco and "Problemas" in bloco
        assert "def f(): pass" in bloco and "Sem bug aparente" in bloco

    def test_variaveis_extras_sobrepoem(self, dir_prompts):
        _escrever(dir_prompts, "revisor-codigo.apf", FICHA_VALIDA)
        bloco = paf.compilar_prompt("revisor-codigo", extras={"linguagem": "Python"})
        assert "Idioma-alvo: Python" in bloco

    def test_id_inexistente_lanca_erro(self, dir_prompts):
        with pytest.raises(paf.PromptFormatoErro, match="revisor-codigo"):
            paf.compilar_prompt("revisor-codigo")

    def test_interpolacao_tambem_nas_instrucoes(self, dir_prompts):
        texto = (
            '{"id": "oficina", "variaveis": {"linguagem": "Rust"},'
            '"sistema": "Olá ${linguagem}", "instrucoes": ["Use ${linguagem} moderno."]}'
        )
        _escrever(dir_prompts, "oficina.apf", texto)
        bloco = paf.compilar_prompt("oficina")
        assert "Use Rust moderno" in bloco


# ---------------------------------------------------------- ativação
class TesteAtivacao:
    def test_usar_prompt_ativa_e_compila(self, dir_prompts):
        _escrever(dir_prompts, "revisor-codigo.apf", FICHA_VALIDA)
        saida = paf.usar_prompt("revisor-codigo")
        assert "ativo" in saida
        assert paf.prompt_ativo_id() == "revisor-codigo"
        assert "revisor-codigo" in paf.prompt_ativo_compilado()

    def test_usar_prompt_inexistente_lanca(self, dir_prompts):
        with pytest.raises(paf.PromptFormatoErro, match="fantasma"):
            paf.usar_prompt("fantasma")

    def test_desativar_limpa(self, dir_prompts):
        _escrever(dir_prompts, "revisor-codigo.apf", FICHA_VALIDA)
        paf.usar_prompt("revisor-codigo")
        paf.desativar_prompt()
        assert paf.prompt_ativo_id() is None
        assert paf.prompt_ativo_compilado() == ""

    def test_prompt_ativo_sem_catalogo_volta_vazio(self, dir_prompts, monkeypatch):
        monkeypatch.setattr(config, "prompts_avancados_dir", dir_prompts.parent / "outro")
        config.prompt_ativo_path.write_text(json.dumps({"id": "sumiu"}), encoding="utf-8")
        assert paf.prompt_ativo_compilado() == ""
        assert paf.prompt_ativo_id() == "sumiu"


# ----------------------------------------------------------- listagem
class TesteListagem:
    def test_listar_mostra_ids_descricoes(self, dir_prompts):
        _escrever(dir_prompts, "revisor-codigo.apf", FICHA_VALIDA)
        saida = paf.listar_prompts()
        assert "revisor-codigo" in saida and "1.1.0" in saida
        assert "Revisa código" in saida

    def test_listar_avisa_sobre_ficha_quebrada(self, dir_prompts):
        _escrever(dir_prompts, "quebrada.apf", "{sistema: sem aspas}")
        saida = paf.listar_prompts()
        assert "1 ficha com erro" in saida

    def test_ver_prompt_marca_ativo(self, dir_prompts):
        _escrever(dir_prompts, "revisor-codigo.apf", FICHA_VALIDA)
        paf.usar_prompt("revisor-codigo")
        saida = paf.ver_prompt("revisor-codigo")
        assert "(ativo)" in saida
        saida2 = paf.ver_prompt("revisor-codigo")  # continua ativo
        assert "(ativo)" in saida2

    def test_ver_prompt_de_outro_nao_marca(self, dir_prompts):
        _escrever(dir_prompts, "revisor-codigo.apf", FICHA_VALIDA)
        assert "(ativo)" not in paf.ver_prompt("revisor-codigo")


# --------------------------------------------------------- integração
class TesteIntegracao:
    def test_sistema_inclui_prompt_ativo(self, dir_prompts):
        from aegis import prompts as mod
        _escrever(dir_prompts, "revisor-codigo.apf", FICHA_VALIDA)
        paf.usar_prompt("revisor-codigo")
        sistema = mod.sistema(None, "", [])
        assert "Prompt Avançado" in sistema and "revisor-codigo" in sistema
        paf.desativar_prompt()
        sistema_sem = mod.sistema(None, "", [])
        assert "Prompt Avançado" not in sistema_sem


# --------------------------------------------------------------- tools
class TesteTools:
    def test_tools_registradas(self):
        from langchain_core.tools import BaseTool
        for ferramenta in (paf.listar_prompts_avancados,
                           paf.usar_prompt_avancado,
                           paf.ver_prompt_avancado):
            assert isinstance(ferramenta, BaseTool)