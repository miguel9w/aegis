# Aegis — Documentação Técnica Completa

> **Documento-mapa do projeto.** Aqui você encontra: o que o Aegis é, a
> arquitetura em camadas, o histórico completo do que já foi feito (fases
> C1–C7 e G1–G5, com commits), o roadmap do que será feito a partir de agora
> (fases X1–X11) e a referência detalhada de **todos** os arquivos do
> repositório.
>
> Complementos: [planejamento-nucleo.md](planejamento-nucleo.md) (specs das 23
> fases do núcleo) · [multiagente.md](multiagente.md) (arquitetura multiagente)
> · [webui.md](webui.md) (design da Web UI) · [../README.md](../README.md)
> (visão de produto e comandos).

---

## 1. O que é o projeto

**Aegis — Agente Pessoal Autônomo.** Um agente de última geração construído
sobre **LangGraph** (Python), com:

- **grafo de estado cíclico** com checkpoints em SQLite (retomada de conversas,
  multi-tópicos via `thread_id`);
- **memória de longo prazo** (Store) + RAG-lite + recall hierárquico;
- **habilidades auto-evolutivas** (skills em `extensions/skills/`, plugins em
  `extensions/plugins/` — o agente cria e recarrega em runtime);
- **multiagente** (orquestrador + especialistas paralelos + avaliador) e
  **subagentes** (agent-as-tool);
- **ciclo de entrega GSD** (discuss → plan → execute → verify → ship) com UAT
  conversacional, revisão por pares, aprendizados versionados e pausa/retomada;
- **superfícies**: TUI Textual em streaming, CLI headless, Web UI (Bun + SSE) e
  gateway Webhook HTTP — todas consumindo o MESMO grafo.

**Fatos técnicos:**

| Item | Valor |
|---|---|
| Linguagem | Python 3.11 (gerenciado por **Pixi** — `pixi.toml`, 100% reproduzível) |
| Framework de agente | LangGraph 1.x + LangChain (`ChatOpenAI` compatível com qualquer endpoint) |
| Web UI | **Bun** 1.3.11 (TypeScript vanilla, bundlado) + ponte Python JSONL, porta **8788** |
| Testes | **418 pytest** + **25 bun tests** (77 `expect()`) — suíte completa verde (M1/X1) |
| Persistência | `config/dados/memoria_agente.db` (checkpoints + Store, gitignored) |
| Provedor | OpenAI-compatível via env (DeepSeek/OpenRouter/NIM/zen) — chaves SÓ em `config/env/.env` |

---

## 2. Arquitetura em camadas

```
┌─────────────────────────────────────────────────────────────────┐
│ SUPERFÍCIES (tudo chama o MESMO grafo)                          │
│  TUI Textual (tui.py) · CLI (main.py) · Web UI (webui/) ·       │
│  Gateway Webhook (gateways/webhook.py) · Agendador (agendador.py)│
└───────────────────────────────┬─────────────────────────────────┘
                                │ astream_events / processar_mensagem
┌───────────────────────────────▼─────────────────────────────────┐
│ NÚCLEO — grafo LangGraph (grafo.py + nos.py + multiagente.py)   │
│  no_agente → no_ferramentas → no_reflexao_auto_correcao →       │
│  no_compressao_contexto → no_memoria → no_verificar →            │
│  ciclo G1 (discuss→plan→execute→verify→ship) → no_uat_apos_ship  │
│  + no_reflexao_pos_turno (lições)                                │
└───────────────┬──────────────────────────┬──────────────────────┘
                │                          │
   ┌────────────▼───────────┐   ┌──────────▼──────────────────┐
   │ FERRAMENTAS (tools)    │   │ PERSISTÊNCIA                │
   │  basics/sistema/       │   │  checkpointer SQLite (passo)│
   │  trabalho/pools/       │   │  Store (longo prazo)        │
   │  relogio + skills/     │   │  trajetorias JSONL          │
   │  plugins + subagentes  │   │  obsidian/ · datasets       │
   └────────────┬───────────┘   └─────────────────────────────┘
                │
   ┌────────────▼───────────────────────────────────────────────┐
   │ CROSS-CUTTING (gates de qualidade)                          │
   │  seguranca.py (anti-injeção) · uso.py (orçamento) ·         │
   │  sandbox.py (docker/ssh) · aprendizados.py (grafo de        │
   │  conhecimento) · sessoes.py (Recall) · trajetoria.py        │
   └──────────────────────────────────────────────────────────────┘
```

**Estado global** (`aegis/estado.py`): `TypedDict` LangGraph com `mensagens`
(reducer `add_messages`), `fluxo_trabalho`, `registros_ferramentas`,
`uso_tokens` (reducer soma), `licoes`, `fontes`, `commits_entrega`,
`revisao_entrega`, `avaliacao`, `metadados_sessao` etc.

---

## 3. O que já foi feito (histórico)

### 3.1 Fundação (pré-núcleo — evolução incremental)

| Bloco | O que entrou |
|---|---|
| TUI | migração Rich → **Textual** (App com streaming por `astream_events`, produtor injetável para testes headless) e polimento estilo Hermes (painel lateral de contexto, statusbar com métricas, modo RAW, troca de modelo em runtime) |
| Config | configuração por **JSON** (`config/dados/*.json` com fallback seguro) · `.env` com isolamento total de chaves |
| CAMEL | papéis (role-playing), memória pontuada (importância × recência × overlap), toolkits thinking/task-planning/note-taking |
| Produtividade | científicas (arXiv, BibTeX, APA), banco estilo **Obsidian** (vault md + wikilinks), 27 **slash commands**, **APF** (Formato de Prompt Avançado) |
| Ferramentas | arquivo (sandbox + diff unificat), `executar_comando` com política de segurança, `relogio` multi-fuso |
| Multiagente | orquestrador por regras (zero LLM) → 3 especialistas **paralelos** (Send) → integrador → avaliador LLM com veredito; pools de ferramentas por domínio |
| Web UI | do design → **W7**: servidor Bun com fila FIFO de jobs + SSE (`:open`/`:ping`), ponte Python JSONL, front 3 colunas dark com chat streaming, thinking, feed de atividade, diffs, mermaid/KaTeX, interromper job, janela de perguntas, comandos melhorados (`/`, `@`, `-/`), vendor estático (bundle 14 KB) |
| Ops | `backup.sh` (só arquivos rastreados pelo git + retenção) com teste automatizado · CI GitHub Actions (pixi + pytest) |

### 3.2 Núcleo — fases C1–C7 (raciocínio e blindagem)

| Fase | Commit | Entrega |
|---|---|---|
| **C1 — Reflexão pós-turno** | `d551339` | lições extraídas ao fim de turnos com ferramentas, gravadas na Store, recall IDF no system (erro repetido eleva prioridade) |
| **C2 — Plan-and-execute** | `4eed79e` | plano ordenado para tarefas complexas (heurística zero-LLM), replanejamento após falha, bloco de progresso no system |
| **C3 — Verify-then-answer** | `468d2fc` | resposta conferida contra evidências da execução (veredito + correção única) — ataca a alucinação de execução |
| **C4 — Memória estrutural** | `e60573f` | resumo incremental + decisões por thread + recall hierárquico (perfil→lições→resumo→decisões) + tool `recuperar_contexto`; **fix raiz do flaky: conexões sqlite separadas saver/store** |
| **C5 — Anti-injeção** | `b49f3f2` | conteúdo externo (arquivos/web/comandos) é **DADO, nunca instrução**: marcadores de classe, bloqueio de ações destrutivas, property tests (hypothesis) |
| **C6 — Orçamento** | `cb16fc8` | medição de tokens por passo (`uso_tokens` com reducer soma), corte na rota quando estoura turno/sessão, tool `estatisticas` (sem rede), card `orcamento` na UI |
| **C7 — Sandbox distribuído** | `d547b68` | `comando_sandbox` com backends **docker** (container efêmero `--rm`, rede isolada, denylist, volume `/artefatos`) e **ssh** (allowlist própria); auditoria unificada em `comandos.jsonl` com campo `backend`; badge na UI |

### 3.3 Disciplina de entrega — fases G1–G5 (paridade GSD)

| Fase | Commit | Entrega |
|---|---|---|
| **G1 — Modo entrega** | `068442e` | ciclo **discuss → plan → execute → verify → ship** com classificador zero-LLM; discussão por interrupt (pedido vago pergunta); waves auditadas com commits; verify goal-backward com correção limitada (anti-loop); ship com selo |
| **G2 — UAT conversacional** | `482c25d` | `no_uat_apos_ship` julga critérios um a um (interrupt, zero LLM); reprovados viram **gaps** persistidos na Store por projeto (sobrevivem a `/clear`) e voltam como contexto |
| **G3 — Revisão por pares** | `406c0a0` | nada vai a ship sem revisão: checklist fixo em `limites.json` (`checklist_revisao`) + revisor LLM com veredito por item, apontamentos viram feedback, anti-loop com limite de correções |
| **G4 — Aprendizados versionados** | `5bd1862` | reflexão classifica cada lição em **4 categorias** (decisão/lição/padrão/surpresa) → `docs/learnings/<sessao>.md` (versionado) + **grafo de conhecimento** consultável pela tool `consultar_grafo` (sem LLM) |
| **G5 — Pausa/retomada + reversão** | `fed0909` | `pausar_trabalho` (handoff na Store com fase/plano/critérios + próximos passos por regra), `retomar_trabalho` (contexto completo — o ciclo continua sem re-executar passos), `reverter_entrega` (`git revert` validado, commit específico ou HEAD), `replay_turno` (forensics sem LLM, detecta não-determinismo) |

**Estado atual da suíte:** 379 pytest + 25 bun verdes, git limpo, Web UI
rodando em `:8788` com `healthz ok` (ponte ok). Último commit: **`fed0909`** (G5).

### 3.4 Bloqueio conhecido (externo, documentado)

A **prova real do G3** (turno com LLM real até o ship) ficou bloqueada por uma
**regressão do provider zen**: desde 2026-08 o endpoint `opencode.ai/zen/v1`
rejeita com **HTTP 400** qualquer request que carregue `tool_calls` no
histórico (`invalid_request_error` — exige `reasoning_content`). A fase foi
marcada concluída com o status honesto (implementação + testes verdes; prova
de runtime pendente). **Não é bug do Aegis** — o mesmo request passa por
outros providers OpenAI-compatíveis. Retomável quando o provider estabilizar.

---

## 4. O que será feito a partir de agora (roadmap X1–X11)

Ordem recomendada (do próprio planejamento):
**X1 → X2 → X3 → X4 → X5 → X6 → X7 → X8 → X9 → X10 → X11**
(X4 e X6 podem entrar mais cedo; X10 roda continuamente.)

| Fase | Objetivo | Mudanças-chave |
|---|---|---|
| **X1 — Catálogo de subagentes sob demanda** ✅ (2026-08) | além de `delegar_pesquisa`/`delegar_redacao`, delegados especializados com pool reduzido e auto-correção própria | `delegar_codigo`, `delegar_dados`, `delegar_revisao` (revisor dedicado do G3); catálogo `config/dados/delegados.json` com `arq_limite` (bloqueia cascata infinita); reuso de `fabrica_nos` com persona |
| **X2 — Skills/playbooks (memória procedimental versionada)** | generalizar `extensions/skills/` com frontmatter (nome, descrição, gatilho) carregadas sob demanda | registro dinâmico sem reiniciar; tool `carregar_skill` com teto de tokens; skills ranqueadas no RAG-lite pela descrição |
| **X3 — Fact-checking com fontes** (paridade web-deep-research) | respostas com pesquisa citam fontes; cruza ≥2 fontes antes de afirmar; divergência vira sinalização | busca devolve `{url, titulo, trecho}`; nó `no_fact_check`; estado `fontes: list[{afirmacao, urls, status}]` |
| **X4 — Early exit inteligente** | o agente para de rodar ferramentas quando já tem o suficiente | sinal no system; heurística `no_early_exit` (pergunta trivial → 1 chamada LLM); métrica `steps_por_turno` |
| **X5 — Colaboração humana no núcleo** | tool `perguntar_humano` em TODAS as superfícies (TUI, gateway, web) | evento `pergunta` na ponte; timeout configurável (`pergunta_timeout_s`) com default — nunca trava; allowlist de perguntas |
| **X6 — Observabilidade do pensamento** | reasoning/plano saem como ESTRUTURA nos eventos; cada rota declara o motivo | evento `raciocinio` `{partes: [{tipo: thinking\|plano\|decisao}]}`; `rota_motivo` em `metadados_sessao`; evento `fase` no ciclo G1 |
| **X7 — Self-critique da resposta final** | rascunho final avaliado (correção, completude, evidência) com custo controlado | revisão só em tarefas ≥ `self_critique_min_steps`; máx. 1 re-geração (anti-loop); estado `avaliacao` |
| **X8 — Modo conservador por provider** | rebaixar estratégia automaticamente por tipo de provider (free vs pago) | `providers` em `limites.json`; `Config` infere pela `OPENAI_API_BASE`; flag manual ainda sobrescreve |
| **X9 — Preços por modelo** | C6 passa a estimar custo REAL em moeda | `precos: {entrada_por_M, saida_por_M, raciocinio_por_M}` por provider/modelo; `custos` no estado e na `estatisticas`; fallback 0 |
| **X10 — Property tests do núcleo** | invariantes globais do grafo provados por geração aleatória (hypothesis) | suíte `tests/property/` com geradores de mensagens/tool results (erros, conteúdo malicioso, tool_calls órfãs); invariantes: terminação, sandbox de escrita, auditoria, ordenação, fluxo legado byte-idêntico; ≥1000 casos gerados |
| **X11 — Sanitização da saída na UI** | além dos segredos, a UI saneia a SAÍDA do modelo (links disfarçados, falso markdown, spoofing) | sanitizador na ponte (header `X-Saneado`), renderizador neutraliza `javascript:`/`data:`, auditoria `saneamento` |

Specs completas (mudanças, testes, critérios de aceite) em
[planejamento-nucleo.md](planejamento-nucleo.md) — cada fase termina com
código + testes verdes (pytest + bun) + commit/README + prova de runtime.

---

## 5. Estrutura do repositório

```
aegis/                       # pacote Python (núcleo)
├── __init__.py              # docstring do pacote
├── main.py → na raiz        # CLI (na verdade main.py está na raiz)
aegis/
├── agendador.py             # cron interno
├── aprendizados.py          # G4: categorias + grafo de conhecimento
├── autorizacoes.py          # aprovação de comandos (janela web)
├── camel_kit.py             # toolkits CAMEL
├── cientificas.py           # arXiv/BibTeX/APA
├── config.py                # singleton de configuração (.env)
├── config_json.py           # config por JSON com fallback
├── contexto.py              # AGENTS.md/CLAUDE.md
├── estado.py                # TypedDict do grafo
├── exportador.py            # trajetórias → datasets
├── ferramentas/             # tools (ver abaixo)
├── gateways/                # webhook HTTP
├── grafo.py                 # montagem do grafo
├── llm.py                   # provedor resiliente
├── memoria.py               # SqliteSaver + SqliteStore
├── memoria_camel.py         # memória pontuada
├── memoria_tool.py          # memória explícita (Hermes)
├── multiagente.py           # orquestrador + especialistas
├── neografo.py              # M1: memória GraphRAG (Neo4j, dois grafos)
├── nos.py                   # nós do grafo (57 KB — maior módulo)
├── obsidian.py              # vault md + wikilinks
├── papeis.py                # roles CAMEL
├── plugins.py               # plugins dinâmicos
├── prompts.py               # prompt de sistema pt-BR
├── prompts_avancados.py     # formato APF
├── recuperacao.py           # RAG-lite
├── sandbox.py               # executors local/docker/ssh
├── seguranca.py             # C5 anti-injeção
├── sessoes.py               # Session Recall
├── skills.py                # skills auto-evolutivas
├── slash.py                 # comandos /
├── subagentes.py            # X1: catálogo de delegados (agent-as-tool)
├── tarefas.py               # Todo (Hermes)
├── trajetoria.py            # auditoria JSONL
├── tui.py                   # TUI Textual
├── uso.py                   # C6 medição/orçamento
└── webui_bridge.py          # ponte Python ↔ Bun

webui/                       # Web UI (Bun + TS)
tests/                       # 39 arquivos: conftest + suítes por módulo
docs/                        # planejamento-nucleo, multiagente, webui, tecnical
config/
├── env/.env.example         # modelo de variáveis (NUNCA versionar .env)
├── dados/                   # delegados.json + limites.json + runtime gitignored + datasets/
└── prompts_avancados/       # fichas .apf
extensions/
├── plugins/exemplo_plugin.py
└── skills/pesquisa-tecnica/SKILL.md
```

