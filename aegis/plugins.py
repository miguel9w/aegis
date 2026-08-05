"""
Carregamento dinâmico de módulos de ferramentas (plugins) em Python.

Módulos em `extensions/plugins/*.py` devem expor uma função
`registrar() -> list[BaseTool] | BaseTool`. O Aegis importa-os em runtime
e permite **recarga dinâmica** (re-importação) sem reiniciar o grafo,
fechando o ciclo de "agente escreve código → recarrega → usa".
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

from .config import RAIZ

# Diretório padrão de plugins (fora do pacote, em `extensions/`)
_DIR_PADRAO = RAIZ / "extensions" / "plugins"

_estado_cache: dict[str, types.ModuleType] = {}
_erros_carregamento: list[str] = []


def _plugins_dir(diretorio: str | Path | None = None) -> Path:
    return Path(diretorio) if diretorio else _DIR_PADRAO


def _listar_modulos(diretorio: Path) -> list[Path]:
    if not diretorio.is_dir():
        return []
    return sorted(p for p in diretorio.glob("*.py") if p.name != "__init__.py")


def _executar_registrar(mod: types.ModuleType, nome_arq: str, ferramentas: list) -> None:
    """Chama `registrar()` do módulo e coleta as ferramentas."""
    fez = getattr(mod, "registrar", None)
    if not callable(fez):
        return
    try:
        registradas = fez()
        if registradas is None:
            return
        if not isinstance(registradas, (list, tuple)):
            registradas = [registradas]
        ferramentas.extend(registradas)
    except Exception as exc:  # noqa: BLE001 — resiliência: um plugin ruim não derruba tudo
        _erros_carregamento.append(f"{nome_arq}: {exc}")


def _importar(nome: str, caminho: Path) -> types.ModuleType:
    """Importa um módulo de arquivo e o registra no sys.modules."""
    spec = importlib.util.spec_from_file_location(nome, caminho)
    if spec is None or spec.loader is None:
        raise ImportError(f"não foi possível criar spec para {caminho}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nome] = mod
    spec.loader.exec_module(mod)
    return mod


def _nome_modulo(stem: str) -> str:
    """Nome plano e único no sys.modules (reload confiável sem pacote pai)."""
    return f"_aegis_plugin_{stem}"


def carregar_plugins(diretorio: str | Path | None = None) -> list:
    """Importa todos os plugins e coleta as ferramentas expostas por `registrar()`."""
    _erros_carregamento.clear()
    _estado_cache.clear()

    ferramentas: list = []
    base = _plugins_dir(diretorio)
    for modulo_path in _listar_modulos(base):
        nome = _nome_modulo(modulo_path.stem)
        try:
            mod = _importar(nome, modulo_path)
        except Exception as exc:  # noqa: BLE001
            _erros_carregamento.append(f"{modulo_path.name}: {exc}")
            continue
        _estado_cache[nome] = mod
        _executar_registrar(mod, modulo_path.name, ferramentas)
    return ferramentas


def recarregar_plugins(diretorio: str | Path | None = None) -> list:
    """Recarrega os plugins (re-importa o código atualizado do disco).

    Cada plugin é re-importado com um spec novo, aplicando mudanças feitas
    enquanto o agente estava em execução (auto-evolução em runtime).
    """
    _erros_carregamento.clear()
    ferramentas: list = []
    base = _plugins_dir(diretorio)
    for modulo_path in _listar_modulos(base):
        nome = _nome_modulo(modulo_path.stem)
        try:
            mod = _importar(nome, modulo_path)  # spec novo → código atual
        except Exception as exc:  # noqa: BLE001
            _erros_carregamento.append(f"{modulo_path.name}: {exc}")
            continue
        _estado_cache[nome] = mod
        _executar_registrar(mod, modulo_path.name, ferramentas)
    return ferramentas


def erros_carregamento() -> list[str]:
    """Retorna erros de plugins que falharam ao carregar (para auditoria)."""
    return list(_erros_carregamento)