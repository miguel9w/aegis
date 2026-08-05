"""Plugin de exemplo — demonstra como criar novos módulos de ferramentas.

Cada plugin em `extensions/plugins/*.py` deve expor `registrar()`
retornando uma ou mais ferramentas. O Aegis importa dinamicamente e permite
recarga em runtime (`recarregar_plugins`).
"""

from __future__ import annotations

from langchain_core.tools import tool


@tool
def contar_palavras(texto: str) -> str:
    """Conta o número de palavras e caracteres de um texto.

    Exemplo: "Olá mundo" -> 2 palavras, 9 caracteres.
    """
    palavras = len(texto.split())
    caracteres = len(texto)
    return f"{palavras} palavras, {caracteres} caracteres."


@tool
def reverter_texto(texto: str) -> str:
    """Inverte a ordem dos caracteres de um texto (ex.: 'abc' -> 'cba')."""
    return texto[::-1]


def registrar() -> list:
    return [contar_palavras, reverter_texto]