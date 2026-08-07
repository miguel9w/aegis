# Aegis Multi-Agente — Plano de Engenharia de Grafo

> **Status:** planejado (próxima execução). **Decisão do usuário:** dividir a tarefa em
> 3 agentes independentes (cada um com seu nó), um nó avaliador específico da tarefa
> e um orquestrador que comanda os nós. **Complemento:** pool de ferramentas.
>
> **Regra de ouro do projeto (anti-alucinação):** toda etapa entrega PROVA verificável
> (testes determinísticos com seeds/fakes, sem rede). Nada de "o LLM faria X" — o
> grafo é testado com respostas controladas.

---

## 1. O que existe hoje (contexto real, verificado)

- **`aegis/grafo.py`** — grafo cíclico de agente ÚNICO:
  `START → no_agente → (tool_calls? no_ferramentas → reflexão) | comprimir | fim → no_memoria → END`.
  Todas as ~115 ferramentas estão no mesmo nó.
- **`aegis/subagentes.py`** — 2 especialistas fixos (`pesquisa`, `redacao`) expostos como
  ferramentas (`delegar_pesquisa`/`delegar_redacao`); subgrafos stateless com prompt
  especialista, sem orquestração nem avaliação.
- **`aegis/ferramentas/__init__.py`** — registro linear (`ferramentas.append(...)`), sem pools.
- **Config relevante:** `cfg.subagentes_ativos`, `cfg.max_tentativas_correcao` (3),
  `criar_llm(config, **extra)` (`aegis/llm.py`).

**Diferença para o pedido:** não existe roteamento por domínio, nem decomposição da tarefa,
nem nós de especialistas em paralelo, nem avaliador, nem pools.

---

## 2. Arquitetura alvo (visão do grafo)

```
                              ┌────────────────────────────┐
            START ──────────▶ │ no_orquestrador (roteia)  │
                              └────────────┬───────────────┘
      ┌────────────────────────┬───────────┴───────────┬──────────────────────────┐
      │ tarefa simples         │ tarefa de domínio     │ tarefa grande/incerta    │
      ▼                        ▼                       ▼
  no_agente (fluxo        SUBGRAFO_<domínio>     no_decompositor
  atual, zero mudança)    (3 especialistas        (divide em ≤3 slots,
                          em paralelo)            grava `divisao`)
        └──────────────────────┬───────────────────────┘
                               ▼
                    no_avaliador (critério da tarefa)
                       ├─ aprovado → no_orquestrador.final → no_memoria → END
                       └─ reprovado + feedback → volta ao(s) especialista(s)
                               (≤ cfg.max_tentativas_correcao iterações)
```

### Subgrafo de domínio — "programar" (o exemplo do pedido)

```
no_orquestrador (LLM orquestrador)
   └─ decide: domínio=programacao, estratégia=paralelo-3
      └─ no_programa_estrutura  (arquivos/esqueleto/plan de arquivos)   ┐
         no_programa_implementa (lógica/funções por arquivo)            ├─ em PARALELO
         no_programa_testa      (testes + smoke runner)                 ┘
            └─ TODOS escrevem em estado `rascunhos[slot_i]`
      └─ no_avaliador_programacao (LLM crítico com critérios fixos)
            ├─ aprovado  → orquestrador consolida → resposta final
            └─ reprovado → feedback estruturado → re-executa os 3 (2ª iteração máx.)
```

**O que cada nó significa (pedido literal do usuário):**
- **3 agentes independentes, cada um com seu nó** → os 3 nós de especialistas do
  subgrafo, rodando em paralelo (o LangGraph excuta em paralelo nós que só dependem do
  estado de entrada — fan-out sem dependências), cada um com SUA pool de ferramentas
  e seu prompt (analista de estrutura / implementador / tester).
- **nó avaliador respectivo à tarefa** → um evaluador por domínio (critérios de
  programação ≠ pesquisa ≠ escrita), montado via `PromptTemplates` por domínio;
  emite `veredito = {status, nota, feedback, criterios_checados}`.
- **modelo orquestrador** → LLM que decide: (a) se a tarefa merece multi-agente,
  (b) o domínio, (c) a divisão em ≤3 slots, (d) consolidação final; configuração
  permite modelos separados (`AEGIS_MODELO_ORQUESTRADOR`, `AEGIS_MODELO_AVALIADOR`).

---

## 3. Pool de ferramentas (`aegis/ferramentas/pools.py` — novo)

Declarativo, por NOME da ferramenta (as ferramentas hoje são funções/BaseTool; o
atributo `name` é estável). O agente principal continua recebendo TUDO (zero
big-bang); os especialistas recebem só a sua fatia.

