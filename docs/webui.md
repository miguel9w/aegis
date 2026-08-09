# Aegis Web UI — Design de Engenharia v2 (Bun + LangGraph streaming + UX agêntica)

> Interface web do Aegis: o mesmo grafo, agora num navegador — **bonita, fluida e
> carregada de detalhes técnicos**. Servidor **Bun** (dep no Pixi, `pixi run
> webui`) + **ponte Python persistente** que executa o LangGraph com streaming e
> fala **JSONL** com o Bun. Front **TypeScript** (Bun transpila/serve) com:
> chat com **pensamento do agente** (reasoning colapsável), **edição de arquivos
> com diff ao vivo**, cards de ferramenta com ciclo de vida animado, **subagentes
> (multiagente e delegações) como árvore viva**, métricas por nó, auditoria e
> **modo wire** (frames crus). Eventos alinhados ao padrão **AG-UI** (espírito,
> sem SDK).

---

## 1. Objetivo e princípios

1. **Mesmo grafo, mesmo streaming da TUI** — a web UI consome o LangGraph
   igual à TUI (`montar_grafo` + `stream_events`), preservando multiagente
   (F1/F2), subagentes agent-as-tool, APF, memória e checkpoints por `thread_id`.
2. **Bun tem papel real** — servidor HTTP/SSE/WS, compilador do front, gestor
   do ciclo de vida da ponte Python. `pixi add bun` (conda-forge, v1.3.11 —
   17 MiB, verificado com `pixi search`).
3. **Bonito e fluido SEM perder o técnico** — o visual é o de ferramenta de
   engenharia premium: dark de alto contraste, animações discretas de estado,
   cards de tool com transições (🕐→⚡→✓/✗), diffs coloridos, thinking em
   colapso animado. Cada pixel técnico continua visível (incl. frames crus).
4. **Visibilidade total do trabalho do agente** — o usuário vê: o que ele
   pensou (reasoning), o que chamou (tools com args/resultado/latência), o que
   editou (arquivos com diff), quem trabalhou (subagentes/subgrafos com estado).
5. **Zero deps npm no runtime do Aegis** — TypeScript compilado pelo Bun;
   render de diff e highlight próprios (~200 linhas, offline). Nenhuma lib de
   build no `pixi.toml` além do `bun`; `package.json` só se W6 optar por React.
6. **Segurança por default** — bind `127.0.0.1`, chaves NUNCA no browser,
   ferramentas de arquivo restritas a diretórios permitidos (sandbox).

## 2. Arquitetura (2 processos, 1 protocolo)

```
┌──────────────┐   SSE (EventSource + keepalive)   ┌──────────────────────┐
│  Navegador    │─────────────────────────────────▶│  Bun webui/server.ts │
│  TS vanilla   │  POST /api/mensagem → 202 job_id │  :8788 (default)     │
│  chat + feed  │─────────────────────────────────▶│                      │
│  ferramentas  │  WS /api/hub (multi-cliente)     │  serve estático      │
│  + painéis    │◀─────────────────────────────────│  SSE por job_id      │
└──────────────┘                                   │  keepalive : ping    │
                                                   └──────────┬───────────┘
                                                              │ spawn (1×, persistente)
                                                              │ JSONL stdio
                                                   ┌──────────▼───────────┐
                                                   │ aegis/webui_bridge.py│
                                                   │ comandos + FRAMES    │
                                                   └──────────┬───────────┘
                                                              │ montar_grafo(cfg)
                                                   ┌──────────▼───────────┐
                                                   │ LangGraph             │
                                                   │ stream_events(v3)     │
                                                   │ + tools de arquivo    │
                                                   │ + checkpointer SQLite │
                                                   └──────────────────────┘
```

- **Uma ponte por processo** (persistente): grafo compilado 1×; fila FIFO de
  jobs; 1 job por vez (mesma semântica da TUI). Crash → Bun reinicia com
  backoff (3 tentativas) e marca jobs ativos com frame `erro`.
- **Portas**: `AEGIS_WEBUI_PORT` default `8788`; `AEGIS_WEBUI_HOST` default
  `127.0.0.1` (gateway Python ocupa 8787).

## 3. Protocolo da ponte (JSONL sobre stdio)