---

## 6. Referência detalhada de todos os arquivos

### 6.1 Raiz do projeto

| Arquivo | Descrição |
|---|---|
| `README.md` | Visão de produto: recursos, 23 fases do núcleo, variáveis de ambiente (`AEGIS_*`), modo de uso, FAQ. **34 KB** — o maior doc. |
| `main.py` | **CLI / ponto de entrada.** Modos: `pixi run start` (TUI interativa com streaming), `start "pergunta"` / `--headless` (execução única), `--thread`, `--novo-thread`, `--listar-ferramentas`, `--comando` (slash), `--dev` (eventos verbosos). Monta o grafo com `montar_grafo` + `recarregar_tudo` (registro real de ferramentas). |
| `pixi.toml` | **Manifesto Pixi** (conda-forge, linux-64, v0.11.0). Tasks: `start`, `dev`, `webui`, `webui-test`, `agendador`, `prompts`, etc. Dependências: langgraph, langchain-core, pydantic, rich/textual, hypothesis, dotenv. |
| `pixi.lock` | Lockfile do ambiente (reproduzibilidade total). **68 KB.** |
| `pytest.ini` | Config do pytest: `pythonpath = .`, `testpaths = tests`, `-q --tb=short`, warnings de langchain/langgraph ignorados. |
| `backup.sh` | Rotina de backup: copia apenas arquivos **rastreados pelo git** (`git ls-files`), retenção `BACKUP_RETENCAO` (default 5), nunca leva segredos/runtime. |
| `test_backup.sh` | Teste shell da rotina: roda `backup.sh` num destino temporário, valida manifesto, arquivos essenciais e não-vazamento (espelhado em `tests/test_backup.py`). |
| `.gitignore` | Ignora `.pixi/`, `__pycache__/`, `.env`, `*.db`, `trajetorias/`, `agendamentos.jsonl`, `comandos.jsonl`, `grafo_conhecimento.json`, `artefatos/`, `log.log`, `.hermes/`, `.hypothesis/`. |
| `log.log` | Log de runtime (gitignored). |
| `.github/workflows/ci.yml` | CI: pixi + pytest (Python 3.11) em ubuntu-latest, push/PR para master/main. |

### 6.2 Pacote `aegis/` — núcleo

| Arquivo | Tamanho | Descrição |
|---|---|---|
| `__init__.py` | 425 B | Docstring do pacote: "Agente Pessoal Autônomo". Não importa nada pesado (imports preguiçosos). |
| `config.py` | 9,9 KB | **Singleton tipado** de configuração. Carrega `config/env/.env`, expõe `OPENAI_API_BASE/KEY`, `MODEL_NAME`, `temperatura`, `max_tokens`, `thread_id`, `banco`, `artefatos_dir`, `comandos_path`, `learnings_dir`, `grafo_path`, `exec_timeout`, `orcamento_por_turno/sessao`, `checklist_revisao`, limites… Nenhuma chave é commitada. |
| `estado.py` | 5,3 KB | **Estado global do grafo** (`EstadoAegis`, TypedDict). Campos: `mensagens` (reducer `add_messages`), `fluxo_trabalho`, `registros_ferramentas` (sem reducer — o lote mais recente), `uso_tokens`, `licoes`, `fontes`, `commits_entrega`, `revisao_entrega`, `avaliacao`, `metadados_sessao`, `rastro_rotas`. |
| `grafo.py` | 11,8 KB | **Montagem do grafo cíclico.** `montar_grafo(llm, ferramentas, checkpointer, store, cfg)` constrói: START → no_agente (com tool_calls? → no_ferramentas; senão → no_memoria → END), no_ferramentas (erro detectado? → no_reflexao_auto_correcao; senão → no_agente), no_compressao_contexto, ciclo G1 (discuss→plan→execute→verify→revisar→ship→uat), no_reflexao_pos_turno, rota de corte de orçamento (rotas condicionais), Register + checkpointer/store. |
| `nos.py` | **57 KB** | **Nós do grafo** — o coração. `no_agente` (injeta system com perfil/lições/plano/tarefa, invoca `llm.bind_tools(ferramentas)`, extrai uso), `no_ferramentas` (ToolNode com logging + auditoria), `no_reflexao_auto_correcao`, `no_compressao_contexto`, `no_memoria` (fatos duráveis), `no_verificar` (C3), `no_planejamento`/`no_replanejamento` (C2), `no_discuss`/`no_plan`/`no_execute`/`no_verify_entrega`/`no_revisar`/`no_ship`/`no_uat_apos_ship` (G1/G2/G3), `no_reflexao_pos_turno` (C1/G4 — grava lições categorizadas em `docs/learnings/` + grafo), medição C6 nos 5 nós, `_parsear_*` (plano, revisão, licões, verificações) com fail-safe. |
| `llm.py` | 3,9 KB | Provedor agnóstico (ChatOpenAI). Retry resiliente com backoff exponencial + jitter e respeito a `Retry-After`. |
| `memoria.py` | 5,7 KB | `criar_checkpointer_sync` (SqliteSaver — checkpoints por passo) e `criar_store_sync` (SqliteStore — longo prazo), **conexões separadas** (fix de transação), namespaces `namespace_licoes()` e `namespace_handoffs()` (G5). |
| `recuperacao.py` | 9,6 KB | RAG-lite: ranking por overlap de tokens com peso IDF sobre a Store + `extensions/skills/`; determinístico, sem LLM. |
| `multiagente.py` | 15,6 KB | Orquestrador por regras (zero LLM) → subgrafo do domínio com fan-out `Send` para 3 especialistas paralelos (cada um com SUA pool) → integrador → avaliador LLM com veredito estruturado; reducer de rascunhos (merge de escritas paralelas); fallback para fluxo legado quando `multiagente_ativos=false` (byte-idêntico). |
| `subagentes.py` | 13,9 KB | **X1. Agent-as-tool sob demanda**: catálogo `config/dados/delegados.json` (5 delegados — pesquisador, redator, codigo, dados, revisao; fallback embutido se ausente/corrompido); fábrica `delegar_<nome>` (assinatura por `parametro`, nome por campo `tool`); pools por nome do registro central; anti-cascata `arq_limite` (tool de delegação fora do pool quando a profundidade estoura; `_executar` bloqueia acima do limite do alvo); `tools_delegacao()` expõe as 5 no registro central. |
| `sandbox.py` | 10,7 KB | **C7.** `ExecutorLocal` (subprocess com timeout), `ExecutorDocker` (container efêmero `--rm`, rede isolada, denylist — docker-in-docker, podman/nerdctl-in-docker, `--privileged`, bomba fork `:\s*\(\s*\)\s*\{` — volume de artefatos em `/artefatos`, **nunca recebe env do host**), `ExecutorSSH` (allowlist de comandos, `BatchMode=yes`, `ConnectTimeout=10`) + fábrica por `AEGIS_SANDBOX_BACKEND`. |
| `seguranca.py` | 4,9 KB | **C5.** Helpers puros de anti-injeção: `classificar_conteudo` (detecta instrução embutida), marcadores de classe, `BLOCO_SEGURANCA` (bloco no prompt), `_catalogo_ferramentas` (nomes permitidos). Conteúdo externo é DADO. |
| `uso.py` | 3,9 KB | **C6.** `extrair_uso` (entrada/saída/reasoning de respostas OpenAI-compat), `estimar_custo` (tabela `precos_por_token` em `limites.json`), `verificar_orcamento` (turno/sessão em tokens ou R$). Sem imports de runtime. |
| `aprendizados.py` | 5,8 KB | **G4.** `classificar` (decisão/lição/padrão/surpresa), `GrafoConhecimento` (grafo.json com entidades/relações, navegação por ferramenta/fase/erro/categoria), `bloco_markdown`, `nome_arquivo_sessao`. |
| `neografo.py` | 21,7 KB | **M1.** Memória GraphRAG: `classificar_registro`/`classificar_e_tipo` (trivial×importante, regras determinísticas — zero LLM), `GrafoNeo4j` (dois grafos num database Community via propriedade `grafo` + label `:Memoria`; Cypher 100% `$parametrizado`; `_executar_mutacao` confirma escrita via `.consume()`; consulta = nós diretos + vizinhos 1 salto; `limpar_privado`/`purga_vencidos`), `gravar_turno_graphrag` (fachada chamada pela reflexão), `consultar_graphrag` (None = inativo → fallback RAG-lite). |
| `trajetoria.py` | 3,8 KB | Auditoria JSONL: início/fim de ferramentas, transições de nós, chamadas ao modelo — append + flush à prova de interrupção. |
| `sessoes.py` | 10,9 KB | **Session Recall** (paridade Hermes `session_search_tool`): busca em sessões passadas indexadas das trajetórias (FTS5-like local, sem LLM), 3 modos. |
| `memoria_tool.py` | 5,5 KB | Memória explícita (paridade Hermes `memory_tool`): add/replace/remove sobre a Store — memória de perfil injetada no system. |
| `memoria_camel.py` | 8 KB | Memória pontuada CAMEL: importância 0–10 + recência (decaimento exponencial) × overlap; recuperação top-k ranqueada. |
| `papeis.py` | 12,3 KB | Roles CAMEL: catálogo (`papeis.json` + padrões), papel ativo + tarefa especificada injetadas no system; tools de papel. |
| `camel_kit.py` | 7,5 KB | Toolkits CAMEL: `pensar`/`ver_pensamento`, `planejar_tarefa`/`atualizar_plano`/`ver_plano`, `anotar`/`ver_notas` — persistidos em JSON. |
| `cientificas.py` | 9 KB | arXiv (busca + id), parse Atom puro, BibTeX, citação APA, biblioteca local `biblioteca.json` com dedupe; falha de rede → `[]`. |
| `obsidian.py` | 11 KB | Vault estilo Obsidian: diretório de `.md` com tags, subpastas e `[[wikilinks]]` bidirecionais; índice recalculado dos arquivos. |
| `slash.py` | 11,1 KB | Dispatcher puro de comandos `/`: `parsear_slash` + `executar_slash` — reusa funções reais (papeis, memória, plano, notas, vault, científico), ações de app via marcador `@@ACAO:` |
| `prompts.py` | 17 KB | Construção do system pt-BR: identidade, perfil do usuário, resumo comprimido, catálogo de ferramentas, contexto AGENTS.md, papel ativo, tarefa, bloco de segurança, regras de ferramentas. |
| `prompts_avancados.py` | 13,3 KB | Formato **APF** (JSON5-lite): comentários `//`/`#`, vírgulas pendentes, variáveis `${chave}`; fichas em `config/prompts_avancados/`; ativação persistida. |
| `plugins.py` | 3,9 KB | Carregamento dinâmico de `extensions/plugins/*.py` (função `registrar()`), recarga em runtime sem reiniciar o grafo. |
| `skills.py` | 5,7 KB | Skills auto-evolutivas: `extensions/skills/<nome>/SKILL.md` (frontmatter) → tool `usar_skill_<nome>`; `criar_skill` valida e grava novo SKILL.md (o agente evolui o repo). |
| `tarefas.py` | 7 KB | Todo (paridade Hermes `todo_tool`): uma tool `tarefas` — informou = escreve, omitiu = lê. |
| `agendador.py` | 12 KB | Cron interno (paridade Hermes): `agendar`/`agendamentos` em `agendamentos.jsonl` (gitignored), daemon `pixi run agendador` executa vencidos no mesmo grafo, webhook de callback. |
| `autorizacoes.py` | 1,1 KB | Aprovação de comandos na sessão: `executar_comando` com `confirmar=True` recusa por padrão; se o usuário aprova na janela web, o comando exato fica aprovado em memória (até reiniciar). |
| `contexto.py` | 1,8 KB | Porta do AGENTS.md/CLAUDE.md/.cursorrules: o system anexa a convenção do projeto. |
| `config_json.py` | 1,6 KB | `carregar_config_json(nome, padroes)`: `config/dados/<nome>.json` sobrescreve com merge raso; inválido/ausente → padrão (fallback seguro). |
| `exportador.py` | 6,7 KB | Trajetórias → datasets ShareGPT / OpenAI (ChatML) para fine-tuning/RLHF. |
| `tui.py` | 21,7 KB | TUI Textual estilo Hermes: chat + painel lateral (modelo/papel/APF/sessão/métricas), statusbar (tempo/tok/s/tokens/chamadas), modo RAW, atalhos, notificações; só conhece o grafo por `astream_events`. |
| `webui_bridge.py` | 21,5 KB | **Ponte web**: processo persistente, executa o grafo com `astream_events(v2)` e fala JSONL com o Bun. Frames: `token`/`reasoning` (cumulativos p/ reconnect), `tool_inicio`/`tool_fim`, `arquivo` (diff), `comando` (extrai `backend=`), `subgrafo`, `veredito`, `final`, `erro`, `orcamento`, `pergunta`; comando `cancelar`. |

### 6.3 Pacote `aegis/ferramentas/`

| Arquivo | Tamanho | Descrição |
|---|---|---|
| `__init__.py` | 7,2 KB | Registro central. `recarregar_tudo()` monta a lista completa de ferramentas — **duas montagens** (main.py e ponte) — com `ferramentas_basicas()` (basic, sistema, uso, trabalho, skills…), `ferramentas_trabalho()` (G5), pools e plugins. |
| `basicas.py` | 12,6 KB | **Tools built-in**: `calculadora` (eval seguro com AST restrito), `hora_atual`, `buscar_web` (fallback offline → lista vazia), `relogio` (multi-fuso, via `relogio.py`), `comando_sandbox` (C7 — executa nos backends local/docker/ssh com auditoria `_auditar_comando` no MESMO `comandos.jsonl` da tool `comando`), `estatisticas` (C6 — tokens/custo/sucesso/top tools, export JSON) e `consultar_grafo` (G4 — navegação no grafo de conhecimento, sem LLM). |
| `sistema.py` | 15 KB | **Ferramentas do sistema**: `escrever_arquivo`/`editar_arquivo`/`ler_arquivo`/`listar_arquivos` (sandbox de caminho restrito a `artefatos_dir` + raiz, anti path-traversal, diffs unified) e `executar_comando` (política de segurança + `confirmar`/autorização + auditoria `_registrar_comando` com campo `backend`). |
| `trabalho.py` | 10,9 KB | **G5.** `pausar_trabalho(motivo)` — handoff na Store (namespace `handoffs/`, fase/plano/critérios/commits + próximos passos por regra); `retomar_trabalho()` — contexto completo de retomada; `reverter_entrega(sha?)` — `git revert` validado (regex SHA, subprocess em `RAIZ`); `replay_turno(limite)` — forensics: re-executa os `registros_ferramentas` (sem LLM) e reporta ✓ igual / ✗ diferente / não reproduzível. |
| `pools.py` | 4,1 KB | Pools por domínio (programacao, pesquisa, escrita, obsidian, memoria): subconjuntos declarativos para os especialistas multiagente. |
| `relogio.py` | 1,3 KB | Tool `relogio`: data/hora de um ou vários fusos IANA (separados por vírgula). |

