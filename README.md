# 🛡️ Project Aegis — Agente Pessoal Autônomo

> Uma base modular e determinística para um **agente pessoal autônomo** construída sobre
> **LangGraph** (grafos de estado), totalmente **isolada e reproduzível com Pixi**
> (`prefix.dev`) e alimentada por **modelos abertos** (DeepSeek / OpenRouter / qualquer
> endpoint OpenAI-compatível com *Function Calling*).

O Aegis é uma arquitetura de **máquina de estados cíclica**: o modelo cognitivo decide,
invoca ferramentas, se auto-corrige em caso de erro, compacta conversas longas e persiste
tudo em SQLite — tudo com uma interface de terminal (TUI) moderna em streaming baseada em `Textual`.

![Aegis](https://img.shields.io/badge/langgraph-1.x-1c3c3c?logo=langchain) ![Pixi](https://img.shields.io/badge/pixi-reproducible-%23b22222) ![Python](https://img.shields.io/badge/python-3.11-blue)

---

## ✨ Recursos

- **Motor cognitivo agnóstico** — `ChatOpenAI` configurado por `config/env/.env`
  (`OPENAI_API_BASE`, `OPENAI_API_KEY`, `MODEL_NAME`). Compatível com DeepSeek,
  OpenRouter, NVIDIA NIM e endpoints OpenAI-compatíveis.
- **Grafo de estado cíclico (LangGraph)** com 4 nós:
  - `no_agente` — injeta o prompt de sistema, invoca a LLM, decide entre responder ou chamar ferramentas (`tool_calls`).
  - `no_ferramentas` — `ToolNode` que executa ferramentas registradas dinamicamente.
  - `no_reflexao_auto_correcao` — analisa o erro da ferramenta e reformula a chamada, fechando o loop *Agente → Ferramenta → (se erro) → Reflexão → Agente*.
  - `no_compressao_contexto` — resume histórico antigo para controlar tokens em conversas longas.
- **Auto-correção resiliente**: se uma ferramenta falha, o agente analisa o erro e reformula, até `AEGIS_MAX_TENTATIVAS_CORRECAO` vezes.
- **Memória de longo prazo** (LangGraph `Store`): perfil, preferências e fatos persistidos entre sessões.
- **Checkpoints por passo** (`SqliteSaver` em `config/dados/memoria_agente.db`) — retomada de conversas e multi-tópicos via `thread_id`.
- **TUI Textual em streaming** via `astream_events()`: Markdown em tempo real, status ("Pensando…"/rodapé de tokens), painéis de parâmetros/retornos de ferramentas, entrada multiwidget.
- **Sistema de habilidades auto-evolutivas** (`agentskills.io`): diretório `extensions/skills/` com `SKILL.md`; o agente pode **escrever novas habilidades e recarregá-las em runtime**.
- **Plugins Python recarregáveis** (`extensions/plugins/`) — módulos com `registrar()` adicionam ferramentas sem reiniciar.
- **Trajectory logging** (auditoria) + **exportador de datasets** ShareGPT/OpenAI (fine-tuning/RLHF).
- **RAG-lite `pesquisar_memoria`** — recupera fatos da Store de longo prazo e do `extensions/skills/` com ranqueamento IDF (sem dependência pesada), injetando contexto em novas sessões.
- **Subagentes avançados (agent-as-tool)** — o agente delega tarefas a subgrafos especialistas: `delegar_pesquisa` (pesquisador com busca web + cálculo + memória) e `delegar_redacao` (redator de texto longo), cada um com o mesmo loop de auto-correção do núcleo.
- **Gateway Webhook HTTP** (`pixi run gateway`) — expõe o mesmo grafo via REST (POST `/mensagem`, GET `/healthz`), pronto para bots/automação.
- **Cron interno (agendador)** — o agente agenda tarefas autônomas (`agendar`, `listar_agendamentos`, `cancelar_agendamento`); o daemon `pixi run agendador` executa os vencidos no grafo e notifica um webhook opcional (`AEGIS_AGENDADOR_CALLBACK_URL`).
- **Recall de sessões anteriores** — ferramenta `pesquisar_sessoes` (paridade Hermes `session_search_tool`): descobrir por palavra-chave, rolar uma janela de mensagens e navegar por sessões recentes, tudo sobre as trajetórias já gravadas, sem custo de LLM.
- **Lista de tarefas (todo)** — ferramenta `tarefas` (paridade Hermes `todo_tool`): decompõe tarefas complexas com status `pendente/executando/concluida/cancelada` e re-injeta as ativas após a compressão de contexto.
- **Memória explícita e curada** — ferramenta `gerenciar_memoria` (paridade Hermes `memory_tool`): `salvar`/`esquecer`/`listar` fatos duráveis na mesma Store que o recall lê (notas do agente ou perfil do usuário), tornando o que o agente grava imediatamente recuperável.
- **Contexto do projeto (`AGENTS.md`)** — o prompt de sistema anexa automaticamente as regras e convenções do repositório lidas de `AGENTS.md` (paridade Hermes), truncadas a 4000 chars e configuráveis por `AEGIS_CONTEXTO`.
- **Rate limiting resiliente** — backoff exponencial + jitter + respeito a `Retry-After`.
- **Papéis (role-playing CAMEL)** — ferramentas `definir_papel`/`ver_papel`/`listar_papeis` trocam a persona (assistente, pesquisador, redator, planejador — configuráveis em `config/dados/papeis.json`); o papel ativo e a **tarefa especificada** (`especificar_tarefa`/`estruturar_tarefa`) são injetados no prompt de sistema.
- **Memória pontuada estilo CAMEL** — `registrar_memoria_camel`/`consultar_memoria_camel`/`esquecer_memoria_camel`: registros com importância 0–10, pontuação heurística (recência × importância × overlap lexical) e recuperação top-k.
- **Toolkits CAMEL** — `pensar`/`ver_pensamento` (thinking), `planejar_tarefa`/`atualizar_plano`/`ver_plano` (task-planning) e `anotar`/`ver_notas` (note-taking).
- **Configuração por JSON** — 5 arquivos em `config/dados/` (`limites.json`, `tarefas_config.json`, `agendador_config.json`, `papeis.json`, `memoria_camel_config.json`) externalizam hardcodes (limites de contexto/resultado, busy_timeout, frequências, personas) com fallback seguro.
- **29 slash commands (`/`)** — dispatcher local na TUI e `main.py --comando "/..."`: `/ajuda, /status, /papeis, /definir_papel, /planejar, /plano, /marcar, /notas, /memoria, /salvar_memoria, /esquecer, /marcar, /criar_nota, /buscar_nota, /tag, /buscar_paper, /bibtex, /revisar, /obsidian, /prompt, /prompts…`
- **Features científicas** — busca na API do **arXiv** (`buscar_papers_arxiv`, `revisar_literatura`), **BibTeX** e citação **APA** determinísticos (`gerar_citacao_bibtex`) e biblioteca local em `config/dados/biblioteca.json` (`salvar_paper`).
- **Banco estilo Obsidian** — `aegis/obsidian.py`: vault de notas `.md` (subpastas, tags `#tag`, `[[wikilinks]]` bidirecionais com backlinks e grafo em `indice.json`); ferramentas `criar_nota`/`ler_nota`/`ligar_nota`/`buscar_notas`/`notas_por_tag`/`notas_conectadas`/`listar_obsidian`/`limpar_obsidian`.
- **Comandos de terminal** — `pixi run papeis | memoria | plano | notas | papers | obsidian | prompts` para operar papéis, memória, planos, notas, arXiv, vault e prompts avançados direto do shell.
- **Formato de Prompt Avançado (APF)** — fichas `.apf` (JSON5-lite: JSON + comentários `//`/`#` + vírgulas pendentes + variáveis `${chave}`) que compõem um bloco injetável no prompt de sistema: sistema, instruções, restrições, formato de saída e exemplos. Ative com `/prompt <id>` (persistido) e veja exemplos auto-documentados em `config/prompts_avancados/`.

---

## 🧱 Arquitetura do pacote

```
config/
├── env/                  # variáveis de ambiente (.env gitignored + .env.example)
├── dados/                # estado em runtime + config JSON versionado
│   ├── memoria_agente.db     # checkpoints + store de longo prazo
│   ├── trajetorias/          # auditoria JSONL (base p/ datasets)
│   ├── datasets/             # exportações ShareGPT/OpenAI
│   ├── agendamentos.jsonl    # cron interno
│   ├── obsidian/             # vault de notas markdown (wikilinks)
│   ├── biblioteca.json       # papers salvos (arXiv)
│   └── *.json                # configuração JSON (limites, tarefas, papeis…)
└── prompts_avancados/     # fichas de prompt avançado (.apf — versionadas)

extensions/
├── skills/               # habilidades agentskills.io (SKILL.md auto-evolutivos)
└── plugins/             # plugins Python recarregáveis (contar_palavras, reverter_texto)

aegis/
├── config.py          # Configuração central (config/env/.env → singleton tipado)
├── estado.py          # EstadoAegis (TypedDict + reducers)
├── llm.py             # Provedor cognitivo (ChatOpenAI + retry resiliente)
├── grafo.py           # Monta e compila o grafo (nós + arestas condicionais)
├── nos.py             # Implementação dos 4 nós de execução
├── ferramentas/       # Ferramentas nacionais (busca web, calculadora, sandbox)
├── sandbox.py         # Execução de comandos isolada (subprocesso limitado)
├── skills.py          # Habilidades (leitura/escrita/recarga de extensions/skills)
├── plugins.py         # Carregador dinâmico de plugins (extensions/plugins)
├── memoria.py         # SqliteSaver (checkpoints) + Store (longo prazo)
├── trajetoria.py      # Registro de ações (JSONL) para auditoria/MLOps
└── tui.py             # Interface Textual baseada em streaming de eventos

main.py                # CLI: TUI streaming, headless, --listar-*
```

---

## 🚀 Começando

### 1. Pré-requisitos

- [Pixi](https://prefix.dev) instalado (`curl -fsSL https://pixi.sh/install.sh | bash`).

### 2. Configurar credenciais

```bash
cp config/env/.env.example config/env/.env
# preencha OPENAI_API_BASE / OPENAI_API_KEY / MODEL_NAME
#   Ex.: DeepSeek:   https://api.deepseek.com/v1        + deepseek-chat
#        OpenRouter: https://openrouter.ai/api/v1        + openrouter/auto
#        Zen (gratuito): https://opencode.ai/zen/v1      + deepseek-v4-flash-free
```

### 3. Executar

```bash
pixi run start "Quem descobriu o Brasil?"   # modo headless (resposta única)
pixi run start                              # TUI interativa (streaming)
pixi run dev                                # igual ao start, com modo verboso (dev)
pixi run start --listar-ferramentas         # lista as ferramentas registradas
pixi run start --listar-skills              # lista as habilidades carregadas
pixi run start --exportar-sharegpt          # config/dados/trajetorias → dataset ShareGPT
pixi run start --exportar-openai            # config/dados/trajetorias → dataset OpenAI/RL
pixi run start --gateway                    # serve o grafo via Webhook HTTP (:8787)
pixi run test --co   # (se configurado) roda a suíte de testes
```

Exemplo em TUI:

```
Você: CALCULE 8 * 8 com a ferramenta calculadora
╭────────────────────────── → resultado: calculadora ─────────────────────────╮
│ 8 * 8 = 64                                                                  │
╰──────────────────────────────────────────────────────────────────────────────╯
╭─────────────────────────────────── Aegis ────────────────────────────────────╮
│ 8 * 8 = 64.                                                                │
╰──────────────────────────────────────────────────────────────────────────────╯
⏱ 5.35s  ·  ~6 tokens  ·  thread smoke
```

---

## ⚙️ Variáveis de ambiente (`config/env/.env`)

| Variável | Padrão | Descrição |
|---|---|---|
| `OPENAI_API_BASE` | `https://api.deepseek.com/v1` | Endpoint OpenAI-compatível |
| `OPENAI_API_KEY` | *(vazio)* | **Obrigatória** — chave da API |
| `MODEL_NAME` | `deepseek-chat` | Modelo ativo |
| `AEGIS_THREAD_ID` | `default` | Tópico/conversa atual |
| `AEGIS_DB` | `config/dados/memoria_agente.db` | Banco de checkpoints SQLite |
| `AEGIS_LIMIAR_COMPRESSAO` | `20` | Mensagens antes de compactar |
| `AEGIS_MANTER_APOS_COMPRESSAO` | `8` | Mensagens mantidas após compactar |
| `AEGIS_MAX_TENTATIVAS_CORRECAO` | `3` | Limite do loop de auto-correção |
| `AEGIS_MEMORIA_ATIVA` | `true` | Liga/desliga Store de longo prazo |
| `AEGIS_SKILLS_DIR` | `extensions/skills` | Pasta de habilidades auto-evolutivas |
| `AEGIS_TRAJETORIA` | `false` | Habilita trajectory logging |
| `AEGIS_TRAJETORIA_DIR` | `config/dados/trajetorias` | Onde o JSONL é gravado |
| `AEGIS_GATEWAY_PORT` | `8787` | Porta do gateway Webhook (`pixi run gateway`) |
| `AEGIS_SUBAGENTES` | `true` | Habilita subagentes pesquisador/redator |
| `AEGIS_AGENDAMENTOS` | `config/dados/agendamentos.jsonl` | Arquivo de persistência do cron interno |
| `AEGIS_TAREFAS` | `config/dados/tarefas.json` | Persistência da lista de tarefas (todo) |
| `AEGIS_CONTEXTO` | `AGENTS.md` | Arquivo de contexto do projeto injetado no prompt |
| `AEGIS_AGENDADOR_INTERVALO` | `60` | Segundos entre execuções do daemon `agendador` |
| `AEGIS_AGENDADOR_CALLBACK_URL` | *(vazio)* | Webhook notificado a cada conclusão de agendamento |
| `AEGIS_SEARXNG_URL` | — | URL do SearXNG (alternativa à busca DDG) |
| `AEGIS_TEMPERATURA` | `0.7` | Temperatura do modelo |

> `OPENAI_*` seguem o padrão da OpenAI (`OpenAI` / `base_url`), permitindo trocar de
> provedor mudando apenas o `.env` — sem tocar em nenhum código.

---

## 🧠 Como funciona o grafo

```
                    ┌───────────────┐
         START ───▶ │  no_agente    │  (LLM decide)
                    └──────┬────────┘
                           │ tool_calls?
                    ┌──────┴────────┐
                    │  no_ferramentas │  (ToolNode)
                    └──────┬────────┘
                    erro?  │           sucesso?
              ┌────────────┴─┐
              ▼              ▼
   ┌────────────────┐   ┌────────────┐
   │ no_reflexao_   │   │  fim (END) │
   │ auto_correcao  │   └────────────┘
   └────────┬───────┘
            │ (reformula e volta ao agente / ferramentas)
            ▼
     no_compressao_contexto ── quando a conversa fica longa
```

- **Aresta condicional** por presença de `tool_calls`.
- **Auto-correção**: `Agente → Ferramenta → (erro) → Reflexão → Agente`.
- A cada **superstep** um checkpoint é gravado (`SqliteSaver`); a memória de longo
  prazo (perfil do usuário) é gravada ao final de cada turno (`Store`).

---

## 🧩 Subagentes avançados (agent-as-tool)

O Aegis delega tarefas especializadas a **subgrafos LangGraph** que reusam o
loop cognitivo do núcleo (agente → ferramentas → reflexão), com persona própria
e um subconjunto de ferramentas:

| Subagente | Ferramentas | Uso |
|---|---|---|
| `pesquisador` | `buscar_web`, `calculadora`, `pesquisar_memoria` | pesquisa com fontes, síntese com evidências |
| `redator` | — (escrita pura) | textos longos e estruturados em pt-BR |

O agente principal decide quando delegar (via `delegar_pesquisa` /
`delegar_redacao`); cada delegação é registrada no painel de ferramentas da TUI
e na trajetória. Subagentes são stateless — o resultado volta ao grafo principal.

## 🧠 Formato de Prompt Avançado (APF)

Arquivos `.apf` (em `config/prompts_avancados/`) são **JSON5-lite**: todo JSON
válido, mais comentários de linha (`//` e `#`), vírgulas pendentes e variáveis
`${chave}` interpoladas do bloco `variaveis`. Cada ficha compõe um bloco que é
**injetado por último no prompt de sistema** quando ativo (persistido em
`config/dados/prompt_ativo.json`).

```apf
{
  "id": "revisor-codigo",        // obrigatório — usado em /prompt <id>
  "versao": "1.0.0",             // opcional (default 1.0.0)
  "descricao": "Revisão de código com foco em bugs.",
  "sistema": "Você é um revisor sênior. Contexto: ${contexto}",
  "instrucoes": ["Aponte bugs com trecho exato.", "Sugira a correção mínima."],
  "variaveis": { "contexto": "avalie o impacto em produção" },
  "restricoes": ["Nunca invente APIs."],
  "formato_saida": { "tipo": "markdown", "secoes": ["Problemas", "Sugestões"] },
  "exemplos": [{ "entrada": "x = y + 1", "saida": "correto" }]
}
```

Uso:

- `pixi run prompts` — lista as fichas válidas (e avisa fichas quebradas);
- `/prompt <id>` / `/prompt nenhum` — ativa/desativa (TUI ou `--comando`);
- `main.py --prompts` — lista via CLI;
- tools do agente: `usar_prompt_avancado`, `ver_prompt_avancado`, `listar_prompts_avancados`.

Exemplos completos e auto-documentados: `config/prompts_avancados/revisor-codigo.apf`
e `config/prompts_avancados/pesquisa-profunda.apf`.

## 🧪 Testes

```bash
pixi run pytest          # 96 testes (grafo, memória/recall, memória explícita, contexto do projeto, TUI Textual, ferramentas, skills, plugins, exportador, RAG, gateway, subagentes, cron, recall de sessões, tarefas)
```

Cobertura: roteamento do grafo, fluxo de auto-correção (incl. limite), retomada de
threads, calculadora segura (bloqueio de `import`/`__import__`), hora com `zoneinfo`,
busca web (mock), sandbox, Store de longo prazo, carga/recriação de skills e recarga de plugins.

---

## 🗺️ Roteiro de paridade com Hermes (extensões)

O desacoplamento já permite (sem code change estrutural):

- ✔️ **Exportador de trajetórias ShareGPT/RL** — `pixi run start --exportar-sharegpt|--exportar-openai` consome `config/dados/trajetorias/*.jsonl` e gera datasets em `config/dados/datasets/`.
- ✔️ **RAG-lite sobre a memória** — ferramenta `pesquisar_memoria` (IDF) sobre a Store + `extensions/skills/`.
- ✔️ **Gateway Webhook HTTP** — `pixi run gateway` expõe o grafo via REST (base para bots).
- ✔️ **Background workers / cron** — `pixi run agendador` (daemon) executa vencidos de `config/dados/agendamentos.jsonl` com callback webhook.
- ✔️ **Estrutura organizada (v0.5.0)** — `config/` (env + dados/estado em subpastas) e `extensions/` (skills + plugins em subpastas); tudo configurável por `AEGIS_*`.
- ✔️ **Recall de sessões + todo (v0.6.0, paridade Hermes)** — `pesquisar_sessoes` (descobrir/rolar/navegar sobre as trajetórias) e `tarefas` (lista com re-injeção pós compressão).
- ✔️ **Memória explícita + contexto do projeto (v0.7.0, paridade Hermes)** — `gerenciar_memoria` (salvar/esquecer/listar na Store) e injeção de `AGENTS.md` no prompt.
- ✔️ **TUI Textual (v0.8.0)** — migração da interface Rich (loop `Live`+`Prompt`) para um `App` do Textual (`Header`/`VerticalScroll`/`Input`/`Footer`), mantendo o streaming por `astream_events` num worker; produtor de eventos injetável para testes headless.
- **Sandbox Docker/SSH** — plug into `sandbox.py` (hoje subprocesso isolado).
- **Bots Telegram/Discord/Slack** — próxima camada sobre o gateway: basta um dispatcher apontando para `processar_mensagem`.

---

## 📄 Licença

MIT — sinta-se livre para usar e modificar. Construído com ❤️ e LangGraph.