Lê 1 comando/linha no stdin; emite 1 frame/linha no stdout. Frames alinhados
ao **AG-UI** (agent_message/agent_thinking/tool_call/file_edit/sub_agent) no
espírito — nomes próprios, SDL não usado.

### 3.1 Comandos (stdin → ponte)

```jsonl
{"cmd":"mensagem","job_id":"j-abc","texto":"...","thread_id":"web-1"}
{"cmd":"historico","limit":50}
{"cmd":"estado"}          // snapshot seguro: versão, switches, limites, n_ferramentas
{"cmd":"ping"}
```

### 3.2 Frames (ponte → stdout) — v2 (novo em negrito)

```jsonl
{"job_id":"j-abc","kind":"token","texto":"def ","cumulativo":"def main(): ..."}      // deltas + acumulado (recuperação pós-reconnect)
{"job_id":"j-abc","kind":"reasoning","texto":"Analisando...","cumulativo":"..."}     // deltas do PENSAMENTO (message.reasoning / reasoning_content)
{"job_id":"j-abc","kind":"tool_inicio","id":"r9","nome":"editar_arquivo","args":{...}}// tool chamada (pending→executando)
{"job_id":"j-abc","kind":"tool_fim","id":"r9","nome":"editar_arquivo","saida":"... (2000)", "duracao_ms":312}
{"job_id":"j-abc","kind":"arquivo","acao":"escrever|editar|apagar","caminho":"artefatos/novo.py","diff":"@@ -0,0 +1,3 @@\n+def main(): ...","status":"ok|erro"}
{"job_id":"j-abc","kind":"subgrafo","nome":"programacao","evento":"start|end","nivel":1,"tipo":"multiagente|delegacao"}
{"job_id":"j-abc","kind":"subagente","nome":"delegar_pesquisa","estado":"inicio|fim","resumo":"..."}  // agent-as-tool
{"job_id":"j-abc","kind":"comando","cmd":"git status","status":"ok|erro|recusado","duracao_ms":85,"resumo":"código=0","confirmado":true}
{"job_id":"j-abc","kind":"veredito","veredito":{"status":"aprovado","nota":8.5,"detalhe":"..."}}
{"job_id":"j-abc","kind":"estado","parcial":{...}}                    // snapshots stream.values (top-level)
{"job_id":"j-abc","kind":"fim","estado_final":{...}}                  // stream.output — estado COMPLETO (redigido)
{"job_id":"j-abc","kind":"metrica","tokens":1234,"duracao_s":9.2,"tps":134.1}
{"job_id":"j-abc","kind":"erro","tipo":"GraphRecursionError","mensagem":"..."}
{"cmd":"pong"}  /  {"cmd":"estado","dados":{...}}  /  {"cmd":"historico","threads":[...]}
```

- **`arquivo`** nasce da própria tool (ver §7): a ferramenta computa o diff
  (unified, `difflib`) e devolve no resultado → a ponte emite o frame com o
  mesmo diff → front renderiza colorido (linhas +/−, colapso de hunk). Frames
  `arquivo` também chegam na TUI como bloco de tool normal (bônus).
- **`reasoning`**: pensamento do modelo em tempo real. O proveedor DeepSeek
  emite `reasoning_content` (models de thinking) que o LangChain expõe como
  `additional_kwargs["reasoning_content"]` (v2) / `message.reasoning` (v3).
  **Não cai no `.content`** — por isso o SSE precisa de keepalive (§5).
- **`cumulativo`** em token/reasoning: o front reconstrói o texto mesmo se um
  frame se perder no reconnect (recuperação AG-UI-ish).

## 4. API HTTP do Bun

| Rota | Método | Função |
|---|---|---|
| `/` | GET | front (index.html + app.js) |
| `/api/estado` | GET | snapshot seguro (switch, limites, versão, ferramentas, dirs) |
| `/api/mensagem` | POST | `{"texto","thread_id"}` → **202** `{"job_id"}` |
| `/api/stream?job_id=...` | GET | **SSE** — frames até `fim`/`erro` |
| `/api/historico?limit=50` | GET | threads do checkpointer |
| `/api/auditoria?linhas=50` | GET | tail de `config/dados/orquestracoes.jsonl` |
| `/api/healthz` | GET | `{"status":"ok","bun":"1.3.11","ponte":"ok\|morto"}` |
| `/api/hub` | WS | broadcast de jobs ativos (multi-tab) |