### 6.4 Pacote `aegis/gateways/`

| Arquivo | Tamanho | Descrição |
|---|---|---|
| `__init__.py` | 450 B | Docstring do pacote: canais desacoplados que consomem o mesmo grafo (`processar_mensagem(app, thread_id, texto)`). |
| `webhook.py` | 3,6 KB | Gateway HTTP mínimo (stdlib `http.server`): `POST /mensagem` → `{resposta, ferramentas, thread_id}`; `GET /healthz` → `{status, versao}`. Sem dependências novas. |

### 6.5 Web UI — `webui/` (Bun + TypeScript)

| Arquivo | Tamanho | Descrição |
|---|---|---|
| `server.ts` | 14,8 KB | **Servidor Bun :8788.** Fila FIFO de jobs; `POST /api/mensagem` → 202 `{job_id}`; `GET /api/stream?job_id=` → SSE com `:open` e keepalive `: ping` (DeepSeek fica mudo no reasoning); `server.timeout(req, 0)`; serve estáticos e vendor; rotas de saúde/jobs. |
| `bridge.ts` | 4,6 KB | **Ponte Python**: spawna `python -m aegis.webui_bridge`, 1 comando/linha no stdin, 1 frame/linha no stdout; `comando` injetável nos testes (bridge fake). |
| `app.ts` | 29,4 KB | **Front**: chat streaming com markdown avançado, thinking colapsável, feed de atividade (tools/arquivo/comando/subgrafo/veredito — chip `backend` na C7), painel técnico (métricas, wire, config, histórico), cards de orçamento/gaps, interromper job, entrada com `/`, `@`, `-/`. |
| `markdown.ts` | 2,1 KB | Markdown leve escape-first (zero HTML arbitrário): inline code, negrito, itálico, listas, títulos. |
| `markdown2.ts` | 5,9 KB | Markdown avançado: **KaTeX** (vendor global) e **mermaid** (placeholder + `executarMermaid` via DOM); slots temporários sobre o render leve. |
| `diff.ts` | 1,5 KB | Render de diff unified colorido (linha a linha), zero deps — cobre o formato do difflib das tools de arquivo. |
| `index.html` | 20,3 KB | Layout 3 colunas dark (chat/feed/painel), vendor katex, CSS design system, mermaid. |
| `server.test.ts` | 11 KB | Testes bun do servidor: fila, SSE, cancelamento, healthz. |
| `markdown2.test.ts` | 2,8 KB | Testes bun do markdown avançado (mermaid/KaTeX puros, sem DOM): 25 tests / 77 `expect()` no total. |
| `fixtures/bridge_fake.mjs` | 3,5 KB | Ponte fake para os testes do servidor (frames scriptados). |
| `package.json` | 123 B | Deps: `katex` e `mermaid` (vendored no bundle — bundle final 14 KB). |
| `bun.lock` | 22,6 KB | Lockfile do Bun. |

### 6.6 Testes — `tests/` (39 arquivos)

| Arquivo | Descrição |
|---|---|
| `conftest.py` | **ModeloFake** — `BaseChatModel` determinístico com lock global (nós paralelos consomem a fila em ordem), helper `chamada_tool` e `basico_tools()`. O fake consome **5 respostas/turno** (tool → resposta → verificação → resumo estrutural → reflexão). |
| `test_grafo.py` | **37 testes** do núcleo: roteamento, auto-correção, persistência, ciclo G1 completo (`_executar_entrega_com_uat` — padrão dos testes de entrega), helpers `_C()` com `learnings_dir`/`grafo_path` em tmp (zero poluição). |
| `test_seguranca.py` | C5: property tests (hypothesis, `max_examples=15`) + integração anti-injeção. |
| `test_orcamento.py` | C6: medição por passo, corte em estouro, evento `orcamento`, tool `estatisticas`. |
| `test_sandbox_distribuido.py` | C7: 17 testes — contrato docker/ssh com subprocess mockado (denylist, allowlist, env limpo, auditoria) + integração docker real opcional. |
| `test_aprendizados.py` | G4: 9 testes — 4 categorias, grafo, `consultar_grafo`, arquivo versionado (tmp). |
| `test_trabalho_g5.py` | G5: 10 testes — handoff, retomada com invariante (nenhum passo re-executado), revert (repo git tmp, SHA válido/HEAD/inválido), replay (igual/diferente/não reproduzível). |
| `test_neografo.py` | M1: 23 testes — classificação trivial×importante (regras do usuário), Cypher `$parametrizado` (anti-injeção), driver mockado (distribuição universal×privado, idempotência), fallback sem Neo4j + integração real com container `neo4j:5` (skip automático). |
| `test_multiagente.py` | Orquestrador, especialistas paralelos, avaliador, pools, reducer de rascunhos, rota legado × subgrafo. |
| `test_subagentes.py` | Delegação agent-as-tool (determinístico, sem rede). |
| `test_webui_bridge.py` | Frames do protocolo da ponte via `executar_job` (sem subprocesso). |
| `test_tui.py` | TUI headless com produtor de eventos fake. |
| `test_ferramentas.py` / `test_ferramentas_arquivo.py` / `test_ferramentas_comando.py` | Tools básicas; sandbox de caminho + diffs; política do `executar_comando` + auditoria + env limpo. |
| `test_modulos.py` | Memória (Store), habilidades, plugins e trajetória. |
| `test_prompts_avancados.py` | Formato APF (JSON5-lite). |
| `test_papeis.py` / `test_memoria_camel.py` / `test_camel_kit.py` | Papéis, memória pontuada e toolkits CAMEL. |
| `test_cientificas.py` / `test_obsidian.py` | arXiv/BibTeX/APA (fallback offline); vault wikilinks/backlinks/tags. |
| `test_slash.py` / `test_sistema.py` | Slash commands; prompt de sistema (regras, anti-loop). |
| `test_recuperacao.py` / `test_memoria_tool.py` / `test_sessoes.py` / `test_tarefas.py` | RAG-lite; memória explícita; Session Recall; Todo. |
| `test_agendador.py` / `test_gateway.py` / `test_relogio.py` | Cron interno; webhook HTTP; relógio multi-fuso. |
| `test_exportador.py` / `test_config_json.py` / `test_contexto.py` / `test_autorizacoes.py` | Datasets; config JSON; AGENTS.md; autorizações. |
| `test_backup.py` | Rotina de backup (script real em destino temporário). |

### 6.7 Documentação — `docs/`

| Arquivo | Descrição |
|---|---|
| `planejamento-nucleo.md` | **A spec das 23 fases** (C1–C7, G1–G5, X1–X11): objetivo, mudanças, testes, critérios de aceite, dependências e ordem recomendada — **36,8 KB**. É o contrato do roadmap. |
| `multiagente.md` | Design do multiagente v2: Send/Command/interrupt, avaliador por execução, fallback adaptativo, domínios declarativos, HITL. |
| `webui.md` | Design de engenharia da Web UI v2: Bun + ponte JSONL + SSE, AG-UI (espírito), painéis técnicos. |
| `tecnical.md` | **Este documento.** |

### 6.8 Configuração — `config/`

| Arquivo | Descrição |
|---|---|
| `env/.env` | **Segredo — nunca versionado** (gitignored). Chaves do provider. |
| `env/.env.example` | Modelo documentado: `OPENAI_API_BASE/KEY`, `MODEL_NAME`, `AEGIS_TEMPERATURA`, `AEGIS_MAX_TOKENS`, `AEGIS_ARTEFATOS_DIR`, `AEGIS_COMANDOS`, `AEGIS_SEARXNG_URL`, `AEGIS_SANDBOX_BACKEND` (local\|docker\|ssh), `AEGIS_OBSIDIAN_DIR` etc. |
| `dados/limites.json` | Limites centralizados (contexto/trecho/resultado/recursion_limit), `checklist_revisao` (5 itens do G3) e `precos_por_token` (C6). |
| `dados/agendador_config.json` | Frequências e intervalo do cron (sobrescreve padrões via config_json). |
| `dados/delegados.json` | **X1.** Catálogo de subagentes: 5 delegados (pesquisador, redator, codigo, dados, revisao) com descrição (para o LLM), `parametro` (pergunta/tarefa), `ferramentas` (por nome) e `arq_limite` (anti-cascata); campo `tool` preserva `delegar_pesquisa`/`delegar_redacao`. |
| `dados/tarefas_config.json` | Limites da tool `tarefas`. |
| `dados/papeis.json` | Catálogo de papéis (estende padrões de código). |
| `dados/memoria_camel_config.json` | Pesos da pontuação CAMEL. |
| `dados/datasets/` | Exemplos de datasets exportados (ShareGPT/OpenAI). |
| `dados/*.json (runtime)` | `plano_tarefas.json`, `pensamento_atual.json`, `prompt_ativo.json`, `tarefas.json`, `memoria_camel.json` — estado vivo das tools (gitignored). |
| `dados/*.jsonl (runtime)` | `comandos.jsonl` (auditoria unificada com `backend`), `orquestracoes.jsonl` (auditoria multiagente) — gitignored. |
| `dados/trajetorias/` | Trajetórias JSONL por dia (matéria-prima do Session Recall e do exportador) — gitignored. |
| `dados/artefatos/` | Sandbox de escrita das tools de arquivo — gitignored. |
| `dados/memoria_agente.db` | **Checkpoints + Store SQLite** — gitignored. |
| `prompts_avancados/` | Fichas `.apf`: `pesquisa-profunda.apf`, `revisor-codigo.apf`. |

### 6.9 Extensões — `extensions/`

| Arquivo | Descrição |
|---|---|
| `plugins/exemplo_plugin.py` | Plugin de exemplo: `registrar()` → tool `contar_palavras` (demonstra o contrato de plugins). |
| `skills/pesquisa-tecnica/SKILL.md` | Skill de metodologia de pesquisa técnica (frontmatter name/description + procedimento) — a base da fase X2. |

---

## 7. Detalhamento por arquivo — funções, classes e tools registradas

> Esta seção foi **extraída do código-fonte** (análise AST): para cada arquivo `.py`,
> as funções, classes, métodos e ferramentas registradas. `**[@tool]**` = ferramenta
> exposta ao LLM. Quando não há docstring, a assinatura é listada sem descrição.

---

### Pasta `Raiz do projeto`

**`main.py`**

- `novo_argumentos()`
- `_aplicar_flags(args)` — Ajusta a configuração conforme as flags de linha de comando.
- `listar_ferramentas(ferramentas)`
- `listar_skills()`
- `_exportar(formato, destino)` — Exporta trajetórias para dataset ShareGPT ou OpenAI (fine-tuning/RL).
- `_rodar_gateway(porta)` — Serve o grafo via Webhook HTTP (mesma lógica da TUI, sem terminal).
- `_rodar_agendador(intervalo, uma_vez)` — Loop do cron: executa agendamentos vencidos no grafo a cada intervalo.
- `_montar_app_sync()` — Constrói o grafo com checkpointer síncrono (headless/testes).
- `_montar_app_async()` — Constrói o grafo com checkpointer assíncrono (TUI — astream_events).
- `_imprimir_resultado(resultado, cfg)` — Imprime o resultado de uma execução headless (painel + ferramentas).
- `executar_headless(app, ferramentas, pergunta, cfg)` — Execução síncrona one-shot (também usada nos testes).
- `main(argv)`

### Pasta `aegis/`

**`aegis/__init__.py`**

- _(só docstring de pacote/módulo)_

**`aegis/agendador.py`**

- `_fuso_local()` — tzinfo local concreto, com fallback a UTC (evita dep. de tzdata).
- `_agora()` — Instante atual (UTC). Isolado para permitir freeze em testes.
- **classe `ArmazenamentoAgendamentos`** — Armazenamento de agendamentos em arquivo JSONL (lock por escrita).
  - `carregar(self)`
  - `salvar(self, itens)`
  - `adicionar(self, item)`
