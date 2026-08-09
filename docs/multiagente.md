# Aegis Multi-Agente — Plano de Engenharia de Grafo (v2, expandido)

> **Status:** planejado (próxima execução). **Decisão do usuário:** dividir a tarefa em
> 3 agentes independentes (cada um com seu nó), um nó avaliador específico da tarefa
> e um orquestrador que comanda os nós. **Complemento:** pool de ferramentas.
>
> **Regra de ouro do projeto (anti-alucinação):** toda etapa entrega PROVA verificável
> (testes determinísticos com seeds/fakes, sem rede). Nada de "o LLM faria X" — o
> grafo é testado com respostas controladas.
>
> **Prioridades:** `P0` = núcleo do pedido · `P1` = alto valor, baixo custo · `P2` = depois.

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
- **Falha real que motivou isto:** no provider free, o modelo foi truncado no meio de
  uma tool_call gigante (`finish_reason: length`) e entrou em loop até o limite de
  recursão. O multiagente mitiga isso (comandos pequenos por slot) e o plano prevê
  fallback adaptativo (seções 4.4).

---

## 2. Arquitetura alvo

```
                              ┌────────────────────────────┐
            START ──────────▶ │ no_orquestrador (roteia)  │  ← LLM orquestrador
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

### 2.1 Subgrafo de domínio — "programar" (o exemplo do pedido)

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

**Mapeamento ao pedido do usuário:** 3 agentes independentes/nós = os 3 especialistas do
subgrafo (fan-out sem dependências = execução paralela nativa do LangGraph); avaliador
respeitivo à tarefa = um evaluador por domínio; modelo orquestrador = LLM que decide
domínio/divisão/consolidação.

### 2.2 Onde entram as APIs modernas do LangGraph (P1)

- **Fan-out dinâmico com `Send`** (`langgraph.types.Send`): em vez de 3 nós fixos, o
  orquestrador pode emitir `Send("no_especialista", slot_i, tarefa_i)` para N itens
  (arquivos, capítulos, módulos) — paralelismo por GRANULARIDADE da tarefa, não fixo.
  `AEGIS_MAX_ESPECIALISTAS=3` vira teto físico; o orquestrador decide o número real.
- **`Command`** (LangGraph 1.x): atualizar estado + rotear no mesmo retorno
  (`Command(goto=..., update={...})`) — essencial para o avaliador devolver
  `{update: {"vereditos": [...]}, goto: "no_especialista" | "no_orquestrador"}`.
- **`interrupt()`** (HITL): quando o avaliador reprovar com nota baixa E confiança
  baixa (ambiguidade grave), em vez de re-loop automático, `interrupt()` pausa e o
  usuário decide na TUI ("revisar slot 2" / "aprovar como está"). Resumo do
  checkpointer já suporta retomada — o `thread_id` da TUI vira o meio de continuar.
- **`with_structured_output`** para o veredito: `Veredito` Pydantic
  `{status, nota 0-5, confianca, feedback, criterios_checados}` — sem JSON avulso,
  parse determinístico (mesma técnica já usada no APF `compilar_prompt`).

### 2.3 Roteamento barato antes do LLM (P1)

- **Classificador leve de primeira passagem**: regras por palavras-chave
  (`programar`/`app`/`site`/`bug`… → domínio) + fallback para o LLM orquestrador quando
  o escore for baixo. Cada hierarquia economiza um round-trip do modelo.
- **Cache de divisões**: hash (pergunta normalizada + papéis ativos) → se a mesma
  pergunta já foi orquestrada antes, reusa `divisao` e `pool` gravados em
  `config/dados/orquestracoes.json` (determinismo + economia).

---

## 3. Pool de ferramentas (`aegis/ferramentas/pools.py` — novo)

Declarativo, por NOME da ferramenta (atributo `name` estável nos `BaseTool`). O agente
principal continua recebendo TUDO (zero big-bang); especialistas recebem só sua fatia.

```python
POOLS: dict[str, set[str]] = {
    "geral": {"executar_comando", "ler_arquivo", "escrever_arquivo", "listar_arquivos",
              "pesquisar_memoria", "gerenciar_memoria", "pensar"},
    "programacao": POOLS["geral"] | {"executar_teste", "revisar_codigo", ...},
    "pesquisa": POOLS["geral"] | {"buscar_papers_arxiv", "revisar_literatura",
                                  "gerar_citacao_bibtex", "delegar_pesquisa", ...},
    "escrita": POOLS["geral"] | {"delegar_redacao", "anotar", "ver_notas", ...},
    "obsidian": POOLS["geral"] | {"criar_nota", "ler_nota", "buscar_notas",
                                  "ligar_nota", "notas_por_tag", ...},
}
def pool_da_lista(ferramentas, dominio) -> list
def registrar_pool(nome, nomes: set[str])   # plugins podem registrar pools próprios
```

### 3.1 Pool dinâmica (P1)

- **Por papel CAMEL**: o papel ativo (`definir_papel`) pode declarar `pool` (ex.:
  papel "Cientista" → `pesquisa` + `ciencia`). O orquestrador consulta o papel ANTES
  de montar slots. Extensão natural do sistema de papéis que já existe.
- **Pinning por uso (P2)**: `trajetoria.py` já registra chamadas de ferramentas; um
  contador `nome → frequência por domínio` (Store) reordena a tool list do nó —
  ferramentas quentes no topo (menos tokens, menos ruído no `tool_calls`).
- **Ferramentas efêmeras por tarefa (P2)**: o orquestrador EXPÕE ao especialista uma
  tool sintética de alto nível (ex.: `relatorio_completo(secao)`) que internamente
  consolida `rascunhos[slot]` — a interface para o modelo fica limpa e o estado
  complexo fica escondido.
- **Lazy-load de pools (P2)**: domínios que dependem de libs pesadas (ex.: ciência →
  pandas) só importam a pool quando o subgrafo do domínio é instanciado.

---

## 4. Especialistas

### 4.1 Contrato mínimo de nó (P0)
Cada nó recebe `{divisao[slot_i], rascunhos[outros_slots] (read-only)}` e devolve
`rascunhos[slot_i]` (string markdown/texto) + `metadados_slot` (arquivos criados,
tempo, tokens). Regra: **nunca** misturar estado de outro slot no próprio retorno —
fan-out limpo, integração depois.

### 4.2 Estratégias (P0/P1)
- `paralelo-3` (pedido do usuário): fan-out sem dependências.
- `pipeline` (analista → implementador → revisor): quando os slots têm Dependência
  (ex.: implementação precisa da análise). O orquestrador escolhe pela natureza.
- `map-reduce` via `Send` (P1): N itens independentes (ex.: N arquivos de um projeto).
- **Estratégia híbrida (P1)**: primeiro o analista gera o plano de arquivos; depois
  os N implementadores (um por arquivo, paralelo) — combina os dois mundos e é o
  melhor custo-benefício para "programar".

### 4.3 Integrador de slots (P1)
`no_integrador` entre especialistas e avaliador: verifica ligações entre slots
(imports/contratos de interface), aponta conflitos e produz um único artefato
consolidado para o avaliador julgar. Sem integrador, o avaliador julgaria 3 pedaços
soltos e o veredito seria injusto.

### 4.4 Fallback adaptativo ao provider free (P0 — resposta à falha real)
Se o modelo reportar `finish_reason: "length"` ou truncar tool_call (como no crash),
o orquestrador REBAIXA a estratégia: paralelo-3 → pipeline → sequencial com
ferramentas "uma call pequena por vez". Regra de sistema por nó: "comandos curtos,
blocos pequenos" (já adicionado ao `sistema()` no fix 9d41f97). O estado guarda
`modo_conservador: bool` para o restante do turno.

### 4.5 Tolerância parcial (P1)
Timeout por nó (`AEGIS_TIMEOUT_NO=120`): se um especialista trava, o orquestrador
prossegue com os slots que concluíram, registra `slot_falho` no veredito e o avaliador
julga o que existe (nenhum turno inteiro desperdiçado).

---

## 5. Avaliador

### 5.1 Dupla avaliação (P1 — forte anti-alucinação)
- **Avaliador LLM (crítico por domínio)**: critérios fixos em prompt por domínio
  (programação: compile/coesão/segurança; pesquisa: fontes/atribuição; escrita:
  estrutura/ruído).
- **Avaliador por execução**: para programação, o nó testa já GERA testes; o avaliador
  RODA `pytest`/smoke no sandbox e a nota objetiva entra no veredito
  (`execucao: {passou, falhas, cobertura}`). Veredito = LLM × execução — opinião
  calibrada por fato. Isso é o que separa "parece bom" de "funciona".

### 5.2 Veredito estruturado (P0)
`Veredito(status, nota⩽5, confianca, feedback, criterios_checados, execucao?)` via
`with_structured_output`; histórico em `vereditos[]` (P1: memória de vereditos — os
especialistas recebem TODOS os vereditos anteriores na iteração 2+, não só o último,
para não repetirem os mesmos erros) e persistido em `config/dados/vereditos/` por
tarefa (auditável, retomável).

### 5.3 Confiança baixa → humano (P1)
`nota <= 1 e confianca < 0.4` → `interrupt()` para o usuário decidir (TUI mostra o
slot, o feedback e as 3 opções). Evita loop cego e dá governança.

---

## 6. Domínios declarativos e skills (P1 — extensibilidade no padrão do repo)

```python
@registrar_dominio(
    "programacao",
    estrategia="híbrida",
    slots=["analista", "implementadores", "testador"],
    pool="programacao",
    avaliador="programacao",
    gatilhos=["programar", "app", "site", "bug", "refatorar", "projeto"],
)
```
- A mesma interface de `extensions/plugins/` (função `registrar()`): o usuário cria um
  domínio novo (sysadmin, finanças, devops…) sem tocar no núcleo — só declarar.
- **Skills como especialistas (P2)**: um domínio pode ser definido por uma skill
  (`extensions/skills/<nome>/SKILL.md` → prompt do especialista + `ferramentas` da
  skill + pool derivada) — amarra o sistema de habilidades auto-evolutivas existente
  ao multiagente: "skill vira nó". Recarga em runtime já é suportada.

---

## 7. Estado novo (`aegis/estado.py`)

```python
dominio: str                  # "programacao" | "pesquisa" | ... | ""
divisao: list[dict]           # [{slot, tarefa, estrategia, status}]
rascunhos: dict[str, str]     # rascunhos[slot_i] -> conteúdo
vereditos: list[dict]         # veredito estruturado por iteração
modo_conservador: bool        # provider free / truncamento detectado
orquestracao_final: str       # resposta consolidada
```

## 8. TUI (painel de orquestração — P1)

- **Árvore viva de nós**: cada slot com estado (pendente/rodando/ok/falhou), tempo,
  tokens por nó (`usage_metadata` já vem no streaming) — painel lateral.
- **`/orquestrar` (dry-run)**: mostra a divisão proposta (slot/tarefa/estratégia)
  ANTES de executar; o usuário ajusta ("junta slots 1 e 2", "troca para pipeline").
- **`/multi on|off`**: liga/desliga o orquestrador em runtime (mesmo padrão `/modo`).
- **Redirecionamento de slot (P2)**: durante a execução, mensagem dirigida a um slot
  ("slot 2: use Python 3.12") vira feedback injetado na próxima iteração.

## 9. Config (env)

```
AEGIS_MULTIAGENTE=true
AEGIS_MODELO_ORQUESTRADOR=       # vazio = mesmo modelo do principal
AEGIS_MODELO_AVALIADOR=
AEGIS_MAX_ESPECIALISTAS=3        # teto de fan-out (Send respeita)
AEGIS_GATILHOS_MULTIAGENTE=programar,projeto,sistema,site,documento,pesquisa
AEGIS_TIMEOUT_NO=120             # timeout por especialista
AEGIS_HITL=false                 # interrupt() quando confianca baixa
```

## 10. Testes (prova anti-alucinação — obrigatório)

1. **Pool íntegra:** `∪ POOLS ⊆ nomes(ferramentas)` — nenhum nome órfão.
2. **Divisão determinística:** orquestrador FAKE (LLM stub → JSON fixo) → `divisao`
   com 3 slots; 3 `rascunhos` preenchidos, sem rede.
3. **Avaliador:** FAKE aprovado → termina; FAKE reprovado → re-execução limitada a
   `max_tentativas_correcao` (contar execuções no estado).
4. **Paralelismo real:** 3 nós do subgrafo preenchem os 3 slots antes do fan-in
   (isso também é uma prova de ORDER-FREE — rodar 3 seeds e comparar os rascunhos).
5. **Fallback conservador:** stub de LLM com `finish_reason="length"` → estratégia
   rebaixada para pipeline; turno termina sem exceção (regressa o bug do 9d41f97).
6. **HITL:** interrupt dispara quando `nota<=1 e confianca<0.4`; retoma via thread_id.
7. **Regressão total:** suíte 224 verde com `AEGIS_MULTIAGENTE=false` (caminho
   byte-idêntico ao atual) — contrato TUI (`ultima_resposta`, `ultimos_tokens`,
   produtor injetável) intocado.
8. **TUI:** painel de orquestração presente quando ativo (teste headless).

## 11. Fases (cada fase = commit verde com testes)

> **Estado da implementação:** F1 ✅ e F2 ✅ concluídas (suíte **237 passed**;
> ver git log desta leva). F3–F6 pendentes conforme prioridades P1/P2.

1. **F1 · Pool de ferramentas** (P0) — ✅ **implementada**: `aegis/ferramentas/pools.py`
   com 6 pools declarativas (programacao/pesquisa/escrita/obsidian/memoria/prompt),
   `registrar_pool()` para plugins, `pool_da_lista()` por domínio e teste de
   integridade `∪ POOLS ⊆ nomes(ferramentas)` (0 órfãos contra a lista real de 47).
   Ordem alfabética de propósito (estabilidade do prompt → cache do provedor).
2. **F2 · Núcleo multiagente** (P0) — ✅ **implementada**: `aegis/multiagente.py`
   (registro declarativo de domínios + classificador por regras zero-LLM +
   `parsear_veredito` tolerante + `montar_subgrafo_dominio` + `montar_multiagente`),
   estado novo em `estado.py` (dominio/divisao/rascunhos/vereditos/orquestracao_final,
   reducers `_merge_dict` e `operator.add`), wire em `grafo.py` (START → orquestrador →
   `sub_<domínio>` | legado), auditoria em `config/dados/orquestracoes.jsonl`, 4
   domínios além de programação (pesquisa/escrita/obsidian/memoria), prompts dos
   especialistas/integrador/avaliador, TUI com frame `resposta_multi` (+ estado
   `dominio_turno`/`vereditos_turno`). **Deliberadamente diferente do plano em 2
   pontos** (lições reais, ver §13): fan-out por 3 arestas paralelas em vez de
   `Send` no retorno de nó (a versão instalada do LangGraph 1.x rejeita lista de
   `Send` com `InvalidUpdateError`); loop de reprovação via aresta condicional para
   `no_fanout` em vez de `Command` (o avaliador append em `vereditos` e a rota pura
   `rota_apos_avaliador` já cobrem o ciclo).
3. **F3 · Roteamento e HITL** (P1) — pendente: classificador leve + cache de divisões
   (a auditoria JSONL já grava a base), `Send` para map-reduce (validar a API da
   versão instalada antes), `interrupt()` + retomada na TUI.
4. **F4 · TUI de orquestração** (P1) — parcial: frame `resposta_multi` + estado
   exposto; falta a árvore viva de slots, `/orquestrar` dry-run, `/multi on|off`.
5. **F5 · Domínios declarativos** (P1) — parcial: `DOMINIOS` é registro declarativo
   com 5 domínios; falta `@registrar_dominio` para plugins e skills como especialistas.
6. **F6 · Polimento e benchmark** (P2) — pendente: modelos separados (`AEGIS_MODELO_ORQUESTRADOR`/
   `AEGIS_MODELO_AVALIADOR` já lidos na config), `pixi run bench-multi`, memória de
   vereditos, pinning de ferramentas por uso.

## 12. Riscos e limites

- **Custo de tokens**: multiagente multiplica prompts. Mitigação: classificador leve,
  cache, pools pequenas, avaliação por execução substitui iterações de LLM.
- **Latência**: 3 nós em paralelo ≈ 1 nó em latência (fan-out); no provider free a
  latência ainda escala por nó — daí `AEGIS_TIMEOUT_NO` e tolerância parcial.
- **Deriva de slots**: especialistas livres divergem em convenções → `no_integrador`
  + critérios fixos no avaliador.
- **Determinismo**: mesma seed → mesma divisão/rascunhos EXIGE fakes nos testes; em
  produção o LLM é estocástico por natureza: documentado como "determinismo de teste,
  não de produção" (o mesmo que o núcleo do progect_singularity prega).
- **Cache global de subgrafos**: `obter_subgrafo` cacheia por (domínio, id(llm),
  id(cfg)) — o subgrafo captura modelo e config nos closures; reusá-lo com outro
  modelo/config vazaria estado (bug real encontrado e corrigido nos testes).
- **Duas arestas do START**: no modo multiagente, START → no_orquestrador SUBSTITUI
  START → no_agente (a rota interna encaminha ao legado). Manter as duas arestas
  roda o agente principal EM PARALELO com o subgrafo (bug real de execução dupla
  visto no diagnóstico).

## 13. Web search — o que a pesquisa mudou na implementação

Pesquisa feita via SearXNG local (**nota: a porta mudou de 8888 para 8081** após
restart do docker compose) + extração das páginas. Lições aplicadas (ou registradas):

1. **Fan-out paralelo exige reducers** (forum LangChain, "parallel nodes/fanouts" +
   resposta de devs do LangGraph): nós agendados juntos rodam em paralelo (Pregel) e
   chaves escritas por múltiplos nós precisam de reducer — sem ele,
   `INVALID_CONCURRENT_GRAPH_UPDATE`. Aplicado: `rascunhos` com `_merge_dict` (cada
   slot grava a própria chave) e `vereditos` com `operator.add` (append-only).
   Link: https://forum.langchain.com/t/best-practices-for-parallel-nodes-fanouts/1900
2. **`Send` para fan-out dinâmico; `Command(update, goto)` para rotear+atualizar;
   `max_concurrency` para throttling; `RetryPolicy` por nó** — planejado (F3), com a
   ressalva real: nesta versão instalada do LangGraph 1.x, RETORNO de nó com lista de
   `Send` levanta `InvalidUpdateError: Expected dict, got [Send...]` — validar a API
   antes de migrar o fan-out estático.
3. **Manter estado bruto, formatar prompts na hora** (docs oficiais "Thinking in
   LangGraph"): os rascunhos são armazenados crus no estado; o integrador monta o
   trecho formatado só quando precisa. Link:
   https://docs.langchain.com/oss/python/langgraph/thinking-in-langgraph
4. **Self-correcting loop com avaliador estruturado** (axontick blog): grade/audit/
   corrigir antes de responder, com veredito em JSON (Pydantic no original; aqui
   parse tolerante `parsear_veredito`). Link:
   https://www.axontick.com/blogs/stateful-retries-evaluation-nodes-langgraph
5. **Cache de LLM corta custo/latência drasticamente** (AWS database blog): com
   prompt caching, queries cacheadas ficam ~sub-ms. Aplicado de forma indireta:
   pools com ordem alfabética estável (prompt de sistema estável = cache hit), estado
   com pouca mutação por turno multipa (só mensagens + chaves novas), subgrafos
   compilados UMA vez por processo (`obter_subgrafo`). O prompt caching do provedor
   já aparecia no diagnóstico (`cache_read: 2.18M`). Link:
   https://aws.amazon.com/blogs/database/optimize-llm-response-costs-and-latency-with-effective-caching/
6. **Supervisor/orchestrator pattern** (eastondev + docs LangGraph): supervisor
   decide, workers executam, um nó de síntese consolida — o modelo desta F2
   (orquestrador por regras + especialistas + integrador/avaliador). Link:
   https://eastondev.com/blog/en/posts/ai/20260512-langgraph-multi-agent-supervisor/

**Velocidade de resposta (pedido do usuário) — o que foi implementado no F2:**
- Especialista recebe a pool REDUZIDA (8–14 tools vs 47 do agente principal) →
  prompt de sistema ~3–6× menor → menos tokens de entrada e menos ruído de
  tool_calls (menos risco do loop/truncamento que derrubou a TUI).
- Classificador de domínio por REGRAS (zero LLM): o orquestrador não custa uma
  chamada extra em nenhum turno; só entra no subgrafo quando há domínio.
- Subgrafos compilados uma vez por processo (cache `obter_subgrafo`).
- Fluxo legado byte-idêntico: pergunta sem domínio → caminho antigo sem overhead.

---
*Convenções respeitadas: pt-BR em código/comentários/README; TDD (testes antes do
commit verde); `pixi run` tasks; `.pixi/envs/default/bin/python -m pytest --tb=short`
sem `-q` nem pipe na linha de resumo; git push exige `miguel9w@users.noreply.github.com`.*