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
| Testes | **379 pytest** + **25 bun tests** (77 `expect()`) — suíte completa verde |
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
| **X1 — Catálogo de subagentes sob demanda** | além de `delegar_pesquisa`/`delegar_redacao`, delegados especializados com pool reduzido e auto-correção própria | `delegar_codigo`, `delegar_dados`, `delegar_revisao` (revisor dedicado do G3); catálogo `config/dados/delegados.json` com `arq_limite` (bloqueia cascata infinita); reuso de `fabrica_nos` com persona |
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
├── subagentes.py            # agent-as-tool
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
├── dados/                   # limites.json + runtime gitignored + datasets/
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
| `subagentes.py` | 5,5 KB | **Agent-as-tool**: `delegar_pesquisa`/`delegar_redacao` — subgrafos stateless com o mesmo loop cognitivo (agente→ferramentas→reflexão), prompt de persona e subconjunto de ferramentas. |
| `sandbox.py` | 10,7 KB | **C7.** `ExecutorLocal` (subprocess com timeout), `ExecutorDocker` (container efêmero `--rm`, rede isolada, denylist — docker-in-docker, podman/nerdctl-in-docker, `--privileged`, bomba fork `:\s*\(\s*\)\s*\{` — volume de artefatos em `/artefatos`, **nunca recebe env do host**), `ExecutorSSH` (allowlist de comandos, `BatchMode=yes`, `ConnectTimeout=10`) + fábrica por `AEGIS_SANDBOX_BACKEND`. |
| `seguranca.py` | 4,9 KB | **C5.** Helpers puros de anti-injeção: `classificar_conteudo` (detecta instrução embutida), marcadores de classe, `BLOCO_SEGURANCA` (bloco no prompt), `_catalogo_ferramentas` (nomes permitidos). Conteúdo externo é DADO. |
| `uso.py` | 3,9 KB | **C6.** `extrair_uso` (entrada/saída/reasoning de respostas OpenAI-compat), `estimar_custo` (tabela `precos_por_token` em `limites.json`), `verificar_orcamento` (turno/sessão em tokens ou R$). Sem imports de runtime. |
| `aprendizados.py` | 5,8 KB | **G4.** `classificar` (decisão/lição/padrão/surpresa), `GrafoConhecimento` (grafo.json com entidades/relações, navegação por ferramenta/fase/erro/categoria), `bloco_markdown`, `nome_arquivo_sessao`. |
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

## 7. Como o projeto é desenvolvido (padrão de entrega)

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