```python
POOLS: dict[str, set[str]] = {
    "geral": {"executar_comando", "ler_arquivo", "escrever_arquivo", "listar_arquivos",
              "pesquisar_memoria", "gerenciar_memoria", "pensar"},
    "programacao": POOLS["geral"] | {"executar_teste", "revisar_codigo", "instalar_ferramenta", ...},
    "pesquisa": POOLS["geral"] | {"buscar_papers_arxiv", "revisar_literatura", "gerar_citacao_bibtex",
                                  "delegar_pesquisa", ...},
    "escrita": POOLS["geral"] | {"delegar_redacao", "anotar", "ver_notas", ...},
    "obsidian": POOLS["geral"] | {"criar_nota", "ler_nota", "buscar_notas", "ligar_nota", ...},
}
def pool_da_lista(ferramentas, domínio) -> list
def registrar_pool(nome, nomes: set[str])  # plugins podem registrar pools próprios
```

Mecânica: `registrar()` no `__init__.py` continua uma única lista; `pools.py`
deriva fatias por `name in POOLS[dominio]`. Validação em teste:
`set(p.name for p in ferramentas_all) ⊇ union(po os)` — nada de nome órfão.

## 4. Arquivos novos/tocados

| Arquivo | Ação |
|---|---|
| `aegis/ferramentas/pools.py` | **novo** — POOLS + filtros |
| `aegis/multiagente.py` | **novo** — `montar_subgrafo_dominio()`: especialistas + avaliador (reusa `fabricar_nos` como subagentes.py faz) |
| `aegis/estado.py` | + `divisao: list[dict]`, `rascunhos: dict[str, str]`, `vereditos: list[dict]`, `dominio: str` |
| `aegis/grafo.py` | + nós `no_orquestrador`, `no_avaliador`; rota `no_orquestrador → subgrafo/legado` |
| `aegis/config.py` | + `multiagente_ativos`, `AEGIS_MODELO_ORQUESTRADOR`, `AEGIS_MODELO_AVALIADOR`, `AEGIS_POOLS` (csv) |
| `aegis/nos.py` | prompt de orquestrador/avaliador (ou `prompts.py`) |
| `aegis/subagentes.py` | `delegar_pesquisa/redacao` passam a usar pools (não quebram) |
| `aegis/tui.py` | painel lateral mostra orquestração (domínio, slots, vereditos) |
| `pixi.toml` | task `pools` (lista pools + contagens) |
| `tests/test_multiagente.py` | **novo** — ver seção 6 |

## 5. Config (env, defaults)

```
AEGIS_MULTIAGENTE=true            # liga o orquestrador
AEGIS_MODELO_ORQUESTRADOR=        # vazio = mesmo modelo do principal
AEGIS_MODELO_AVALIADOR=
AEGIS_MAX_ESPECIALISTAS=3         # fan-out máximo
AEGIS_GATILHOS_MULTIAGENTE=programar,projeto,sistema,site,documento,pesquisa
```

## 6. Testes (prova anti-alucinação — obrigatório por pedido do usuário)

1. **Pool íntegra sem furos:** `union(POOLS.values()) ⊆ nomes(ferramentas)` — erro zero.
2. **Divisão determinística:** orquestrador FAKE (LLM stub retornando JSON fixo) →
   `divisao` com 3 slots; grafo entrega 3 rascunhos preenchidos (sem rede).
3. **Avaliador veredito:** avaliador FAKE → `aprovado` termina; `reprovado` re-executa
   especialistas no máx. `max_tentativas_correcao` (contar execuções do nó via estado).
4. **Paralelismo:** `rascunhos` preenchidos pelos 3 nós do subgrafo; ordem não importa
   (unhas os 3 antes do avaliador — LangGraph espera fan-in).
5. **Regressão total:** suíte completa (224 hoje) passa sem alteração de contratos —
   com `AEGIS_MULTIAGENTE=false` o caminho é byte-a-byte o atual.
6. **TUI:** painel de orquestração presente quando multiagente ativo (teste headless).

## 7. Faseamento de entrega (cada fase = commit verde)

1. **F1 · Pool de ferramentas** (pools.py + fio no subagentes.py + testes f/u) — zero impacto no grafo.
2. **F2 · Orquestrador + especialistas** (estado novo, nós, subgrafo programação com 3
   especialistas paralelos + avaliador; config multiagente).
3. **F3 · Roteamento fino + TUI** (gatilhos, painel, métricas, slash `/multi` e `/pool`).
4. **F4 · Polimento** (modelos separados por env, determinismo documentado, benchmark de


tokens/tempo vs. agente único).

---
*Convenções respeitadas: pt-BR no código/comentários/README; TDD (testes antes do
commit verde); `pixi run` tasks; `.pixi/envs/default/bin/python -m pytest --tb=short`
sem `-q` nem pipe na linha de resumo.*