Erros: `{"erro":...}` com 400/404/409 (job ativo)/408 (bridge sem `pong` em 5 s).

## 5. SSE — spec com as lições reais

- Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`,
  `Connection: keep-alive`, `X-Accel-Buffering: no`.
- **`server.timeout(req, 0)`** (Bun fecha idle em 10 s — lição da doc oficial).
- **Primeiro byte imediato**: ao conectar, o Bun emite `: open\n\n` logo —
  o browser vê HTTP 200 antes do grafo produzir (sem bodyless 504 em cold
  start — lição do vadim.blog/v3).
- **Keepalive durante reasoning silencioso**: o DeepSeek thinking fica mudo
  (reasoning não vai pro `.content`); sem tráfego, proxies/browsers podem
  derrubar o stream. O Bun injeta `: ping\n\n` a cada 15 s enquanto não há
  frames (lição combinada vadim.blog + agentic-forge).
- Formato: `event: <kind>\ndata: <json>\n\n`; `fim`/`erro` fecham o stream;
  `retry: 2000`.
- Jobs sem cliente por 30 s são descartados (log); replay do mesmo `job_id`
  após a fila de 100 conclusões → 404.

## 6. Front — layout e painéis (bonito E técnico)

**Layout 3 colunas, dark de alto contraste** (fundo `#0b0e14`, acentos
`#4f8cff`/`#3ddc97`, texto `#e6e9f0`, monospace IBM Plex Mono p/ código/wire):

```
┌───────────────┬──────────────────────────────┬──────────────────────┐
│ FEED ATIVIDADE │  CHAT (central)               │  PAINÉIS (abas)      │
│ . tool calls   │  mensagens + thinking         │  Árvore | Métricas   │
│ . edições de   │  (colapsável animado)         │  Wire | Auditoria    │
│   arquivo      │  + resposta em stream         │  Config              │
│ . subagentes   │  rodapé: Pensando… t/s tokens  │                      │
│ (cronológico,  │                              │                      │
│  ícones/estado)│  [ entrada ]  Ctrl+Enter env. │                      │
└───────────────┴──────────────────────────────┴──────────────────────┘
```

### 6.1 Chat
- Resposta em stream (Markdown próprio, leve), bloco **thinking** acima da
  resposta: colapsável com animação (▸ pensando…), mostra os deltas de
  `reasoning` em tempo real; borra nada (é o usuário, não produto).
- Multiagente: quando um turno vai para especialistas, o chat mostra o fluxo
  orquestrador → 3 especialistas → integrador → **veredito** (badge
  aprovado ✓ verde / reprovado ✗ âmbar) → resposta consolidada.

### 6.2 Feed de atividade (esquerda — cronológico vivo)
Cada evento do agente vira um **card** com ícone, nome e estado animado:
- **Tool card**: nome, status (🕐 pending → ⚡ executando → ✓ ok / ✗ erro),
  args colapsáveis, resultado com **latência ms** (estilo agentic-forge).
- **Arquivo card**: caminho, ação (escrever/editar/apagar), **diff colorido**
  embutido (linhas + verde / − vermelho, colapso de hunk, render próprio —
  bônus W6: highlight de sintaxe).
- **Subagente card**: `delegar_pesquisa` etc. — expande em sub-feed (o que o
  delegado chamou), encerra com resumo (frame `subagente`).
- Mudanças de estado animadas (200 ms, respeitando `prefers-reduced-motion`).

### 6.3 Painéis (direita, abas)
1. **Árvore** — execução viva: nós do grafo + subgrafos (`sub_<domínio>`
   expandível, slots dos especialistas), nó ativo destacado pelo frame
   `subgrafo`; vereditos com cor.
2. **Métricas** — por turno: tokens, duração, tps (frame `metrica`); por nó:
   chamadas de tool; sparklines CSS de tokens por turno (histórico do cliente).
3. **Wire** — frames CRUS (ts, kind, payload) com filtros por kind e pause;
   o oráculo de verdade ("o que a UI mostra é o que o agente emitiu").
4. **Auditoria** — tail de `orquestracoes.jsonl` + threads navegáveis.
5. **Config** — switches efetivos, limites, modelo, versão — **redigido**.

## 7. Ferramentas do sistema — arquivo (sandbox) E comandos (política)

