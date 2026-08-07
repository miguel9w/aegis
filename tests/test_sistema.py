"""Testes do prompt de sistema (regras de uso de ferramentas, anti-loop)."""

from aegis.prompts import sistema


def test_sistema_tem_regras_do_loop():
    texto = sistema(None, "", [])
    assert "blocos pequenos" in texto
    assert "ferramenta falhar 3 vezes" in texto
    assert "gerar o arquivo inteiro em um único comando" in texto


def test_sistema_mantem_identidade_e_ferramentas():
    texto = sistema(None, "", [])
    assert "Você é o **Aegis**" in texto
    assert "Ferramentas disponíveis" in texto


def test_sistema_inclui_metadados_quando_dados():
    texto = sistema(None, "", [], metadados={"thread_id": "x1"})
    assert "Metadados de sessão" in texto
    assert "x1" in texto