"""G4 — Aprendizados estruturados e versionados + grafo de conhecimento.

Eleva a C1: além da Store (recall rápido), cada lição da reflexão pós-turno
é classificada em 4 categorias (decisão, lição, padrão, surpresa — paridade
`LEARNINGS.md` do GSD), gravada em `docs/learnings/<sessao>.md` (versionado,
acoplado ao repo) e indexada num grafo de conhecimento consultável pela tool
`consultar_grafo` (sem LLM, sem rede — extração por regras).

O grafo persiste em `config/dados/grafo_conhecimento.json` (gitignored) e
navega por relação: entidades que compartilham ferramenta/fase/erro/categoria
aparecem como "relacionadas" na consulta.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

CATEGORIAS: tuple[str, ...] = ("decisao", "licao", "padrao", "surpresa")

_PADROES_CATEGORIA: tuple[tuple[str, str], ...] = (
    ("decisao", r"decidimos|decis[ãa]o|escolhemos|optamos|adotamos|vamos usar|vamos adotar|definimos"),
    ("surpresa", r"surpresa|inesperad|n[ãa]o esper[áa]vamos|imprevisto|contra-intuitiv|pegou|por qu[êe]"),
    ("padrao", r"padr[ãa]o|sempre que|geralmente|recorrente|repetiu|habitual|normalmente|toda vez"),
    ("licao", r"li[çc][ãa]o|aprendemos|nunca mais|falha|erro|quebrou|n[ãa]o repetir|cuidado"),
)


def classificar(texto: str) -> str:
    """Classifica um aprendizado em uma das 4 categorias, por regras."""
    t = (texto or "").lower()
    for categoria, padrao in _PADROES_CATEGORIA:
        if re.search(padrao, t):
            return categoria
    return "licao"


def nome_arquivo_sessao(thread_id: str) -> str:
    """Nome de arquivo seguro a partir do thread_id (sanitização)."""
    return re.sub(r"[^\w.-]", "_", thread_id or "default") or "default"


def bloco_markdown(licoes: list[tuple[str, str, str]],
                   ts: str | None = None) -> str:
    """Bloco markdown das lições: [(texto, prioridade, categoria)]."""
    ts = ts or time.strftime("%Y-%m-%d %H:%M:%S")
    linhas = [f"## {ts}", ""]
    for texto, prioridade, categoria in licoes:
        linhas.append(f"- **[{categoria}]** *(prioridade {prioridade})* {texto}")
    linhas.append("")
    return "\n".join(linhas)


class GrafoConhecimento:
    """Grafo consultável de aprendizados (entidades + relações derivadas).

    Persistido em JSON. Extração por regras — sem LLM, sem rede.
    """

    def __init__(self, caminho: str | Path | None = None) -> None:
        self.caminho = Path(caminho) if caminho else None
        self.entidades: dict[str, dict] = {}
        if self.caminho and self.caminho.exists():
            self._carregar()

    def _carregar(self) -> None:
        try:
            dados = json.loads(self.caminho.read_text(encoding="utf-8"))
            self.entidades = dados.get("entidades", {})
        except (OSError, json.JSONDecodeError):
            self.entidades = {}

    def adicionar(self, categoria: str, texto: str, *, ferramenta: str = "",
                  fase: str = "", erro: str = "") -> str:
        """Registra um aprendizado no grafo; retorna o id da entidade."""
        id_ent = f"{categoria}:{abs(hash(texto)) % 10**9}:{int(time.time_ns())}"
        self.entidades[id_ent] = {
            "categoria": categoria,
            "texto": (texto or "")[:300],
            "ferramenta": ferramenta,
            "fase": fase,
            "erro": erro,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        return id_ent

    @staticmethod
    def _casa(entidade: dict, termo: str) -> bool:
        return (termo in entidade["texto"].lower()
                or termo in entidade["categoria"]
                or termo in entidade["ferramenta"].lower()
                or termo in entidade["fase"].lower()
                or termo in entidade["erro"].lower())

    def consultar(self, termo: str, limite: int = 8) -> list[dict]:
        """Entidades que casam `termo` + relacionadas (mesmos atributos)."""
        termo = (termo or "").strip().lower()
        if not termo:
            return []
        casadas = {k: v for k, v in self.entidades.items() if self._casa(v, termo)}
        relacionadas: dict[str, dict] = {}
        for v in casadas.values():
            for k2, v2 in self.entidades.items():
                if k2 in casadas or k2 in relacionadas:
                    continue
                if (v2["ferramenta"] and v2["ferramenta"] == v["ferramenta"]) \
                        or (v2["fase"] and v2["fase"] == v["fase"]) \
                        or (v2["erro"] and v2["erro"] == v["erro"]) \
                        or v2["categoria"] == v["categoria"]:
                    relacionadas[k2] = v2
        diretas = [{"tipo": "direta", **v} for v in casadas.values()][:limite]
        rel = [{"tipo": "relacionada", **v} for v in relacionadas.values()][:limite]
        return diretas + rel

    def formatar(self, termo: str, limite: int = 8) -> str:
        """Consulta formatada para a tool (sem rede)."""
        itens = self.consultar(termo, limite)
        if not itens:
            return "Nada encontrado no grafo de conhecimento para esse termo."
        linhas = [f"Grafo de conhecimento — consulta por '{termo}':"]
        for i in itens:
            tag = "🔗" if i["tipo"] == "relacionada" else "•"
            extra = " | ".join(x for x in (i["ferramenta"], i["fase"], i["erro"]) if x)
            suf = f" ({extra})" if extra else ""
            linhas.append(f"{tag} [{i['categoria']}] {i['texto']}{suf}")
        return "\n".join(linhas)

    def salvar(self) -> None:
        if not self.caminho:
            return
        self.caminho.parent.mkdir(parents=True, exist_ok=True)
        self.caminho.write_text(
            json.dumps({"entidades": self.entidades}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