> Descoberta desta leva: o Aegis **não tem** ferramentas de arquivo reais
> (`escrever_arquivo` só existe como referência de papel; inventário de 47
> tools não inclui nenhuma de arquivo). E **não tem** execução de comandos.
> O usuário pediu: edição de arquivos na UI **e** comandos do sistema **sem
> sandbox**, mas com **segurança de comando** (outra ferramenta).

### 7.1 Arquivos — `escrever_arquivo` / `editar_arquivo` / `ler_arquivo` / `listar_arquivos`

- Cria/sobrescreve/edita/ler/lista; **diff unified** (difflib) no retorno de
  escrita/edição; erro de trecho ausente vira retorno de tool (não exceção).
- **Sandbox de caminho** (anti path-traversal): resolve e exige prefixo em
  `AEGIS_ARTEFATOS_DIR` (default `config/dados/artefatos/`, criado) ou no
  diretório do projeto; fora → erro controlado.
- Testes de isolamento: `../`, absoluto fora, symlink escape → bloqueados.

### 7.2 Comandos — `executar_comando` (sem sandbox, com política)

Roda comandos do sistema **sem sandbox** (qualquer cwd, com o usuário real,
pipes/redirect — `shell=True` com `shlex`), mas com **segurança de comando**
declarativa — nova ferramenta, separada das de arquivo:

1. **Allowlist de leitura** — comandos de leitura rodam direto: `ls, cat, head,
   tail, grep, find, pwd, git status/log/diff/show, df, free, ps, uname, which`.
2. **Denylist absoluta** — recusados SEMPRE, com erro didático: `rm -rf /`,
   `mkfs, dd, shutdown, reboot, halt, kill -9, chmod -R 777 /, curl|sh, :(){:|:&};:`,
   redirecionamento para `/dev/sd*`, `> /etc/*` etc.
3. **Escrita exige `confirmar: true`** — qualquer comando fora da allowlist
   (instalar pkgs, git commit/push, criar pasta, mover, apagar arquivo
   específico…) exige o argumento `confirmar: true`; sem ele → erro "use
   confirmar: true para comandos de escrita". **Toda execução vai para
   `config/dados/comandos.jsonl`** (auditoria: cmd, sha256, status, duração,
   confirmado) — visível na web UI (feed + aba Auditoria).
4. **Contenção de execução**: timeout (`AEGIS_EXEC_TIMEOUT`, default 120 s),
   saída truncada em `limite_resultado` (8000), **env LIMPO para o subprocesso**
   (sem `OPENAI_API_KEY`/segredos — o comando roda com PATH básico + cwd),
   sem stdin (não-interativo).
5. **`cwd`**: default = raiz do projeto; `AEGIS_EXEC_CWD` customiza.
6. **HITL real** (aprovação humana via interrupt AG-UI) registrado como
   evolução W6+ — no v1, "confirmar + auditoria + feed" é a segurança.
   Frame `comando` no protocolo (§3.2) alimenta o card de terminal do feed.

## 8. Integração com o LangGraph (ponte)

- `montar_grafo(llm, ferramentas, cfg)` — idêntico à TUI/gateway, agora com as
  tools de arquivo no ToolNode.
- **`stream_events(version="v3")`** — projeções tipadas: `stream.messages`
  (tokens + **reasoning** + tool-call chunks), `stream.subgraphs` (subgrafos
  aninhados — multiagente E delegações), `stream.output` (estado final sem o
  hack de `on_chain_end` da TUI). **W1 valida v3 na versão instalada** (1.2.10);
  fallback: `astream_events(version="v2")` com o mesmo produtor da TUI —
  o contrato de frames não muda (camada de tradução isola).
- Reasonning no v2: `additional_kwargs["reasoning_content"]` (DeepSeek);
  no v3: `message.reasoning`. A ponte emite `reasoning` quando o chunk tem
  conteúdo de pensamento.
- `cfg.thread_id` do comando; checkpoints SQLite existentes; `historico` via
  `app.get_state` por thread.

## 9. Pixi — deps e tasks

```toml
# [dependencies] — novo
bun = ">=1.3"          # servidor web + compilador TS (conda-forge, verificado)

# [tasks] — novo
webui        = "bun ./webui/server.ts"
webui-build  = "bun build ./webui/src/app.ts --outdir ./webui/public/dist"
webui-test   = "bun test ./webui/*.test.ts"
```

