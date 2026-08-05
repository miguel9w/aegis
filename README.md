# 🛡️ Project Aegis — Agente Pessoal Autônomo

> Uma base modular e determinística para um **agente pessoal autônomo** construída sobre
> **LangGraph** (grafos de estado), totalmente **isolada e reproduzível com Pixi**
> (`prefix.dev`) e alimentada por **modelos abertos** (DeepSeek / OpenRouter / qualquer
> endpoint OpenAI-compatível com *Function Calling*).

O Aegis é uma arquitetura de **máquina de estados cíclica**: o modelo cognitivo decide,
invoca ferramentas, se auto-corrige em caso de erro, compacta conversas longas e persiste
tudo em SQLite — tudo com uma interface de terminal (TUI) em streaming baseada em `rich`.

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
- **TUI Rich em streaming** via `astream_events()`: Markdown em tempo real, spinners ("Pensando…"), painéis de parâmetros/retornos de ferramentas.
- **Sistema de habilidades auto-evolutivas** (`agentskills.io`): diretório `extensions/skills/` com `SKILL.md`; o agente pode **escrever novas habilidades e recarregá-las em runtime**.
- **Plugins Python recarregáveis** (`extensions/plugins/`) — módulos com `registrar()` adicionam ferramentas sem reiniciar.
- **Trajectory logging** (auditoria) + **exportador de datasets** ShareGPT/OpenAI (fine-tuning/RLHF).
- **RAG-lite `pesquisar_memoria`** — recupera fatos da Store de longo prazo e do `extensions/skills/` com ranqueamento IDF (sem dependência pesada), injetando contexto em novas sessões.
- **Subagentes avançados (agent-as-tool)** — o agente delega tarefas a subgrafos especialistas: `delegar_pesquisa` (pesquisador com busca web + cálculo + memória) e `delegar_redacao` (redator de texto longo), cada um com o mesmo loop de auto-correção do núcleo.
- **Gateway Webhook HTTP** (`pixi run gateway`) — expõe o mesmo grafo via REST (POST `/mensagem`, GET `/healthz`), pronto para bots/automação.
- **Cron interno (agendador)** — o agente agenda tarefas autônomas (`agendar`, `listar_agendamentos`, `cancelar_agendamento`); o daemon `pixi run agendador` executa os vencidos no grafo e notifica um webhook opcional (`AEGIS_AGENDADOR_CALLBACK_URL`).
- **Recall de sessões anteriores** — ferramenta `pesquisar_sessoes` (paridade Hermes `session_search_tool`): descobrir por palavra-chave, rolar uma janela de mensagens e navegar por sessões recentes, tudo sobre as trajetórias já gravadas, sem custo de LLM.
- **Lista de tarefas (todo)** — ferramenta `tarefas` (paridade Hermes `todo_tool`): decompõe tarefas complexas com status `pendente/executando/concluida/cancelada` e re-injeta as ativas após a compressão de contexto.
- **Rate limiting resiliente** — backoff exponencial + jitter + respeito a `Retry-After`.

---

## 🧱 Arquitetura do pacote

```
config/
├── env/                  # variáveis de ambiente (.env gitignored + .env.example)
└── dados/                # estado em runtime (gitignored)
    ├── memoria_agente.db     # checkpoints + store de longo prazo
    ├── trajetorias/          # auditoria JSONL (base p/ datasets)
    ├── datasets/             # exportações ShareGPT/OpenAI
    └── agendamentos.jsonl    # cron interno

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
└── tui.py             # Interface Rich baseada em streaming de eventos

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

## 🧪 Testes

```bash
pixi run pytest          # 74 testes (grafo, memória, ferramentas, skills, plugins, exportador, RAG, gateway, subagentes, cron, recall de sessões, tarefas)
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
- **Sandbox Docker/SSH** — plug into `sandbox.py` (hoje subprocesso isolado).
- **Bots Telegram/Discord/Slack** — próxima camada sobre o gateway: basta um dispatcher apontando para `processar_mensagem`.

---

## 📄 Licença

MIT — sinta-se livre para usar e modificar. Construído com ❤️ e LangGraph.