- `_parsear_quando(quando, agora)` — Converte um alvo legível em datetime tz-aware. Aceita: "agora", ISO datetime ("2026-08-05T09:00"), ou relativo ("em 5 mi
- `_reagendar(item)` — Avança `quando_iso` conforme a frequência, se for recorrente.
- `agendar_tarefa(tarefa, quando, frequencia, caminho, agora)` — Cria um agendamento e retorna o registro (com `id`).
- `listar(caminho, estados)` — Lista agendamentos ativos (não concluídos/cancelados), por instante.
- `cancelar(agend_id, caminho)` — Cancela um agendamento pelo id. Retorna False se não encontrar.
- `vencidos(agora, caminho)` — Agendamentos 'agendado' com instante alvo <= `agora` (determinístico).
- `_executar_um(agend, app)` — Executa a tarefa no grafo e devolve a resposta final.
- `_notificar(webhook_url, agend)` — Notifica um webhook (callback) sobre a conclusão de um agendamento.
- `executar_vencidos(app, agora, caminho, webhook_url)` — Executa todos os vencidos no grafo e atualiza a persistência. Retorna a lista de agendamentos processados (concluídos, r
- `agendar(tarefa, quando, frequencia)` **[@tool]** — Agenda uma tarefa para execução autônoma futura (cron interno). Args: tarefa: o que executar (mensagem natural para o ag
- `listar_agendamentos()` **[@tool]** — Lista os agendamentos ativos do cron interno.
- `cancelar_agendamento(id_agendamento)` **[@tool]** — Cancela um agendamento pendente pelo seu id.

**`aegis/aprendizados.py`**

- `classificar(texto)` — Classifica um aprendizado em uma das 4 categorias, por regras.
- `nome_arquivo_sessao(thread_id)` — Nome de arquivo seguro a partir do thread_id (sanitização).
- `bloco_markdown(licoes, ts)` — Bloco markdown das lições: [(texto, prioridade, categoria)].
- **classe `GrafoConhecimento`** — Grafo consultável de aprendizados (entidades + relações derivadas). Persistido em JSON. Extração por regras — sem LLM, s
  - `adicionar(self, categoria, texto, ferramenta, fase, erro)` — Registra um aprendizado no grafo; retorna o id da entidade.
  - `consultar(self, termo, limite)` — Entidades que casam `termo` + relacionadas (mesmos atributos).
  - `formatar(self, termo, limite)` — Consulta formatada para a tool (sem rede).
  - `salvar(self)`

**`aegis/autorizacoes.py`**

- `aprovar_comando(comando)` — Registra o comando exato como aprovado na sessão.
- `comando_aprovado(comando)`
- `aprovados()`
- `limpar()`

**`aegis/camel_kit.py`**

- `pensar(passo_raciocinio)` **[@tool]** — Registra um passo de raciocínio e devolve a cadeia completa numerada.
- `ver_pensamento()` **[@tool]** — Mostra a cadeia de raciocínio registrada até agora.
- `_parsear_passos(texto)` — Converte '1. x 2. y' ou '- x - y' numa lista de passos limpos.
- `planejar_tarefa(objetivo, passos)` **[@tool]** — Cria (ou sobrescreve o plano atual) um plano de tarefas em passos (um por linha, '- x' ou '1. x').
- `atualizar_plano(id, novo_status)` **[@tool]** — Atualiza o status de um passo do plano (pendente|executando|ok|cancelado).
- `ver_plano()` **[@tool]** — Mostra o plano de tarefas atual com o progresso.
- `_formatar_plano(plano)` — Formata um plano (passos + progresso) — função pura p/ reuso interno.
- `anotar(nota)` **[@tool]** — Registra uma nota rápida no bloco de notas (histórico anexado).
- `ver_notas(qtd)` **[@tool]** — Lista as últimas N notas registradas.

**`aegis/cientificas.py`**

- `_extrair_arxiv_id(url)` — 'http://arxiv.org/abs/2401.12345v2' → '2401.12345v2'.
- `_normalizar_paper(entry)` — Constrói o dict normalizado a partir de um <entry> do Atom.
- `parsear_arxiv_xml(texto)` — Faz parse do feed Atom do arXiv — função pura (sem rede).
- `buscar_papers(consulta, n)` — Busca `n` papers por consulta (all:).
- `buscar_paper_por_id(id_arxiv)` — Busca um único paper pelo id (ex.: '2401.12345v2').
- `gerar_bibtex(paper)` — Entrada BibTeX determinística.
- `citar_apa(paper)` — Citação APA 7 simplificada (determinística).
- `_salvar_paper_biblioteca(paper)` — Adiciona o paper à biblioteca (dedupe por id). Retorna True se novo.
- `buscar_papers_arxiv(consulta, n)` **[@tool]** — Busca papers na API do arXiv por consulta e lista título/autores/url.
- `gerar_citacao_bibtex(id_arxiv)` **[@tool]** — Gera a entrada BibTeX de um paper já salvo na biblioteca (use salvar_paper antes).
- `salvar_paper(id_arxiv)` **[@tool]** — Salva um paper (por id) na biblioteca e cria uma nota de leitura no vault.
- `revisar_literatura(consulta, n)` **[@tool]** — Busca o arXiv e monta uma revisão de literatura com citações APA e BibTeX.

**`aegis/config.py`**

- **classe `ConfigError`** — Erro de configuração (ex.: chave de API ausente).
- **classe `Config`** — Contém toda a configuração do Aegis.

**`aegis/config_json.py`**

- `carregar_config_json(nome_arquivo, padroes, caminho)` — Carrega `nome_arquivo` de `config/dados/` e faz merge sobre `padroes`. - Arquivo ausente ou inválido → retorna os padrõe

**`aegis/contexto.py`**

- `ler_contexto(caminho)` — Lê um arquivo de contexto textual de forma segura. Retorna "" se o arquivo não existir, for ilegível ou ficar acima do l
- `contexto_do_projeto()` — Lê o contexto ativo do projeto conforme config.contexto_path.

**`aegis/estado.py`**

- `_merge_dict(atual, novo)` — Reducer de merge para dicionários escritos por nós em paralelo. Cada especialista grava a SUA chave (slot) no dict; o re
- **classe `EstadoAegis`**

**`aegis/exportador.py`**

- `carregar_registros(diretorio)` — Carrega e mescla todos os `*.jsonl` do diretório, ordenados por `ts`.
- `agrupar_por_thread(registros)` — Agrupa os registros por `thread_id`, preservando a ordem temporal.
- `_converter_para_mensagens(registros)` — Converte registros de uma thread em pares {role, content} (OpenAI/ChatML).
- `_anexar_notas(mensagens, notas)` — Anexa notas de ferramenta pendentes à última mensagem do assistente.
- `_para_sharegpt(mensagens)` — Converte pares {role, content} para o formato ShareGPT {from, value}.
- `exportar_sharegpt(diretorio, saida)` — Exporta todas as trajetórias como um arquivo JSON no formato ShareGPT. Retorna resumo: {arquivo, conversas, threads, pul
- `exportar_openai(diretorio, saida)` — Exporta as trajetórias como JSONL no formato OpenAI (SFT/RLHF). Uma linha por conversa: {"messages": [...]}. Padrão usad
- `_destino(saida, prefixo, sufixo)` — Define o caminho de saída padrão: `config/dados/datasets/<prefixo><sufixo>`.

**`aegis/grafo.py`**

- `montar_grafo(llm, ferramentas, checkpointer, store, cfg)` — Compila o grafo completo do Aegis. Args: llm: modelo ChatOpenAI (provedor cognitivo) ferramentas: ferramentas registrada
- `mk_config(thread_id)` — Config de execução padrão (thread_id).
- `executar_headless(app, pergunta, thread_id)` — Executa uma pergunta de forma síncrona (automação/testes).

**`aegis/llm.py`**

- `criar_llm(config, streaming, **extra)` — Cria um `ChatOpenAI` a partir da configuração (.env). `streaming=True` faz o modelo emitir eventos de token para a TUI (
- `_eh_erro_transitorio(exc)` — True para erros 429/5xx / de conexão — merecem retry com backoff.
- `_espera_retry(exc, base_espera, tentativa)` — Calcula o backoff, respeitando `Retry-After` quando presente.
- `com_retry(fn, tentativas, base_espera)` — Executa `fn` com retry em erros transitórios (rate-limit / 5xx). Levanta o último erro caso ele não seja transitório ou 
- `invocar_com_retry(llm, mensagens, **kwargs)` — Wraper que chama o modelo com retry e isolamento de falhas de cota.

**`aegis/memoria.py`**

- `_conexao(caminho, rotulo)` — Abre (e reutiliza) uma conexão SQLite persistente por componente. `rotulo` isola checkpointer ("ckpt") de store ("store"
- `_setup(obj)` — Chama `setup()` de forma síncrona (aceitando coroutine, se houver).
- `criar_checkpointer_sync(caminho)` — Checkpointer síncrono persistente (CLI/headless e testes).
- `criar_checkpointer_async(caminho)` — Checkpointer assíncrono (TUI — necessário para `astream_events`). Nota: langgraph 1.x exige AsyncSqliteSaver (mais aiosq
- `criar_store_sync(caminho)` — Store de longo prazo síncrona persistente.
- `namespace_perfil()` — Namespace global do perfil do usuário (entre TODAS as sessões).
- `namespace_memoria(thread_id)` — Namespace de memória por tópico/conversa.
- `namespace_licoes()` — Namespace das lições aprendidas (memória procedimental global).
- `namespace_handoffs()` — Namespace dos handoffs de trabalho pausado (retomável por thread).
- `namespace_handoff_thread(thread_id)` — Namespace do handoff de UMA sessão (sem perfil de dados).
- `namespace_resumos(thread_id)` — Namespace dos resumos incrementais por sessão (C4).
- `namespace_decisoes(thread_id)` — Namespace das decisões-chave por sessão (C4).
- `namespace_uat(projeto)` — UAT por PROJETO (não thread): sobrevive a `/clear` e a troca de sessão.

**`aegis/memoria_camel.py`**

- **classe `RegistroMemoria`** — Um registro da memória pontuada.
  - `de_dict(cls, dados)`
  - `as_dict(self)`
- `_tokenizar(texto)` — Tokens minúsculos sem stopwords (para overlap lexical).
- `pontuacao(conteudo, consulta_tokens, importancia, ts, agora, peso_importancia, meia_vida)` — Pontua um registro contra a consulta (recência + importância + overlap).
- `carregar_memoria(caminho)` — Carrega os registros do arquivo ([] se ausente/inválido).
- `salvar_memoria(registros, caminho, n_max)` — Grava os registros (limitados a n_max, do mais recente para o antigo).
- `consultar_topk(consulta, registros, k, caminho, agora, peso_importancia)` — Top-k registros por pontuação contra `consulta` (ordem decrescente).
- `registrar_memoria_camel(conteudo, importancia, fonte)` **[@tool]** — Registra um fato/nota na memória pontuada (importância 0-10, padrão 5).
- `consultar_memoria_camel(consulta, k)` **[@tool]** — Consulta os k registros mais relevantes da memória pontuada para a consulta.
- `esquecer_memoria_camel(id_registro)` **[@tool]** — Remove um registro da memória pelo seu id.

**`aegis/memoria_tool.py`**

- `definir_store(store)` — Vincula a Store de longo prazo à ferramenta de memória explícita.
- `_fatos_todos()` — Chaves dos fatos gravados em ("aegis", "fatos").
- `gerenciar_memoria(acao, conteudo, chave, alvo)` **[@tool]** — Grava, esquece ou lista memória de longo prazo de forma EXPLÍCITA e durável. - acao: "salvar" grava um fato; "esquecer" 

**`aegis/multiagente.py`**

- **classe `Dominio`** — Registro de um domínio multiagente.
- `classificar_dominio(pergunta, limiar)` — Classifica a pergunta em um domínio por regras (zero LLM, rápido). Cada gatilho presente soma 1 ponto; vence o domínio c
- `divisao_do_dominio(dominio, pergunta, max_especialistas)` — Monta os slots do domínio (template determinístico).
- `parsear_veredito(texto)` — Parse tolerante do JSON de veredito do avaliador (estilo APF).
- `_registrar_jsonl(cfg, dominio, divisao)` — Auditoria em config/dados/orquestracoes.jsonl (base para cache F3).
- `montar_subgrafo_dominio(dominio, llm, ferramentas, cfg)` — Compila o subgrafo stateless de um domínio (especialistas + avaliador). Estrutura: START → no_fanout (Send ×N) → no_slot
- `montar_orquestrador(cfg)` — Monta o nó orquestrador (classificação por regras) e sua rota.
- `montar_multiagente(cfg)` — Monta orquestrador + rota multiagente para o wire do grafo principal. A rota mapeia o domínio decidido no turno para o n
- `obter_subgrafo(dominio, llm, ferramentas, cfg)` — Compila (ou reusa) o subgrafo compilado de um domínio.

**`aegis/nos.py`**

- **classe `_CapturaRaciocinio`** — Coleta o `reasoning_content` dos chunks do stream (DeepSeek/Zen). O DeepSeek em modo thinking EMITE o raciocínio nos chu
  - `on_llm_new_token(self, token, chunk, **kwargs)`
- `_eh_erro(mensagem)` — True se a mensagem de ferramenta indica falha (prefixo de erro).
- `_parsear_json_fatos(texto)` — Faz parse tolerante do JSON de fatos retornado pelo LLM.
- `_parsear_licoes(texto)` — Parse tolerante do JSON de lições: [(texto, prioridade)] (máx. 3).
- `_prioridade_por_repeticao(registros)` — True se a MESMA ferramenta falhou ≥2× com o mesmo erro no turno. Repetição de falha é o sinal mais forte de lição duráve
- `_precisa_plano(pergunta)` — Heurística barata (zero LLM) de complexidade da tarefa. Ativa planejamento quando a pergunta pede uma ENTREGA multi-pass
- `_eh_pedido_entrega(pergunta)` — Zero-LLM: pedido de ENTREGA (código/artefato/documento) vs. pergunta informativa. Verbo de entrega + sinal de repo; pref
- `_eh_ambiguo(pergunta)` — Zero-LLM: pedido de entrega sem especificação (detalhes de execução) → discuss deve perguntar antes de planejar.
- `_parsear_vereditos_entrega(texto, total)` — Parse tolerante do JSON do verify goal-backward (G1): lista de {indice, verificado, evidencia} na ordem dos critérios.
- `_parsear_revisao(texto)` — Parse tolerante do JSON do revisor por pares (G3): lista de {item, veredito, apontamento}. Item ausente na resposta = re
- `_parsear_plano(texto)` — Parse tolerante do JSON do plano: lista de {passo, objetivo} (máx. 6).
- `_bloco_plano(plano)` — Renderiza o plano ativo com progresso para injeção no system.
- `_parsear_verificacao(texto)` — Parse tolerante do JSON de verificação: {"veredito", "evidencias"}. Retorna None quando não há JSON válido — o fluxo tra
- `fabricar_nos(llm, ferramentas, store, cfg, prompt_fn)` — Cria todos os nós do grafo com o contexto injetado. `prompt_fn` (opcional) substitui o prompt de sistema padrão — usado 

**`aegis/neografo.py`** — M1: memória GraphRAG — dois grafos Neo4j

- `classificar_registro(registro)` — Classifica um registro do turno como 'privado' (trivial) ou 'universal'.
- `_id_de(texto, prefixo)` — Id determinístico (hash) — lições idempotentes; CREATE usa ts_ns.
- `classificar_e_tipo(registro)` — (grafo, subtipo) para gravação — classificação + tipo de nó.
- `classe GrafoNeo4j` — Cliente mínimo do Neo4j: gravação nos dois grafos + consulta GraphRAG.
  - `saude()` — Ping rápido no banco (verifica conectividade + schema).
  - `_criar_schema(driver)` — Constraints + índice (idempotentes, IF NOT EXISTS).
  - `_executar(cypher, params)` — Executa com parâmetros; falha silenciosa (grafo nunca derruba).
  - `_executar_mutacao(cypher, params)` — Executa um MERGE/CREATE (sem RETURN) e confirma via .consume().
  - `gravar_licao(texto, categoria, ferramenta, fase, erro, prioridade, thread_id)` — Lições (G4) sobem ao grafo universal — id determinístico (idempotente).
  - `gravar_tarefa_final(texto, veredito, origem, thread_id)` — Estado final de uma tarefa (entrega G1 / orquestrador) → universal.
  - `gravar_modificacao(texto, tipo, ferramenta, thread_id)` — Modificação persistente do ambiente (ex.: dependência instalada).
  - `gravar_trivial(texto, tipo, ferramenta, execucao_id, thread_id)` — Detalhe trivial (retry/sintaxe/contexto bruto) → privado, com TTL.
  - `limpar_privado(execucao_id)` — Remove os triviais de uma execução (ciclo de vida restrito).
  - `purga_vencidos()` — Purga lazy: triviais com expira_em vencido.
  - `consultar(termo, grafo, limite)` — Busca por entidade/termo + nós relacionados (1 salto) — GraphRAG.
  - `fechar()` —
- `grafo_neo4j(cfg)` — Singleton do cliente, sob a configuração atual (None se desativado).
- `consultar_graphrag(cfg, termo, grafo, limite)` — Consulta o grafo Neo4j; None quando inativo (fallback → RAG-lite).
- `gravar_turno_graphrag(cfg, registros, licoes_com_categoria, fase, erro, thread_id)` — Grava o turno nos dois grafos Neo4j — no-op completo sem Neo4j ativo.

**`aegis/obsidian.py`**

- `extrair_links(texto)` — Destinos dos [[wikilinks]] (alias após '|' é descartado).
- `extrair_tags(texto)` — Tags `#tag` (sem duplicatas, fora de links).
- `_notas_no_vault(vault)` — {nome_da_nota: caminho} — varre todos os .md do vault (recursivo).
- `_titulo(texto, padrao)` — Título exibido: primeiro '# Título' do arquivo, senão o nome base.
- `recalcular_indice(vault)` — Índice: por nota — links emitidos, tags e backlinks (derivados).
- `_carregar_indice(vault)` — Lê indice.json se possível; senão recalcula (nunca fica obsoleto).
- `_nome_arquivo(nome)` — Nome amigável → nome de arquivo seguro (espaços viram _, sem barras).
- `_caminho_nota(nome, vault)` — Localiza em qualquer subpasta (nome exato ou nome de arquivo seguro).
- `criar_nota_obsidian(nome, conteudo, pasta)`
- `ler_nota_obsidian(nome)`
- `ligar_nota_obsidian(de, para)`
- `buscar_nota_obsidian(palavra)`
- `notas_por_tag_obsidian(tag)`
- `notas_conectadas_obsidian(nome)`
- `listar_obsidian_vault()`
- `limpar_vault(confirmar)` — Apaga as notas do vault (exige confirmar=True).
- `criar_nota(nome, conteudo, pasta)` **[@tool]** — Cria uma nota markdown no vault Obsidian do Aegis.
- `ler_nota(nome)` **[@tool]** — Lê o conteúdo de uma nota do vault Obsidian.
- `ligar_nota(de, para)` **[@tool]** — Cria um [[wikilink]] bidirecional entre duas notas do vault.
- `buscar_notas(palavra)` **[@tool]** — Busca full-text no vault Obsidian e lista as notas que contêm a palavra.
- `notas_por_tag(tag)` **[@tool]** — Lista as notas do vault que têm uma determinada tag (#tag).
- `notas_conectadas(nome)` **[@tool]** — Mostra o grafo local da nota: links emitidos, backlinks e tags.
- `listar_obsidian()` **[@tool]** — Lista todas as notas do vault Obsidian em árvore por subpasta.
- `limpar_obsidian(confirmar)` **[@tool]** — Apaga todas as notas do vault Obsidian (exige confirmar=True).

**`aegis/papeis.py`**

- **classe `Papel`** — Persona configurável: nome, descrição, identidade, instruções e foco.
- `_copiar_padrao()` — Cópia profunda dos papéis padrão (para não mutar o catálogo original).
- `carregar_papeis(caminho)` — Carrega o catálogo de papéis: padrões + extensões de `papeis.json`. - `"substituir_padrao": true` → usa SOMENTE os papéi
- `resolver_papel(nome, papeis)` — Resolve `nome` (case-insensitive) contra o catálogo de papéis.
- `_carregar_estado(caminho)` — JSON de estado (papel_ativo/tarefa_atual) com fallback a `None`.
- `ler_papel_ativo(caminho)` — Nome do papel ativo (None se nenhum).
- `ler_tarefa_atual(caminho)` — Tarefa especificada em `tarefa_atual.json` (None se ausente).
- `montar_bloco_personalidade()` — Bloco injetável no sistema: papel ativo + tarefa especificada (vazio "").
- `definir_papel(nome)` **[@tool]** — Define o papel ativo do agente (ex.: pesquisador, redator, planejador). Retorna a identidade ativada.
- `ver_papel()` **[@tool]** — Mostra o papel ativo do agente e sua identidade/instruções.
- `listar_papeis()` **[@tool]** — Lista todos os papéis disponíveis no catálogo.
- `especificar_tarefa(objetivo, restricoes, criterios)` **[@tool]** — Especifica uma TAREFA formal para o agente executar (objetivo + restrições + critérios de sucesso).
- `estruturar_tarefa(texto_livre)` **[@tool]** — Converte descrição livre em tarefa estruturada (objetivo; restrições; critérios).
- `_parsear_texto_tarefa(texto)` — Heurística determinística: objetivo na 1ª parte; restrições/critérios por marcadores.

**`aegis/plugins.py`**

- `_executar_registrar(mod, nome_arq, ferramentas)` — Chama `registrar()` do módulo e coleta as ferramentas.
- `_importar(nome, caminho)` — Importa um módulo de arquivo e o registra no sys.modules.
- `_nome_modulo(stem)` — Nome plano e único no sys.modules (reload confiável sem pacote pai).
- `carregar_plugins(diretorio)` — Importa todos os plugins e coleta as ferramentas expostas por `registrar()`.
- `recarregar_plugins(diretorio)` — Recarrega os plugins (re-importa o código atualizado do disco). Cada plugin é re-importado com um spec novo, aplicando m
- `erros_carregamento()` — Retorna erros de plugins que falharam ao carregar (para auditoria).

**`aegis/prompts.py`**

- `sistema(perfil, resumo, ferramentas, metadados)` — Monta o prompt de sistema completo (identidade + contexto + ferramentas).
- `reflexao_auto_correcao()` — Prompt do nó de reflexão: analisar erro de ferramenta e reformular.
- `resumir_historico()` — Prompt do nó de compressão: resumir mensagens antigas.
- `extrair_memoria()` — Prompt do nó de memória: extrair fatos duráveis do perfil do usuário.
- `reflexao_pos_turno()` — Prompt do nó de reflexão pós-turno (C1): extrair lições duráveis.
- `planejar_tarefa()` — Prompt do nó de planejamento (C2): quebrar tarefa complexa em passos.
- `replanejar_tarefa()` — Prompt do nó de replanejamento (C2): ajustar plano após falha de etapa.
- `verificar_resposta()` — Prompt do nó de verificação (C3): conferir a resposta contra evidências.
- `resumir_sessao()` — Prompt da memória estrutural (C4): resumo incremental + decisões.
- `sistema_pesquisador()` — Prompt do subagente PESQUISADOR (persona de pesquisa profunda).
- `sistema_redator()` — Prompt do subagente REDATOR (persona de escrita longa e estruturada).
- `sistema_especialista(dominio, slot, papel)` — Prompt de um nó ESPECIALISTA do subgrafo multiagente. O especialista recebe apenas a SUA fatia da tarefa (slot) e a sua 
- `sistema_integrador()` — Prompt do nó INTEGRADOR: consolida os rascunhos dos especialistas.
- `sistema_avaliador(dominio)` — Prompt do nó AVALIADOR: veredito estruturado sobre o artefato. Deve responder ESTRITAMENTE um JSON com as chaves: status
- `verificar_entrega()` — Prompt do verify goal-backward da entrega (G1): cada critério de aceite conferido contra as evidências reais da execução
- `revisar_entrega(checklist)` — Prompt do REVISOR por pares (G3): segunda opinião obrigatória antes do ship — cada item do checklist de normas julgado c

**`aegis/prompts_avancados.py`**

- **classe `PromptFormatoErro`** — Erro de formato ou uso dos prompts avançados (APF).
- `sanitizar_json5(texto)` — Remove comentários (`//`, `#`) e vírgulas pendentes fora de strings. Mantém intacto o conteúdo de qualquer string JSON (
- `_validar_ficha(ficha, origem)` — Valida e normaliza uma ficha bruta (do JSON). Erros viram PromptFormatoErro.
- `carregar_prompts_avancados()` — Carrega e valida todas as fichas `.apf` válidas do diretório config. Fichas com erro são ignoradas (não derrubam o agent
- `erros_de_carga()` — Motivos das fichas rejeitadas na última chamada de carga.
- `_formatar_variado(valor, variaveis)` — Serializa `formato_saida` (str ou dict) já interpolado.
- `compilar_prompt(nome, extras)` — Compila uma ficha em blocão final (prompt avançado injetável).
- `listar_prompts()` — Lista as fichas válidas (id, versão, descrição) + avisos de erro.
- `ver_prompt(nome)` — Mostra o bloco compilado de um prompt, marcando o ativo.
- `prompt_ativo_id()` — Id do prompt avançado ativo, ou `None` se nenhum.
- `prompt_ativo_compilado()` — Bloco compilado do prompt ativo; "" se nenhum/indisponível.
- `usar_prompt(nome)` — Ativa um prompt avançado (persiste o id). `nenhum` desativa.
- `desativar_prompt()` — Desativa o prompt avançado atual.
- `listar_prompts_avancados()` **[@tool]** — Lista os prompts avançados (APF) disponíveis (id, versão, descrição).
- `usar_prompt_avancado(nome)` **[@tool]** — Ativa um prompt avançado por id (nome "nenhum" desativa).
- `ver_prompt_avancado(nome)` **[@tool]** — Mostra o conteúdo compilado de um prompt avançado.

**`aegis/recuperacao.py`**

- `definir_store(store)` — Vincula a Store de longo prazo às ferramentas de memória.
- `definir_thread(thread_id)` — Vincula o thread ativo às ferramentas de memória (C4).
- `_itens_do_store()` — Recupera textos da Store (perfil global + memórias por tópico).
- `_itens_das_skills()` — Extrai nome + conteúdo das habilidades registradas.
- `_idf(corpus)` — Inverso de frequência documental — destaca termos raros/distintivos.
- `_pontuar(consulta, doc, idf)` — Soma IDF dos tokens da consulta presentes no documento.
- `pesquisar_memoria(consulta, limite)` **[@tool]** — Busca fatos e preferências do usuário na memória de longo prazo (Store) e nos resumos de habilidades (extensions/skills/
- `recuperar_licoes(store, consulta, limite)` — Recupera lições aprendidas relevantes à consulta (mesmo IDF do RAG-lite). Retorna um bloco formatado para injeção no pro
- `_nivel(secao, conteudo, teto)` — Monta um nível do recall; corta por teto de caracteres quando preciso.
- `recuperar_contexto_para_system(store, thread_id, consulta, teto)` — Recall hierárquico para injeção no system: perfil → lições → resumo → decisões. Cada nível é cortado pelo teto; a ORDEM 
- `recuperar_contexto(assunto, escopo_sessao, limite_por_nivel)` **[@tool]** — Recupera o contexto estruturado do Aegis para a tarefa atual: perfil do usuário → lições aprendidas → resumo da sessão →

**`aegis/sandbox.py`**

- **classe `ResultadoExecucao`** — Resultado de uma execução de comando.
  - `sucesso(self)`
  - `resumo(self, limite)`
- **classe `Executor`** — Interface base de sandbox.
  - `executar(self, comando, timeout, cwd)` — Executa `comando` e devolve o resultado. Nunca deve lançar na operação.
- **classe `ExecutorLocal`** — Executa comandos como subprocess local, com timeout e captura.
  - `executar(self, comando, timeout, cwd)`
- `motivo_denylist(comando)` — Primeiro padrão proibido encontrado no comando, ou None.
- **classe `ExecutorDocker`** — Sandbox via container efêmero (`docker run --rm`). Rede isolada por padrão (`--network=none`), volume dos artefatos em `
  - `executar(self, comando, timeout, cwd)`
- **classe `ExecutorSSH`** — Sandbox via host remoto (`ssh -o BatchMode=yes`, sem senha interativa). Host/usuário vêm do `.env` (`AEGIS_SSH_HOST`/`AE
  - `executar(self, comando, timeout, cwd)`
- `criar_executor(nome, cfg)` — Fábrica de executors — troca de backend sem tocar no grafo. `cfg` (opcional, `aegis.config.Config`) fornece imagem docke

**`aegis/seguranca.py`**

- `classificar_conteudo(texto)` — Classifica um texto externo quanto a padrões de instrução embutida. Returns: ``{"suspeito": bool, "padroes": [rótulos...
- `marcar_conteudo(texto, fonte)` — Anexa o marcador de classificação e a ``_fonte`` ao resultado. O resultado das ferramentas de leitura SEMPRE carrega o m

**`aegis/sessoes.py`**

- `_data_iso(ts)` — Extrai a parte de data (AAAA-MM-DD) de um timestamp ISO; fallback.
- **classe `Sessao`** — Uma sessão = um dia + uma thread_id (recorte de troca).
  - `adicionar(self, tipo, conteudo, ts)`
  - `texto(self)`
- `_ler_trajetorias(diretorio)` — Lê todos os JSONL de trajetória e monta sessões (thread+dia).
- `_tokens(frase)` — Tokeniza, remove acentos e margessa a STOPWORDS.
- `_ranquear(consulta, sessao)` — Escore por cobertura de tokens + bônus de frequência (IDF-like).
- `_trecho_com(query, sessao)` — Retorna a 1ª mensagem da sessão que contém a consulta (ou a última).
- `_marcadores(sessao)` — Primeiras e últimas 3 mensagens (marcador de braço), como no Hermes.
- **classe `SessoesIndex`** — Índice em memória (recuperável) sobre as trajetórias de um diretório.
  - `descobrir(self, consulta, limite)` — Top-N sessões ranqueadas por relevância, com trecho destacado.
  - `rolar(self, sessao_id, mensagem, janela)` — Janela de mensagens ao redor de ``mensagem`` (scroll).
  - `navegar(self, limite)` — Sessões recentes (data desc), com prévia, ignorando fontes automáticas.
- `pesquisar_sessoes(consulta, sessao, mensagem, janela, limite)` **[@tool]** — Pesquisa em conversas ANTERIORES armazenadas nas trajetórias do agente. Use quando a resposta depender de algo já dito e

**`aegis/skills.py`**

- `_parsear_frontmatter(texto)` — Extrai frontmatter (name/description) e o corpo do SKILL.md.
- `carregar_skills(diretorio)` — Varre `<diretorio>/**/SKILL.md` e retorna {nome_registrado: {"descricao", "conteudo", "caminho"}}.
- `criar_skill_path(diretorio, nome, descricao, conteudo)` — Valida e grava uma habilidade no padrão agentskills.io. Retorna o caminho.
- `ferramentas_skills(habilidades)` — Cria ferramentas `usar_skill_<nome>` para cada habilidade carregada.
- `carregar_e_expor(diretorio)` — Le as habilidades e devolve as ferramentas correspondentes.

**`aegis/slash.py`**

- `parsear_slash(texto)` — Splita '/nome arg' (None se não for slash).
- `executar_slash(nome, arg)` — Execute o comando e devolve o texto de resposta.

**`aegis/subagentes.py`** — X1: catálogo de delegados sob demanda + fábrica de tools

- `_persona_padrao(...)` — Persona genérica para delegados custom do catálogo (sem prompt próprio).
- `_carregar_catalogo(...)` — Lê `delegados.json` com fallback para o catálogo embutido.
- `_registro_por_nome(...)` — Registro das ferramentas disponíveis para pools, por nome.
- `_resolver_pool(...)` — Resolve os nomes do catálogo para tools reais (desconhecidas ignoradas).
- `_delegado_por_tool(...)` — Delegado do catálogo vigente cuja tool exposta tem `nome_tool`.
- `_executar(...)` — Invoca um subagente registrado com a tarefa (e contexto opcional).
- `_tool_delegacao(...)` — Cria a tool `delegar_<nome>` para o delegado do catálogo.
- `criar_subagente(nome, prompt, ferramentas, cfg, llm, arq_limite=..., profundidade=...)` — Compila um subagente (subgrafo stateless) com o loop cognitivo do núcleo.
- `configurar_subagentes(llm, cfg)` — Constrói e registra TODOS os delegados do catálogo (JSON → default).
- `_sincronizar_atributos(...)` — Expõe delegar_* como atributos do módulo (imports legados).
- `tools_delegacao()` — Todas as tools de delegação do catálogo (para o registro central).
- `_resposta_final(...)` — Extrai a última AIMessage com conteúdo (a resposta final do subagente).

**`aegis/tarefas.py`**

- **classe `TarefasStore`** — Lista de tarefas ordenada por prioridade (posição = prioridade).
  - `escrever(self, itens, merge)` — Substitui (ou faz merge na) da lista. Cada item: id, conteudo, status.
  - `listar(self)`
  - `ativas(self)` — Pendente/executando (para re-injeção pós compressão).
  - `formato_para_reinjecar(self)` — Bloco a anexar após uma compressão; vazio se não há ativas.
  - `limpar(self)`
  - `_atualizar_item(self, item_id, conteudo, status)` — Insere novo item ou atualiza o existente (reseta status se conteúdo mudou).
- `resumo_ativo_para_reinjecao()` — Export para o nó de compressão (os.py). Retorna '' quando não há ativas.
- `tarefas(tarefas)` **[@tool]** — Lista de tarefas do agenâte (planejamento e acompanhamento de progresso). Use para decompor uma tarefa complexa em passo

**`aegis/trajetoria.py`**

- **classe `Trajetoria`** — Registrador de trajetórias em JSONL, por dia de execução.
  - `registrar(self, thread_id, tipo, dados)` — Grava um registro JSONL com timestamp e thread de origem.
  - `registrar_mensagem_usuario(self, thread_id, conteudo)` — Registra a mensagem do usuário (usada pelo exportador ShareGPT/RL).
  - `hook(self, thread_id)` — Retorna um callable pronto para receber cada evento do stream.

**`aegis/tui.py`**

- **classe `TuiAegis`** — Interface terminal interativa (Textual) em estilo Hermes.
  - `compose(self)`
  - `on_mount(self)`
  - `chat(self)`
  - `painel(self)`
  - `status(self)`
  - `statusbar(self)`
  - … (+14 métodos)

**`aegis/uso.py`**

- `extrair_uso(resposta)` — Uso de uma resposta OpenAI-compat → {entrada, saida, reasoning}. Lê `response_metadata.token_usage` (prompt_tokens/compl
- `somar_uso(acumulado, novo)` — Reducer de soma por chave — `uso_tokens` acumula no estado (sessão).
- `total_tokens(uso)` — Entrada + saída + reasoning de uma contabilidade.
- `custo_estimado(uso, precos)` — Custo em R$ estimado pela tabela de preços (R$ por 1M de tokens).
- `verificar_orcamento(uso_turno, uso_sessao, orcamento_turno, orcamento_sessao, precos)` — Corte? Estouro de tokens OU reais (turno ou sessão) → detalhes do corte. Orçamento vazio/ausente = sem teto. Retorna Non

**`aegis/webui_bridge.py`**

- `_montar_app_async(cfg)` — Caminho da TUI: checkpointer async + store sync (threads compartilhadas).
- `montar_app(cfg)` — Compila o grafo uma única vez (processo persistente da ponte).
- `_redigir(arv, profundidade)` — Recursivamente redige chaves sensíveis e trunca strings longas.
- `_extrair_vereditos(saida)` — Vereditos do multiagente no estado final.
- `_processar_evento(evento, est)` — Converte 1 evento cru do astream_events v2 em 0..N frames (contrato). `est` são os acumuladores do job: acumulado_texto,
- `executar_job(app, texto, thread_id, job_id, cfg, dominio)` — Roda um turno e produz os frames do protocolo (token→…→fim/erro). `dominio` (opcional) força o subgrafo multiagente corr
- `snapshot_estado(cfg)`
- `listar_historico(app, limite)` — Threads do checkpointer (AsyncSqliteSaver — .alist no main thread). Nunca lança — [] em falha.
- `snapshot_sugestoes()` — Catálogo das sugestões do input da web UI (`/`, `@`). Uma única fonte de verdade: os registros reais do Aegis (slash da 
- `processar_comando(cmd, app)` — Processa um comando síncrono e devolve a linha JSON de resposta.
- `_emitir_job(app, cmd)` — Executa um turno e imprime todos os frames (com flush).
- `_rodar_job(app, cmd)` — Roda um turno como task independente (cancelável pelo comando `interromper`). O cancel emite `fim` com interrompido=True
- `_main_loop()` — Loop principal da ponte — UM event loop para montagem + todos os jobs (o AsyncSqliteSaver prende conexões/locks ao loop;
- `main()` — Ponto de entrada do processo (spawnado pelo Bun). Lê JSONL do stdin.

### Pasta `aegis/ferramentas/`

**`aegis/ferramentas/__init__.py`**

- `carregar_ferramentas(config_obj)` — Monta o registro completo de ferramentas: built-ins + habilidades (extensions/skills/) + plugins (extensions/plugins/).
- `recarregar_tudo(config_obj)` — Recarrega habilidades E plugins (auto-evolução em runtime).
- `ferramentas_atuais()` — Retorna o registro em cache (ou carrega uma vez).
- `avisos_carregamento()` — Avisos de plugins/skills com falha de carregamento.

**`aegis/ferramentas/basicas.py`**

- `_avaliar_ast(no)` — Avalia um nó da AST com segurança (whitelist de operações).
- `calculadora(expressao)` **[@tool]** — Avalia uma expressão aritmética com segurança (sem eval arbitrário). Suporta + - * / // % **, parênteses e funções matem
- `hora_atual(fuso)` **[@tool]** — Retorna a data e hora atuais em um fuso horário IANA (ex.: America/Sao_Paulo, UTC).
- `buscar_web(consulta, max_resultados)` **[@tool]** — Busca na web (DuckDuckGo; usa SearXNG se AEGIS_SEARXNG_URL estiver configurado). Retorna uma lista numerada de resultado
- `_executor_sandbox()` — Executor do backend configurado (`AEGIS_SANDBOX_BACKEND`).
- `_auditar_comando(resultado, comando)` — Registra a execução em `comandos.jsonl` (backend + comando + código). Mesmo arquivo/estilo da auditoria da tool `comando
- `comando_sandbox(comando, timeout)` **[@tool]** — Executa um comando shell em um sandbox isolado com timeout. Backend por `AEGIS_SANDBOX_BACKEND` (local | docker | ssh — 
- `ferramentas_basicas()`
- `consultar_grafo(termo)` **[@tool]** — Consulta o grafo de conhecimento dos aprendizados do projeto. Sem rede e sem LLM: navegação por relação por regras. `ter
- `estatisticas(escopo, formato)` **[@tool]** — Métricas de uso: tokens, custo estimado e ferramentas executadas. Sem rede. `escopo="sessao"` → contabilidade da thread 

**`aegis/ferramentas/pools.py`**

- `registrar_pool(nome, nomes)` — Registra (ou substitui) uma pool de ferramentas em runtime.
- `nomes_de_pool(pool)` — Nomes da pool (estendida se `pool` está entre as extras).
- `pool_da_lista(ferramentas, dominio)` — Filtra uma lista de ferramentas pela pool do domínio. `dominio=None` devolve a lista inteira (agente principal). Nomes q
- `nomes_das_ferramentas(ferramentas)` — Conjunto de nomes de uma lista de ferramentas (para validação).
- `integridade(nomes_reais)` — Valida as pools contra a lista real de ferramentas. Retorna os nomes órfãos (referenciados em pools mas que não existem)

**`aegis/ferramentas/relogio.py`**

- `relogio(fusos)` **[@tool]** — Mostra a data e hora atuais em um ou mais fusos horários IANA (separados por vírgula), como um relógio mundial. Args: fu

**`aegis/ferramentas/sistema.py`**

- `_permitidos(escrita)` — Diretórios raiz permitidos. - escrita: APENAS `config.artefatos_dir` (sandbox real — o agente não mexe no projeto; o usu
- `_resolver(caminho, escrita)` — Resolve o caminho (relativos contra a raiz do projeto) e valida o sandbox.
- `_diferenciar(antes, depois, caminho)` — Diff unified entre dois conteúdos (vazio se idênticos).
- `ler_arquivo(caminho, limite)` **[@tool]** — Lê um arquivo de texto (truncado). Use para inspecionar arquivos do projeto ou dos artefatos antes de editar. Args: cami
- `escrever_arquivo(caminho, conteudo)` **[@tool]** — Cria ou sobrescreve um arquivo (somente dentro do projeto ou de `config/dados/artefatos/`). Retorna o diff unified das m
- `editar_arquivo(caminho, trecho_antigo, trecho_novo)` **[@tool]** — Edita um arquivo substituindo UM trecho exato por outro (única ocorrência; se ambíguo, inclua mais contexto). Retorna o 
- `listar_arquivos(diretorio, limite)` **[@tool]** — Lista os arquivos de um diretório do projeto/artefatos (árvore rasa limitada). Use para descobrir a estrutura antes de l
- `_verificar_politica(comando)` — Retorna (permitido, motivo_de_recusa). Denylist sempre vence.
- `_env_limpo()` — Ambiente do subprocesso SEM segredos (chaves/tokens nunca vazam).
- `_registrar_comando(comando, confirmado, status, codigo, duracao_ms, motivo)` — Auditoria em config/dados/comandos.jsonl (gitignored).
- `executar_comando(comando, confirmar)` **[@tool]** — Executa um comando do sistema (shell, com pipes/redirects) — SEM sandbox: roda com o seu usuário. Política de segurança 
- `ferramentas_sistema()` — Lista de ferramentas do sistema (arquivo + comando).

**`aegis/ferramentas/trabalho.py`**

- `_thread_id()` — Thread ativa do processo (singleton `config` — mesmo padrão das tools `estatisticas`/`consultar_grafo`).
- `_estado_da_thread(thread_id)` — Lê o estado mais recente da thread no checkpointer (conexão própria).
- `pausar_trabalho(motivo)` **[@tool]** — Pausa o trabalho em andamento com um HANDOFF completo. Congela a fase atual do ciclo de entrega (G1): grava na memória d
- `retomar_trabalho()` **[@tool]** — Retoma o trabalho pausado: devolve o context completo do handoff. Lê o handoff gravado por `pausar_trabalho` na thread a
- `reverter_entrega(sha)` **[@tool]** — Reverte com segurança a última entrega (ou um commit específico). Executa `git revert --no-edit` no repositório do proje
- `_tool_por_nome(nome)` — Localiza a função da ferramenta pelo nome no registro conhecido.
- `replay_turno(limite)` **[@tool]** — Reproduz (forensics) o último turno passo a passo, SEM LLM. Re-executa os `registros_ferramentas` gravados no estado com
- `ferramentas_trabalho()` — Registro das tools de trabalho (G5).

### Pasta `aegis/gateways/`

**`aegis/gateways/__init__.py`**

- _(só docstring de pacote/módulo)_

**`aegis/gateways/webhook.py`**

- `processar_mensagem(app, thread_id, texto)` — Executa uma mensagem no grafo e devolve a resposta estruturada. É a ÚNICA função que o canal precisa conhecer — TUI, CLI
- **classe `HandlerWebhook`** — Handler HTTP com acesso ao grafo via atributo de classe `app`.
  - `do_POST(self)`
  - `do_GET(self)`
  - `log_message(self, format, *args)`
- `iniciar_servidor(app, host, porta)` — Inicia o servidor webhook com o grafo injetado no handler.

### Pasta `extensions/plugins/`

**`extensions/plugins/exemplo_plugin.py`**

- `contar_palavras(texto)` **[@tool]** — Conta o número de palavras e caracteres de um texto. Exemplo: "Olá mundo" -> 2 palavras, 9 caracteres.
- `reverter_texto(texto)` **[@tool]** — Inverte a ordem dos caracteres de um texto (ex.: 'abc' -> 'cba').
- `registrar()`

### Pasta `tests/`

**`tests/conftest.py`**

- **classe `ModeloFake`** — ChatModel determinístico — respostas scriptadas, sem rede. Util para testar o roteamento do grafo sem depender de API. R
  - `bind_tools(self, tools, **kwargs)`
  - `configurar(self, saidas)` — Define a sequência de respostas da conversa simulada.
- `chamada_tool(nome, args, id_chamada)` — Cria um AIMessage com tool_call para rotear para no_ferramentas.
- `basico_tools()`

**`tests/test_agendador.py`** — **9 testes**

- `test_agendar_cria_registro(tmp_path)`
- `test_vencidos_deterministico(tmp_path)`
- `test_cancelar(tmp_path)`
- `test_quando_relativo(tmp_path)`
- **classe `AppStub`** — Substituto do grafo via `executar_headless` (contrato `.invoke()`).
  - `invoke(self, entrada, config)`
- `test_executar_vencidos_conclui(tmp_path)`
- `test_recorrente_reagenda(tmp_path)`
- `test_erro_nao_derruba_lote(tmp_path)`
- `test_notificacao_webhook(tmp_path, monkeypatch)`
- `test_ferramentas_registradas()`

**`tests/test_aprendizados.py`** — **9 testes**

- `test_classificar_quatro_categorias()`
- `test_nome_arquivo_sessao_sanitiza()`
- `test_bloco_markdown_traz_categorias_e_prioridades()`
- `test_grafo_consulta_direta_e_relacionada(tmp_path)`
- `test_grafo_persiste_e_recarrega(tmp_path)`
- `test_reflexao_grava_arquivo_versionado_e_grafo(tmp_path)` — Critério de aceite: após vários turnos, docs/learnings/<sessao>.md tem as 4 categorias (acumuladas) e o grafo responde c
- `test_reflexao_sem_ferramentas_nao_cria_arquivo(tmp_path, monkeypatch)` — Regressão do C1: turno sem ferramentas → nenhum arquivo novo.
- `test_reflexao_com_lição_vazia_nao_grava_arquivo(tmp_path)` — LLM retorna sem lições → zero arquivo/grafo (zero custo).
- `test_tool_consultar_grafo(monkeypatch, tmp_path)`

**`tests/test_autorizacoes.py`** — **3 testes**

- `test_aprovar_e_verificar()`
- `test_aprovar_vazio_falha()`
- `test_aprovacao_e_exata()`

**`tests/test_backup.py`** — **3 testes**

- `_executar_backup(destino)` — Roda backup.sh em destino temporário e devolve o diretório criado.
- `test_backup_cria_diretorio_e_manifesto(tmp_path)`
- `test_backup_copia_arquivos_essenciais(tmp_path)`
- `test_backup_nao_vaza_arquivos_sensiveis(tmp_path)`

**`tests/test_camel_kit.py`** — **14 testes**

- `estado_tmp(tmp_path, monkeypatch)` — Aponta todos os arquivos de estado do kit para o tmp_path.
- `test_pensar_encadeia_em_numerado(estado_tmp)`
- `test_ver_pensamento_vazio(estado_tmp)`
- `test_planejar_tarefa_cria_e_formata(estado_tmp)`
- `test_planejar_com_numeracao(estado_tmp)`
- `test_planejar_sem_passos_erro(estado_tmp)`
- `test_atualizar_plano_status_ok(estado_tmp)`
- `test_atualizar_plano_status_invalido(estado_tmp)`
- `test_atualizar_plano_sem_plano(estado_tmp)`
- `test_atualizar_passo_desconhecido(estado_tmp)`
- `test_ver_plano_sem_plano(estado_tmp)`
- `test_anotar_e_ver_notas(estado_tmp)`
- `test_ver_notas_limitado(estado_tmp)`
- `test_ver_notas_vazio(estado_tmp)`
- `test_registro_do_toolkit_camel()`

**`tests/test_cientificas.py`** — **10 testes**

- `test_parsear_feed_atom()`
- `test_parser_xml_invalido()`
- `test_extrair_arxiv_id()`
- `test_gerar_bibtex_deterministico()`
- `test_citar_apa()`
- `test_citar_apa_multiplos_autores()`
- `test_salvar_na_biblioteca_dedupe(tmp_path, monkeypatch)`
- `test_biblioteca_arquivo_invalido(tmp_path, monkeypatch)`
- `test_buscar_papers_falha_offline(monkeypatch)` — Falha de rede → [] (o agente nunca cai por rede).
- `test_buscar_paper_por_id_offline(monkeypatch)`

**`tests/test_config_json.py`** — **7 testes**

- `test_merge_json_sobrescreve_padroes(tmp_path)`
- `test_fallback_quando_arquivo_ausente(tmp_path)`
- `test_fallback_quando_json_invalido(tmp_path)`
- `test_fallback_quando_raiz_nao_dict(tmp_path)`
- `test_padroes_nao_sao_mutados_entre_chamadas(tmp_path)`
- `test_modulos_leem_dos_json_de_config()` — Os hardcodes trocados refletem os valores dos arquivos de config.
- `test_modulos_aceitam_override_dos_json(tmp_path, monkeypatch)` — Sobrescrevendo o JSON (patch no singleton), os módulos mudam junto.

**`tests/test_contexto.py`** — **7 testes**

- `test_arquivo_inexistente_retorna_vazio(tmp_path)`
- `test_arquivo_vazio_retorna_vazio(tmp_path)`
- `test_le_conteudo(tmp_path)`
- `test_trunca_no_limite(tmp_path)`
- `test_sistema_injeta_contexto(monkeypatch)`
- `test_sistema_sem_contexto_nao_cria_secao(monkeypatch)`
- `test_contexto_do_projeto_usou_config(monkeypatch, tmp_path)`

**`tests/test_exportador.py`** — **4 testes**

- `_criar_trajetorias(tmp_path)` — Duas threads com tool pat + conversa (como grava o hook + main/TUI).
- `test_carregar_e_agrupar(tmp_path)`
- `test_exportar_sharegpt(tmp_path)`
- `test_exportar_openai(tmp_path)`
- `test_exportar_sem_trajetorias(tmp_path)`

**`tests/test_ferramentas.py`** — **14 testes**

- `test_calculadora_basica()`
- `test_calculadora_precedencia_e_funcoes()`
- `test_calculadora_constantes()`
- `test_calculadora_bloqueia_codigo_arbitrario()`
- `test_calculadora_erro_sintaxe()`
- `test_avaliar_ast_direto()`
- `test_hora_atual_fuso_valido()`
- `test_hora_atual_fuso_invalido()`
- `test_executar_comando_sucesso()`
- `test_executar_comando_falha()`
- `test_executar_comando_timeout()`
- `test_buscar_web_ddgs(monkeypatch)`
- `test_buscar_web_searxng(monkeypatch)`
- `test_buscar_web_sem_resultados(monkeypatch)`

**`tests/test_ferramentas_arquivo.py`** — **13 testes**

- `test_escrever_arquivo_cria_com_diff(tmp_path, monkeypatch)`
- `test_escrever_arquivo_sobrescreve_com_diff(tmp_path, monkeypatch)`
- `test_editar_arquivo_sucesso(tmp_path, monkeypatch)`
- `test_editar_arquivo_trecho_ausente_erro_controlado(tmp_path, monkeypatch)`
- `test_editar_arquivo_ambiguo_exige_contexto(tmp_path, monkeypatch)`
- `test_path_traversal_relativo_bloqueado(tmp_path, monkeypatch)`
- `test_path_absoluto_fora_bloqueado(tmp_path, monkeypatch)`
- `test_symlink_escape_bloqueado(tmp_path, monkeypatch)`
- `test_ler_arquivo_trunca(tmp_path, monkeypatch)`
- `test_ler_arquivo_projeto_permitido()`
- `test_listar_arquivos()`
- `test_listar_diretorio_inexistente()`
- `test_escrever_na_raiz_do_projeto_bloqueado(tmp_path, monkeypatch)` — O chat NÃO pode escrever na raiz do projeto — só nos artefatos.

**`tests/test_ferramentas_comando.py`** — **11 testes**

- `test_allowlist_leitura_roda_direto(tmp_path, monkeypatch)`
- `test_allowlist_git_status_sem_confirmar()`
- `test_denylist_recusa_sempre(tmp_path, monkeypatch)`
- `test_escrita_exige_confirmar(tmp_path, monkeypatch)`
- `test_git_escrita_exige_confirmar()`
- `test_env_limpo_sem_segredos(tmp_path, monkeypatch)`
- `test_timeout_respeitado(tmp_path, monkeypatch)`
- `test_saida_truncada(tmp_path, monkeypatch)`
- `test_auditoria_registra_recusa(tmp_path, monkeypatch)`
- `test_aprovado_pela_janela_de_perguntas_executa_sem_confirmar(tmp_path, monkeypatch)` — O comando aprovado pela web UI (autorizacoes) roda sem confirmar=True.
- `test_denylist_recusa_mesmo_aprovado(tmp_path, monkeypatch)` — Aprovação NÃO contorna a denylist — destrutivos continuam recusados.

**`tests/test_gateway.py`** — **4 testes**

- **classe `AplicacaoStub`** — Substituto do grafo compilado — apenas o contrato `.invoke()`.
  - `invoke(self, entrada, config)`
- `test_processar_mensagem_contrato()`
- `test_http_post_mensagem()`
- `test_http_healthz()`
- `test_http_erro_sem_mensagem()`

**`tests/test_grafo.py`** — **37 testes**

- `test_fluxo_ferramenta_sucesso(tmp_path)`
- `test_fluxo_auto_correcao(tmp_path)`
- `test_auto_correcao_respeita_limite(tmp_path)` — Com modelo sempre falhando, o loop para após max_tentativas.
- `test_checkpointer_retoma_conversa(tmp_path)`
- `test_compressao_trunca_historico(tmp_path)`
- **classe `ModeloComRaciocinioFake`** — Emula o DeepSeek/Zen no modo thinking: o `_generate` dispara o callback `on_chat_model_stream` com um chunk de reasoning
  - `bind_tools(self, tools, **kwargs)`
- `test_no_agente_devolve_reasoning_quando_ha_tool_calls()` — O provider exige o reasoning_content de volta quando a resposta tem tool_calls; o agregador do langchain o descarta — o 
- `test_no_agente_sem_tool_calls_nao_injeta_reasoning()` — Sem tool_calls o provider não exige o campo — e não deve vazar.
- **classe `ModeloEspiao`** — Fake que CAPTURA as mensagens recebidas (para inspecionar o system).
  - `bind_tools(self, tools, **kwargs)`
  - `chamadas(self)`
- `test_reflexao_pos_turno_grava_licoes(tmp_path)` — Turno com ferramentas → reflexão extrai e grava lições na Store.
- `test_reflexao_sem_ferramentas_nao_grava(tmp_path)` — Turno sem ferramentas → nenhuma lição (zero custo, nada gravado).
- `test_no_reflexao_pos_turno_marca_prioridade_alta_na_repeticao(tmp_path)` — A MESMA ferramenta falhando ≥2× no turno → lição com prioridade alta.
- `test_recuperar_licoes_por_relevancia(tmp_path)` — Recall: só lições relevantes à consulta voltam (ranqueamento IDF).
- `test_no_agente_injeta_licoes_relevantes_no_system(tmp_path)` — Lições da Store relevantes à pergunta entram no system do turno.
- `test_no_agente_sem_licoes_relevantes_nao_injeta_bloco(tmp_path)` — Pergunta sem relação → nenhum bloco de lições no system (sem ruído).
- `test_pergunta_simples_nao_gera_plano(tmp_path)` — Pergunta curta → fluxo legado: sem plano, sem chamada extra ao LLM.
- `test_tarefa_complexa_dispara_plano(tmp_path)` — Pergunta com múltiplos passos → heurística ativa (sem LLM).
- `test_plano_gerado_e_injetado_no_system(tmp_path)` — Tarefa complexa → plano no estado E bloco '## Plano ativo' no system.
- `test_plano_nao_chama_llm_em_pergunta_simples(tmp_path)` — Heurística negativa → nó de planejamento retorna sem invocar o LLM.
- `test_replanejamento_marca_passo_falho(tmp_path)` — LLM sem plano válido → fallback mantém o plano com o passo marcado falho.
- `test_replanejamento_reformula_com_llm(tmp_path)` — LLM devolve plano revisado → o passo que falhou sai; continuação fica.
- `test_turno_com_ferramenta_gera_evidencia(tmp_path)` — Turno com ferramenta → verificação anexa evidência e segue ao fim.
- `test_divergencia_dispara_correcao(tmp_path)` — Veredito divergente → agente corrige a resposta (uma única vez).
- `test_sem_ferramentas_nao_verifica(tmp_path)` — Turno sem ferramentas → verificação não chama LLM adicional.
- `test_modo_estrita_desligada_nao_verifica(tmp_path)` — verificacao_estrita=False → verificação inativa mesmo com ferramentas.
- `test_resumo_sessao_gravado_apos_intervalo(tmp_path)` — Turno com ≥ intervalo de mensagens → resumo e decisões na Store.
- `test_memoria_estrutural_ignora_turno_curto(tmp_path)` — Menos que o intervalo → zero chamadas de LLM.
- `test_recuperar_contexto_hierarquia(tmp_path)` — Recall hierárquico: perfil → lições → resumo → decisões, na ordem.
- `test_recuperar_contexto_tool_registrada()` — A tool recuperar_contexto existe e responde com o contexto da Store.
- `_resposta_revisao(itens)` — Veredito estruturado do revisor por pares (G3).
- `_executar_entrega_com_uat(app, cfg, pedido, respostas_uat, thread_id)` — Invoca uma entrega até o ship e responde o UAT (G2) pergunta a pergunta via Command(resume); retorna o resultado final.
- `test_entrega_ciclo_completo_ordem_fases(tmp_path)` — Pedido de entrega → fases na ordem discuss→plan→execute→verify→ship (invariante de ordem), com wave registrada e ship só
- `test_tarefa_informativa_fluxo_legado_byte_identico(tmp_path)` — Tarefa informativa → fluxo legado: fluxo_trabalho ausente, UMA chamada ao LLM, resposta byte-idêntica à do fluxo sem cla
- `test_verify_reprovado_volta_execute_sem_ship(tmp_path)` — verify reprova critério → volta a execute (feedback no histórico), NÃO ship; correção final → verify ok → ship.
- `test_discuss_vago_pausa_com_pergunta_e_resume(tmp_path)` — Pedido de entrega vago → no_discuss PAUSA com pergunta (interrupt); resposta do usuário (Command resume) → ciclo complet
- `test_revisao_bloqueante_volta_execute_e_corrige(tmp_path)` — Item bloqueante reprovado na revisão → volta a execute com o apontamento como feedback; após a correção, revisão aprovad
- `test_revisao_aprovada_vai_direto_ship_sem_perguntas(tmp_path)` — Tudo aprovado no checklist → ship direto (sem pergunta ao usuário até o UAT); o selo do ship cita os itens aprovados da 
- `test_revisao_auditoria_no_estado_e_registros(tmp_path)` — `revisao_entrega` persiste no estado final (auditoria replayável).
- `test_uat_aprova_criterios_um_a_um(tmp_path)` — Entrega com 2 critérios → 2 perguntas de UAT (uma por execução), respostas registradas com evidência e selo final 🧪.
- `test_uat_reprovado_vira_gap_e_proximo_turno_retoma(tmp_path)` — Critério reprovado → gap no estado; o próximo turno de entrega (OUTRA thread) carrega o gap como contexto do plano (pers
- `test_uat_persistido_entre_threads_sem_rede(tmp_path)` — UAT gravado na Store sobrevive a thread nova (novo app, mesmo banco): o segundo UAT mescla o histórico, cada resposta vi

**`tests/test_memoria_camel.py`** — **12 testes**

- `test_pontuacao_recencia_decai_com_o_tempo()` — Registro recente pontua mais que antigo (mesmo conteúdo).
- `test_pontuacao_meia_vida_configuravel()` — Com meia-vida pequena, um registro antigo perde quase toda a recência.
- `test_pontuacao_importancia_maior_vence()`
- `test_pontuacao_overlap_lexical()`
- `test_roundtrip_persistencia(tmp_path, monkeypatch)`
- `test_topk_ranqueia_pelo_mais_relevante(tmp_path, monkeypatch)`
- `test_topk_respeita_k()`
- `test_esquecer_registro(tmp_path, monkeypatch)`
- `test_esquecer_desconhecido_erro(tmp_path, monkeypatch)`
- `test_n_max_limitado(tmp_path)`
- `test_tokenizar_ignora_stopwords()`
- `test_registro_camel_registrada()`

**`tests/test_memoria_tool.py`** — **9 testes**

- `test_salvar_grava_na_store()`
- `test_salvar_sem_conteudo_rejeita()`
- `test_listar_mostra_fatos()`
- `test_esquecer_por_chave()`
- `test_esquecer_por_conteudo()`
- `test_perfil_funde_dict()`
- `test_listar_perfil()`
- `test_acao_invalida()`
- `test_sem_store_avisa()`

**`tests/test_modulos.py`** — **9 testes**

- `test_store_put_get(tmp_path)`
- `test_store_namespaces_isolados(tmp_path)`
- `test_carregar_skills_lê_skil_md(tmp_path)`
- `test_carregar_e_expor_cria_ferramentas(tmp_path)`
- `test_criar_skill_escreve_e_valida(tmp_path)`
- `test_carregar_plugins_exemplo()`
- `test_contar_e_reverter()`
- `test_recarregar_plugins()`
- `test_trajetoria_registra_jsonl(tmp_path)`
- `json_date()`

**`tests/test_multiagente.py`** — **13 testes**

- `test_classifica_dominio_por_regras()`
- `test_divisao_do_dominio_limita_especialistas()`
- `test_parsear_veredito_tolerante()`
- `test_merge_dict_combina_slots_de_escritas_paralelas()`
- `test_pools_integridade_contra_lista_real()` — ∪ POOLS ⊆ nomes das ferramentas registradas — nenhuma string órfã.
- `test_pool_da_lista_filtra_e_none_devolve_tudo()`
- `test_rota_apos_orquestrador(tmp_path)`
- `test_orquestrador_registra_auditoria(tmp_path)`
- `test_orquestrador_dominio_explicito_nos_metadados(tmp_path)` — `@escrita` na web UI força o subgrafo mesmo sem gatilho no texto.
- `test_orquestrador_pergunta_simples_nao_dispara(tmp_path)`
- `test_fluxo_multiagente_aprovado(tmp_path)` — Orquestrador → 3 especialistas paralelos → integrador → avaliador OK.
- `test_fluxo_multiagente_loop_reprovacao(tmp_path)` — Avaliador reprova → especialistas rodam de novo → aprova na 2ª.
- `test_fluxo_legado_intocado_quando_sem_dominio(tmp_path)` — Pergunta simples continua no fluxo de agente único (sem multiagente).

**`tests/test_obsidian.py`** — **17 testes**

- `vault(tmp_path, monkeypatch)` — Aponta o vault do config para um diretório temporário.
- `test_extrair_links()`
- `test_extrair_tags()`
- `test_criar_e_ler_nota(vault)`
- `test_criar_nota_duplicada_erro(vault)`
- `test_nota_em_subpasta(vault)`
- `test_ler_nota_inexistente(vault)`
- `test_ligar_nota_bidirecional(vault)`
- `test_ligar_para_inexistente_erro(vault)`
- `test_ligar_idempotente(vault)`
- `test_buscar_fulltext(vault)`
- `test_notas_por_tag(vault)`
- `test_notas_conectadas_vazio(vault)`
- `test_listar_vault_arvore(vault)`
- `test_limpar_exige_confirmacao(vault)`
- `test_limpar_vault_com_confirmacao(vault)`
- `test_indice_nunca_obsoleto(vault)` — Índice corrompido/antigo é recalculado na leitura.
- `test_registro_das_ferramentas_obsidian()`

**`tests/test_orcamento.py`** — **10 testes**

- **classe `_Resp`** — Resposta OpenAI-compat mínima para extrair_uso.
- `test_extrair_uso_completo()`
- `test_extrair_uso_sem_metadata()`
- `test_somar_uso_acumula_por_chave()`
- `test_custo_estimado_por_tabela()`
- `test_verificar_orcamento_turno_e_sessao()`
- `test_corte_por_orcamento_impede_tools(tmp_path)` — Resposta com tool_calls E usage alto → corte imediato: NENHUMA ferramenta executa (resumo parcial) e o estado registra o
- `test_corte_por_orcamento_da_sessao(tmp_path)` — Primeiro turno ok; o segundo acumula e estoura a sessão → corte.
- `test_contabilidade_soma_entre_turnos(tmp_path)` — Reducer de soma: uso de turnos consecutivos na MESMA thread acumula.
- `test_estatisticas_devolve_metricas_sem_rede(tmp_path, monkeypatch)`
- `test_ponte_emite_frame_orcamento()`

**`tests/test_papeis.py`** — **14 testes**

- `catalogo_tmp(tmp_path, monkeypatch)` — Aponta config.papeis_config_path para um arquivo temporário.
- `test_padrao_quando_sem_json(catalogo_tmp)` — Sem arquivo → os 4 papéis padrão.
- `test_override_e_extensao_pelo_json(catalogo_tmp)`
- `test_substituir_padrao_true(catalogo_tmp)`
- `test_resolver_papel_case_insensitive(catalogo_tmp)`
- `test_resolver_papel_desconhecido(catalogo_tmp)`
- `test_ferramenta_definir_e_ver_papel(catalogo_tmp, tmp_path, monkeypatch)`
- `test_listar_papeis(catalogo_tmp)`
- `test_especificar_tarefa_persistida(tmp_path, monkeypatch)`
- `test_estruturar_tarefa_heuristica()`
- `test_estruturar_tarefa_com_marcadores()`
- `test_montar_bloco_personalidade_sem_estado(tmp_path, monkeypatch)`
- `test_montar_bloco_personalidade_com_papel_e_tarefa(tmp_path, monkeypatch)`
- `test_injecao_no_sistema_contem_papel_ativado(tmp_path, monkeypatch)` — sistema() anexa o bloco de personalidade quando há papel/tarefa.
- `test_registro_das_ferramentas_de_papel()` — definir_papel/ver_papel/listar_papeis/especificar_tarefa registradas.

**`tests/test_prompts_avancados.py`**

- `dir_prompts(monkeypatch, tmp_path)`
- **classe `TesteSanitizador`**
  - `test_remove_comentarios_e_virgulas_pendentes(self)`
  - `test_preserva_url_com_slashes_e_hash_em_string(self)`
  - `test_virgula_pendente_dentro_de_string_preservada(self)`
  - `test_aceita_json_puro(self)`
- **classe `TesteCarga`**
  - `test_carrega_ficha_valida(self, dir_prompts)`
  - `test_ficha_quebrada_nao_derruba_catalogo(self, dir_prompts)`
  - `test_tipos_invalidos_viram_erro(self, dir_prompts)`
  - `test_diretorio_ausente_retorna_vazio(self, monkeypatch, tmp_path)`
- **classe `TesteCompilar`**
  - `test_bloco_contem_todo_o_conteudo(self, dir_prompts)`
  - `test_variaveis_extras_sobrepoem(self, dir_prompts)`
  - `test_id_inexistente_lanca_erro(self, dir_prompts)`
  - `test_interpolacao_tambem_nas_instrucoes(self, dir_prompts)`
- **classe `TesteAtivacao`**
  - `test_usar_prompt_ativa_e_compila(self, dir_prompts)`
  - `test_usar_prompt_inexistente_lanca(self, dir_prompts)`
  - `test_desativar_limpa(self, dir_prompts)`
  - `test_prompt_ativo_sem_catalogo_volta_vazio(self, dir_prompts, monkeypatch)`
- **classe `TesteListagem`**
  - `test_listar_mostra_ids_descricoes(self, dir_prompts)`
  - `test_listar_avisa_sobre_ficha_quebrada(self, dir_prompts)`
  - `test_ver_prompt_marca_ativo(self, dir_prompts)`
  - `test_ver_prompt_de_outro_nao_marca(self, dir_prompts)`
- **classe `TesteIntegracao`**
  - `test_sistema_inclui_prompt_ativo(self, dir_prompts)`
- **classe `TesteTools`**
  - `test_tools_registradas(self)`

**`tests/test_recuperacao.py`** — **4 testes**

- `test_pesquisa_recupera_do_store(tmp_path, monkeypatch)`
- `test_pesquisa_recupera_skill(tmp_path, monkeypatch)`
- `test_pesquisa_sem_store()`
- `test_pesquisa_consulta_vazia(tmp_path, monkeypatch)`

**`tests/test_relogio.py`** — **6 testes**

- `test_relogio_fuso_padrao()`
- `test_relogio_multiplos_fusos()`
- `test_relogio_ignora_espacos()`
- `test_relogio_fusos_vazios_usa_padrao()`
- `test_relogio_fuso_invalido()`
- `test_relogio_mistura_valido_e_invalido_falha_rapido()`

**`tests/test_sandbox_distribuido.py`** — **17 testes**

- `test_denylist_reconhece_perigos()`
- `test_docker_monta_comando_completo(monkeypatch)`
- `test_docker_denylist_bloqueia_sem_chamar_subprocess(monkeypatch)`
- `test_docker_timeout(monkeypatch)`
- `test_docker_sem_instalacao(monkeypatch)`
- `test_docker_nao_vaza_ambiente_do_host(monkeypatch)` — O container NUNCA recebe env do host: sem `-e`/`--env-file` no comando.
- `test_ssh_monta_comando_com_allowlist(monkeypatch)`
- `test_ssh_fora_da_allowlist_recusa(monkeypatch)`
- `test_ssh_sem_destino_configurado(monkeypatch)`
- `test_ssh_timeout(monkeypatch)`
- `test_ssh_nao_vaza_ambiente_do_host(monkeypatch)`
- `test_criar_executor_respeita_cfg(monkeypatch)`
- `test_comando_sandbox_backend_docker_audita(monkeypatch, tmp_path)`
- `test_comando_sandbox_local_audita_backend_local(monkeypatch, tmp_path)`
- `test_auditoria_do_comando_com_politica_ganha_backend(monkeypatch, tmp_path)` — A auditoria existente (tool `comando`) também carrega backend=local.
- `test_integracao_docker_real(monkeypatch, tmp_path)` — Critério de aceite: `echo` roda no container efêmero com artefatos montados.
- `test_comando_sandbox_denylist_por_docker(monkeypatch, tmp_path)` — Denylist vale pela ferramenta também (backend docker).

**`tests/test_seguranca.py`** — **12 testes**

- `test_classifica_injecoes_variadas(prefixo, instrucao, payload)` — Qualquer composição de instrução embutida é marcada como suspeita.
- `test_classifica_dados_limpos(texto)` — Dados normais nunca são marcados como suspeitos (zero falso positivo).
- `test_marcador_sempre_presente_e_aviso(instrucao)` — Leitura suspeita: marcador de classificação + aviso + _fonte.
- `test_marcador_sem_aviso_em_dado_limpo(texto)` — Leitura limpa: marcador de classificação presente, sem aviso ⚠️.
- `test_bloco_seguranca_no_prompt_de_sistema()` — O system prompt carrega o bloco permanente de segurança.
- `_montar(tmp_path, monkeypatch)` — Redireciona o sandbox de escrita (artefatos) para tmp_path — o `ler_arquivo` só permite projeto e artefatos.
- `test_ler_arquivo_marca_conteudo_suspeito(tmp_path, monkeypatch)`
- `test_ler_arquivo_marca_conteudo_limpo(tmp_path, monkeypatch)`
- `test_tools_externas_auditadas()` — As ferramentas de leitura externa estão na lista de auditoria.
- `test_agente_recusa_instrucao_embutida_e_audita(tmp_path, monkeypatch)` — Conteúdo com instrução embutida → agente recusa, nunca executa ação destrutiva; a leitura entra na auditoria com fonte_e
- `test_auditoria_ferramenta_interna_nao_externa(tmp_path)` — Ferramenta interna (calculadora) NÃO é marcada como fonte externa.
- `test_reflexao_grava_licao_de_seguranca(tmp_path, monkeypatch)` — Turno que leu conteúdo suspeito aprende a lição de segurança (C1) de forma determinística — independente do LLM da refle
- `test_corrida_injecoes_zero_execucao_destrutiva(instrucao)` — Critério de aceite C5: corrida de arquivos com injeções variadas → o aviso chega ao agente e ZERO ação destrutiva é exec

**`tests/test_sessoes.py`** — **8 testes**

- `_montar_trajetorias(tmp_path)` — Gera trajetórias com conversas conhecidas e retorna o diretório.
- `test_ler_trajetorias_monta_sessoes(tmp_path)`
- `test_descobrir_por_consulta(tmp_path)`
- `test_descobrir_vazio_quando_sem_match(tmp_path)`
- `test_determinismo_mesma_saida(tmp_path)`
- `test_rolar_janela(tmp_path)`
- `test_rolar_sessao_ausente(tmp_path)`
- `test_navegar_oculta_fonte_automatizada(tmp_path)`
- `test_ferramenta_descobrir_invoke(tmp_path)`

**`tests/test_sistema.py`** — **3 testes**

- `test_sistema_tem_regras_do_loop()`
- `test_sistema_mantem_identidade_e_ferramentas()`
- `test_sistema_inclui_metadados_quando_dados()`

**`tests/test_slash.py`** — **19 testes**

- `estado_tmp(tmp_path, monkeypatch)` — Aponta os arquivos de estado para o tmp_path (isolamento total).
- `test_parser_slash_basico()`
- `test_registro_tem_os_20_base()`
- `test_registro_cientifico_e_vault()`
- `test_executar_ajuda(estado_tmp)`
- `test_executar_desconhecido()`
- `test_executar_app_acoes()`
- `test_status_mostra_papeis_e_ferramentas(estado_tmp)`
- `test_config_mostra_caminhos(estado_tmp)`
- `test_papel_e_papeis(estado_tmp)`
- `test_planejar_grava_tarefa(estado_tmp)`
- `test_plano_marcar_pensar(estado_tmp)`
- `test_memoria_salvar_e_consultar(estado_tmp)`
- `test_esquecer_memoria(estado_tmp)`
- `test_ferramentas_lista(estado_tmp)`
- `test_criar_e_ler_nota_vault(estado_tmp)`
- `test_tag_no_vault(estado_tmp)`
- `test_obsidian_lista(estado_tmp)`
- `test_buscar_paper_rede_falha(estado_tmp, monkeypatch)` — Sem rede → mensagem amigável, sem crash do slash.
- `test_tui_intercepta_slash_sem_llm()` — enviar('/ajuda') responde localmente (nenhum produtor é chamado).

**`tests/test_subagentes.py`** — **7 testes**

- `test_pesquisador_usa_ferramenta_e_responde()`
- `test_redator_gera_texto_sem_ferramentas()`
- `test_delegar_redacao_invoca_subagente(monkeypatch)`
- `test_delegar_redacao_aceita_contexto(monkeypatch)`
- `test_delegar_sem_subagente_configurado(monkeypatch)`
- `test_configurar_subagentes_registra_ambos()`
- `test_erro_de_ferramenta_dispara_reflexao_no_subagente()`

**`tests/test_tarefas.py`** — **10 testes**

- `test_escrever_e_listar()`
- `test_merge_preserva_existentes()`
- `test_substituicao_sem_merge()`
- `test_status_invalido_vira_pendente()`
- `test_ativas_apenas_pendente_executando()`
- `test_reinjecao_vazia_sem_ativas()`
- `test_reinjecao_inclui_cabecalho()`
- `test_persistencia_em_arquivo(tmp_path)`
- `test_trunca_conteudo_longo()`
- `test_ferramenta_escreve_e_le()`

**`tests/test_trabalho_g5.py`** — **10 testes**

- `test_pausa_grava_handoff_e_retomada_continua_ciclo(tmp_path, monkeypatch)` — ENTREGAR vago → interrupt (fase discuss); pausa grava o handoff na Store; retomada com a resposta da pergunta continua d
- `test_pausa_sem_entrega_avisada(tmp_path, monkeypatch)`
- `test_retomar_sem_handoff_avisado(tmp_path, monkeypatch)`
- `_repo_git(tmp_path)` — Cria um repo git de teste com 3 commits (a → b → c) e retorna (repo, shas).
- `test_reverter_entrega_reverte_commit_especifico(tmp_path, monkeypatch)`
- `test_reverter_entrega_default_reverte_head(tmp_path, monkeypatch)`
- `test_reverter_entrega_sha_invalido_bloqueado(tmp_path, monkeypatch)`
- `test_replay_turno_deterministico(tmp_path, monkeypatch)` — Re-executa calculadora(2+2) com os MESMOS args — saída idêntica.
- `test_replay_turno_detecta_diferenca(tmp_path, monkeypatch)` — Estado com resultado forjado → o reprodutor aponta o DIFERENTE.
- `test_replay_turno_sem_registros(tmp_path, monkeypatch)`
- `test_registro_ferramentas_trabalho_tem_4_tools()`

**`tests/test_tui.py`** — **17 testes**

- **classe `CfgFake`**
- `_produtor_texto(texto, tools)` — Factory de produtor que emite frames de token (e opcionalmente tool).
- `test_turno_multiagente_frame_resposta_multi()` — Frame resposta_multi (multiagente) vira a resposta exibida na TUI.
- `test_compose_tem_widgets_essenciais()`
- `test_turno_streama_resposta()`
- `test_turno_renderiza_pergunta_e_resposta()`
- `test_turno_com_ferramenta_expõe_saida()`
- `test_comando_sair_nao_dispara_turno()`
- `test_pergunta_vazia_ignorada()`
- `test_painel_lateral_mostra_estado()`
- `test_statusbar_mostra_metricas_apos_turno()`
- `test_meta_do_turno_montada_no_chat()`
- `test_modo_raw_alterna_e_usa_static()`
- `test_modelo_alterado_via_slash()`
- `test_turno_registra_ferramenta_no_painel()`
- `test_bindings_teclado_limpar_e_novo()`
- `test_frame_erro_notifica_e_mostra_no_bloco()`
- `test_turno_captura_excecao_do_grafo()` — GraphRecursionError/limite de recursão não derruba mais o worker.
- `test_nova_sessao_troca_thread_id()`

**`tests/test_webui_bridge.py`** — **15 testes**

- `test_processar_evento_contrato_completo()` — O contrato evento v2 → frames (o runtime 1.x não streama invoke de modelos customizados — mesmo motivo dos testes da TUI
- `test_multiagente_subgrafos_e_vereditos()`
- `test_turno_simples_fim_e_metrica()` — Integração: o fake gera (não streama) — tokens só via contrato (acima), mas fim/metríca/job_id/estado_final vêm do fluxo
- `test_tool_sistema_frame_arquivo(tmp_path, monkeypatch)`
- `test_redigir_nunca_vaza_chave()`
- `test_snapshot_sem_segredos()`
- `test_processar_ping_e_desconhecido()`
- `test_processar_estado()`
- `test_processar_sugestoes_catalogo_real()`
- `test_processar_slash_status()`
- `test_processar_slash_desconhecido_nao_derruba()`
- `test_executar_job_dominio_explicito_dispara_subgrafo(tmp_path)` — `dominio` na mensagem vira metadado → orquestrador roteia p/ subgrafo.
- `test_historico_threads(tmp_path, monkeypatch)`
- **classe `AppQueCapturaConfig`** — App fake cujo astream_events registra o config recebido (e nada além).
  - `astream_events(self, entrada, config, version)`
- `test_executar_job_passa_recursion_limit_no_topo_do_config()` — O LangGraph lê `recursion_limit` no TOPO (default 25); dentro do `configurable` é ignorado e turnos longos morriam aos 2
- `test_linha_malformada_nao_derruba(monkeypatch, capsys)`

---

## 8. Como o projeto é desenvolvido (padrão de entrega)

Cada fase do núcleo termina com o mesmo ciclo:

1. **Implementação** no módulo correto (`aegis/…`), com testes determinísticos
   ANTES ou junto (TDD — `ModeloFake` do conftest, sem rede/LLM real);
2. **Suíte completa verde**: `pytest tests/` (hoje 379) + `pixi run webui-test`
   (hoje 25/0) — via `.pixi/envs/default/bin/python -m pytest`;
3. **Docs**: `docs/planejamento-nucleo.md` (status da fase) + `README.md`;
4. **Commit + push** com `git -c user.email=miguel9w@users.noreply.github.com`:
   mensagem descritiva no formato `fase: resumo`;
5. **Prova de runtime** (turno real documentado) quando o provider permite;
6. **Restart da Web UI** e healthcheck (`/api/healthz` → ponte ok).

> Regra de ouro do projeto (anti-alucinação): **toda etapa entrega PROVA
> verificável** (testes determinísticos com fakes/seeds). Nada de "o LLM faria
> X" — o grafo é testado com respostas controladas. Quando a prova real
> depende de fator externo (regressão do provider), o status é marcado
> concluído de forma honesta com a pendência documentada.