- `pixi run webui` exporta `PIXI_PROJECT_ROOT` → `server.ts` spawna
  `$PIXI_PROJECT_ROOT/.pixi/envs/default/bin/python -m aegis.webui_bridge`
  (sem re-`pixi run`, evita loop). Dev: Bun transplia `.ts` no serve;
  `webui-build` gera bundle estático de produção.

## 10. Segurança e limites

- Bind `127.0.0.1`; expor exige `AEGIS_WEBUI_HOST=0.0.0.0` explícito.
- **Chaves nunca chegam ao browser**: `/api/estado` e `estado_final` passam
  por `_redigir` (env sensíveis → `[REDACTED]`).
- Tools de arquivo restritas ao sandbox (`AEGIS_ARTEFATOS_DIR` + projeto).
- Sem CORS (same-origin); `Origin` fora de localhost → 403 (DNS rebinding).
- Rate limit token bucket (30 req/min/IP) nas rotas `/api/*`.
- Truncamentos iguais à TUI (`limites.json`): tool `saida` 2000 chars, diff
  8000, estado final 8000/chave.

## 11. Testes (prova anti-alucinação — TDD)

1. **pytest — ferramentas do sistema** (`tests/test_ferramentas_arquivo.py` +
   `tests/test_ferramentas_comando.py`): arquivo — escrever retorna diff
   correto; editar com trecho ausente → erro controlado; path traversal (../,
   absoluto, symlink) bloqueado; sandbox dir criado. comando — allowlist roda
   direto; **denylist recusa (rm -rf /, mkfs, shutdown…) com erro didático**;
   escrita sem `confirmar: true` → erro; com `confirmar` → roda + linha em
   `config/dados/comandos.jsonl`; **env do subprocesso sem `OPENAI_API_KEY`**;
   timeout respeitado; saída truncada.
2. **pytest — ponte** (`tests/test_webui_bridge.py`, sem subprocess):
   `executar_job` com `ModeloFake` → frames `token`+`fim` com estado final;
   multiagente aprova/reprova → `subgrafo`+`veredito` na ordem; `reasoning`
   emitido quando o fake injeta `reasoning_content`; `_redigir` não vaza
   `OPENAI_API_KEY`; linha malformada não derruba a ponte.
3. **`bun test` — `webui/server.test.ts`** (integração real): `BRIDGE_CMD`
   injetável → bridge fake (`webui/fixtures/bridge_fake.mjs`) que emite frames
   com intervalo; `GET /` → 200; `POST /api/mensagem` → 202; `GET
   /api/stream` entrega `token`…`fim` na ordem **e mantém vivo com `: ping`
   durante pausa do fake** (prova o keepalive); 408 sem `pong`; job órfão
   descartado.
4. **Smoke manual**: `pixi run webui` + pergunta que EDITE um arquivo no
   sandbox (diff visível no feed) + pergunta multiagente (árvore + veredito) —
   screenshots para o README.

## 12. Fases (cada fase = commit verde)

1. **W1 · Alvenaria + validação v3** — `pixi add bun`; tasks; `server.ts`
   servindo `index.html` + `/api/healthz`; spawn da ponte + `ping`;
   **validação `stream_events` v3 (reasoning/subgraphs/output)** documentada.
   `bun test` esqueleto. Commit.
2. **W2 · Ferramentas do sistema** — `escrever_arquivo`/`editar_arquivo`/
   `ler_arquivo`/`listar_arquivos` (sandbox + diff) E `executar_comando`
   (política: allowlist/denylist/confirmar + auditoria `comandos.jsonl` + env
   limpo); testes de isolamento e política; registradas no ToolNode (TUI ganha
   o diff e o card de comando como bônus). Commit.
3. **W3 · Ponte Python** — `aegis/webui_bridge.py`: protocolo JSONL v2 (frames
   reasoning/arquivo/subagente/veredito/metriica/token cumulativo), `_redigir`,
   log de linha malformada. pytest da ponte (ModeloFake + multiagente +
   reasoning). Commit.
4. **W4 · Bun ↔ ponte (SSE)** — fila FIFO, `POST`→202, `GET /api/stream` com
   `server.timeout(req,0)`, **`: open` + `: ping` 15 s**, descarte de órfãos,
   reinício automático. `bun test` com bridge fake (ordem, keepalive, 408).
   Commit.
