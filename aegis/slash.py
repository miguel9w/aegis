"""
Comandos de barra `/` — dispatcher puro para a TUI e o CLI.

`parsear_slash('/nome arg')` → ('nome', 'arg')
`executar_slash('nome', 'arg')` → texto de resposta (comandos de app
retornam marcador `@@ACAO:Sair|Limpar|Novo`).

Todo comando reusa funções reais do Aegis (papeis, memória, plano, notas,
vault e científico) — sem duplicação de lógica.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .camel_kit import (anotar, atualizar_plano, pensar, planejar_tarefa,
                        ver_notas, ver_pensamento, ver_plano)
from .config import config
from .ferramentas import carregar_ferramentas
from .memoria_camel import (carregar_memoria, consultar_memoria_camel,
                            esquecer_memoria_camel, registrar_memoria_camel)
from .papeis import (definir_papel, especificar_tarefa, estruturar_tarefa,
                     ler_papel_ativo, ler_tarefa_atual, listar_papeis)

# ---- Registro dos comandos ------------------------------------------------
# Chave: lista de apelidos? Não — nome único; 'app' indica ação de TUI.

IMPLEMENTADOS: dict[str, str] = {}
"""nome → descrição curta"""
APP_AÇÕES = {"sair", "limpar", "novo"}


def _registrar(nome: str, descricao: str):
    IMPLEMENTADOS[nome] = descricao


_registrar("ajuda", "Mostra a lista de comandos")
_registrar("sair", "Encerra a TUI (ação da interface)")
_registrar("limpar", "Limpa o chat atual (ação da interface)")
_registrar("novo", "Inicia nova sessão (ação da interface)")
_registrar("status", "Resumo do estado do agente")
_registrar("config", "Caminhos de configuração ativos")
_registrar("papel", "Ver o papel ativo")
_registrar("papeis", "Listar papéis disponíveis")
_registrar("definir_papel", "<nome> — define o papel ativo")
_registrar("tarefa", "Tarefa especificada atual")
_registrar("planejar", "<texto> — estrutura e grava tarefa")
_registrar("plano", "Plano de tarefas corrente")
_registrar("marcar", "<id>=<status> — status de passo do plano")
_registrar("pensar", "<passo> — registra raciocínio")
_registrar("pensamento", "Cadeia de raciocínio atual")
_registrar("anotar", "<nota> — registra nota rápida")
_registrar("notas", "[N] — últimas N notas")
_registrar("memoria", "[consulta] — memória pontuada")
_registrar("salvar_memoria", "<fato>[>=importancia] — salva memória")
_registrar("esquecer", "<id> — apaga registro da memória")
_registrar("ferramentas", "Lista de ferramentas registradas")
_registrar("criar_nota", "<nome> > <conteudo> — nota no vault")
_registrar("ver_nota", "<nome> — lê nota do vault")
_registrar("buscar_nota", "<palavra> — fulltext no vault")
_registrar("tag", "<tag> — notas com a tag")
_registrar("buscar_paper", "<consulta> — busca no arXiv")
_registrar("salvar_paper", "<id> — salva paper (biblioteca+vault)")
_registrar("bibtex", "<id> — citação BibTeX de paper salvo")
_registrar("revisar", "<consulta> — revisão de literatura")
_registrar("obsidian", "Lista o vault Obsidian (árvore por subpasta)")
_registrar("prompt", "[id|nenhum] — ativa/mostra o prompt avançado (APF)")
_registrar("prompts", "Lista os prompts avançados disponíveis (APF)")


def parsear_slash(texto: str) -> tuple[str, str] | None:
    """Splita '/nome arg' (None se não for slash)."""
    if not texto or not texto.startswith("/"):
        return None
    partes = texto.strip().split(None, 1)
    nome = partes[0][1:].strip().lower() if partes else ""
    arg = partes[1].strip() if len(partes) > 1 else ""
    return nome, arg


# --------------------------------------------------------------------------
# Executor — o dispatcher central
# --------------------------------------------------------------------------

def executar_slash(nome: str, arg: str = "") -> str:
    """Execute o comando e devolve o texto de resposta."""
    nome = nome.strip().lower()
    if not nome:
        return "(comando vazio — use /ajuda)"
    if nome in APP_AÇÕES:
        return f"@@ACAO:{nome}"
    if nome not in IMPLEMENTADOS:
        return f"comando desconhecido: /{nome} — use /ajuda"
    try:
        return _EXECUTOR(nome, arg)
    except Exception as erro:  # noqa: BLE001 — slash nunca derruba a UI
        return f"erro no /{nome}: {erro}"


def _EXECUTOR(nome: str, arg: str) -> str:
    if nome == "ajuda":
        linhas = ["Comandos (com barra):"]
        for cmd, desc in sorted(IMPLEMENTADOS.items()):
            linhas.append(f"  `/{cmd}` — {desc}")
        return "\n".join(linhas)

    if nome == "status":
        return _status()
    if nome == "config":
        return _config()
    if nome == "papel":
        return _papel()
    if nome == "papeis":
        return listar_papeis.invoke({})
    if nome == "definir_papel":
        return definir_papel.invoke({"nome": arg})
    if nome == "tarefa":
        return _tarefa()
    if nome == "planejar":
        if not arg:
            raise ValueError("use /planejar <objetivo; restrição; critério…>")
        return especificar_tarefa.invoke(estruturar_tarefa.invoke({"texto_livre": arg}))
    if nome == "plano":
        return ver_plano.invoke({})
    if nome == "marcar":
        return _marcar(arg)
    if nome == "pensar":
        return pensar.invoke({"passo_raciocinio": arg})
    if nome == "pensamento":
        return ver_pensamento.invoke({})
    if nome == "anotar":
        return anotar.invoke({"nota": arg})
    if nome == "notas":
        qtd = int(arg) if arg.isdigit() else 10
        return ver_notas.invoke({"qtd": qtd})
    if nome == "memoria":
        return _memoria(arg)
    if nome == "salvar_memoria":
        return _salvar_memoria(arg)
    if nome == "esquecer":
        return esquecer_memoria_camel.invoke({"id_registro": arg})
    if nome == "ferramentas":
        return _ferramentas()
    if nome == "criar_nota":
        return _criar_nota(arg)
    if nome == "ver_nota":
        from .obsidian import ler_nota_obsidian
        return ler_nota_obsidian(arg)
    if nome == "buscar_nota":
        from .obsidian import buscar_nota_obsidian
        return buscar_nota_obsidian(arg)
    if nome == "tag":
        from .obsidian import notas_por_tag_obsidian
        return notas_por_tag_obsidian(arg)
    if nome == "buscar_paper":
        from .cientificas import buscar_papers_arxiv
        return buscar_papers_arxiv.invoke({"consulta": arg})
    if nome == "salvar_paper":
        from .cientificas import salvar_paper
        return salvar_paper.invoke({"id_arxiv": arg})
    if nome == "bibtex":
        from .cientificas import gerar_citacao_bibtex
        return gerar_citacao_bibtex.invoke({"id_arxiv": arg})
    if nome == "revisar":
        from .cientificas import revisar_literatura
        return revisar_literatura.invoke({"consulta": arg})
    if nome == "obsidian":
        from .obsidian import listar_obsidian_vault
        return listar_obsidian_vault()
    if nome == "prompts":
        from .prompts_avancados import listar_prompts
        return listar_prompts()
    if nome == "prompt":
        from .prompts_avancados import (desativar_prompt, prompt_ativo_id,
                                        usar_prompt, ver_prompt)
        if not arg:
            id_ativo = prompt_ativo_id()
            if not id_ativo:
                return "nenhum prompt avançado ativo (veja /prompts)"
            return ver_prompt(id_ativo)
        if arg in ("nenhum", "off"):
            return desativar_prompt()
        return usar_prompt(arg)
    return f"comando /{nome} não tem executor"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def _status() -> str:
    ferramentas = carregar_ferramentas()
    linhas = [f"🤖 Aegis · modelo {config.modelo}"]
    linhas.append(f"🔧 {len(ferramentas)} ferramentas registradas")
    papel = ler_papel_ativo()
    linhas.append(f"👤 papel ativo: {papel or 'assistente (padrão)'}")
    try:
        dados = json.loads(Path(config.tarefas_path).read_text(encoding="utf-8"))
        pendentes = sum(1 for t in dados.get("tarefas", []) if t.get("status") != "concluida")
        linhas.append(f"📌 {pendentes} tarefas pendentes")
    except Exception:  # noqa: BLE001
        pass
    return "\n".join(linhas)


def _config() -> str:
    itens = [
        ("sqlite", config.banco),
        ("dados JSON", config.dados_dir),
        ("vault obsidian", config.obsidian_dir),
        ("biblioteca", config.biblioteca_path),
        ("papel ativo", config.papel_ativo_path),
        ("tarefa atual", config.tarefa_atual_path),
    ]
    return "\n".join(f"{nome}: `{caminho}`" for nome, caminho in itens)


def _papel() -> str:
    nome = ler_papel_ativo()
    if not nome:
        return "(papel padrão: assistente — /papeis lista todos)"
    return f"Papel ativo: **{nome}** (definido via /definir_papel)"


def _tarefa() -> str:
    tarefa = ler_tarefa_atual()
    if not tarefa:
        return "(nenhuma tarefa especificada — use /planejar)"
    return f"Objetivo: {tarefa.get('objetivo', '')}"


def _marcar(arg: str) -> str:
    from .camel_kit import atualizar_plano
    if "=" in arg:
        id_passo, status = arg.split("=", 1)
    else:
        partes = arg.rsplit(None, 1)
        if len(partes) != 2:
            raise ValueError("uso: /marcar <id> <status> ou <id>=<status>")
        id_passo, status = partes
    return atualizar_plano.invoke({"id": id_passo.strip(), "novo_status": status.strip()})


def _memoria(arg: str) -> str:
    if not arg:
        registros = carregar_memoria()
        if not registros:
            return "(memória pontuada vazia — use /salvar_memoria <fato>)"
        linhas = [f"🧠 {len(registros)} registros na memória pontuada"]
        for r in registros[-10:]:
            linhas.append(f"- [{r.id}] ({r.importancia:.1f}) — {r.conteudo[:70]}")
        return "\n".join(linhas)
    return consultar_memoria_camel.invoke({"consulta": arg})


def _salvar_memoria(arg: str) -> str:
    if not arg:
        raise ValueError("uso: /salvar_memoria <fato> [>= importância]")
    importancia = 5.0
    if ">=" in arg:
        arg, _, imp = arg.partition(">=")
        try:
            importancia = float(imp.strip())
        except ValueError:
            importancia = 5.0
    return registrar_memoria_camel.invoke(
        {"conteudo": arg.strip(), "importancia": importancia})


def _ferramentas() -> str:
    ferramentas = carregar_ferramentas()
    linhas = [f"🔧 {len(ferramentas)} ferramentas registradas:"]
    for f in ferramentas:
        linhas.append(f"- **{f.name}** — {f.description.split('.')[0]}")
    return "\n".join(linhas)


def _criar_nota(arg: str) -> str:
    from .obsidian import criar_nota_obsidian
    if ">" not in arg:
        raise ValueError("uso: /criar_nota <nome> > <conteúdo>")
    nome, _, conteudo = arg.partition(">")
    if not nome.strip():
        raise ValueError("nome da nota é obrigatório")
    return criar_nota_obsidian(nome.strip(), conteudo.strip())