5. **W5 · Front — chat + feed + wire** — layout 3 colunas (dark), chat com
   thinking colapsável e stream, feed de atividade (tool cards animados +
   arquivo cards com diff colorido + subagente), aba Wire, métricas básicas.
   Screenshot. Commit.
6. **W6 · Árvore + auditoria + polimento** — aba Árvore (subgrafos/vereditos),
   Auditoria (orquestracoes.jsonl + threads), Config redigida, WS `/api/hub`,
   rate limit, `webui-build` produção, README + `pixi run help` (linha webui),
   highlight de sintaxe (bônus). Commit.

## 13. Riscos e limites

- **`stream_events` v3**: se a versão instalada não expuser v3, ponte usa v2
  (produtor da TUI); contrato de frames não muda.
- **Reasoning**: `deepseek-chat` (não-thinking) não emite `reasoning_content`
  — o bloco aparece vazio até um modelo thinking (`deepseek-reasoner` etc.).
  O feed de atividade + árvore cobrem a "visualização de pensamento" mesmo
  sem reasoning.
- **Job único**: POST durante turno ativo → 409 (igual TUI); multi-jobs
  paralelos exigem múltiplas compilações do grafo (fora de escopo, registrado).
- **Render de diff próprio**: sem highlight de sintaxe no W5 (bônus W6);
  colapso de hunk e cores +/− cobrem a maioria dos casos.
- **Ponte morre**: Bun reinicia (backoff 3×); jobs ativos perdem o streaming;
  checkpointer preserva o histórico (reenviar retoma — igual TUI pós-crash).
- **Zero auth**: expor em rede é decisão consciente; sem users/sessões.

## 14. Referências (pesquisa real desta leva — SearXNG :8081 + extração)

- **AG-UI** (protocolo aberto de interação agente↔front, Microsoft/CopilotKit):
  eventos tipados para streaming chat, thinking steps, backend tool rendering
  com side effects como eventos de 1ª classe, sub-agents with tracing.
  Inspiração de nomes/campos dos frames (sem dependência de SDK).
  https://docs.ag-ui.com/introduction
- **LangGraph v3 — vadim.blog**: projeções tipadas (`run.messages`,
  `run.subgraphs`, content-block com start/delta/finish; **reasoning separado
  do texto**); lições do v2 na prática: keepalive `: ping` durante reasoning
  silencioso do DeepSeek e primeiro byte `: open` (evita bodyless 504).
  https://vadim.blog/langgraph-v3-event-streaming-typed-projections
- **Agentic Forge — streaming de tool calls**: eventos `token` (com
  `cumulative`), `thinking` (com `cumulative`), `tool_call`
  (pending→executing→complete), `tool_result` (com `latency_ms`), `complete`
  (usage), `ping` (heartbeat 30 s); UI de tool cards com estados animados e
  painel lateral de tools ativas. Padrão seguido no feed de atividade.
  https://agentic-forge.github.io/blog/streaming-tool-calls.html
- **diff2html**: render de diff linha-a-linha/side-by-side + syntax highlight;
  inspiração visual — aqui implementado próprio (offline, zero deps).
  https://diff2html.xyz/
- **Bun SSE — doc oficial**: `Response` + async generator +
  `text/event-stream`; **`server.timeout(req, 0)`** (idle 10 s); `finally` no
  generator para cleanup. https://bun.com/docs/guides/http/sse
- **LangGraph event streaming — docs oficiais**: v3 com projeções tipadas;
  `stream.output` (estado final), `stream.subgraphs` (nomes+path aninhados).
  https://docs.langchain.com/oss/python/langgraph/event-streaming
- **langgraph-fullstack-python** (referência oficial de UI): POST cria o run →
  placeholder → SSE puxa chunks; headers `text/event-stream` +
  `Cache-Control: no-cache` + `Connection: keep-alive`; evento de fechamento.
  https://deepwiki.com/langchain-ai/langgraph-fullstack-python/2.3-sse-streaming
- Infra: SearXNG local consultável em **:8081** (o backend de pesquisa do
  Hermes aponta 8888 — nota operacional).

---
*Convenções respeitadas: pt-BR em código/comentários/README; TDD (testes antes
do commit verde); `pixi run` tasks; `.pixi/envs/default/bin/python -m pytest
--tb=short` sem `-q`; `bun test` para o servidor; git push exige
`miguel9w@users.noreply.github.com`.*