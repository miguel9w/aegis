# Aegis Web UI — Design de Engenharia (Bun + LangGraph streaming)

> Plano de construção da interface web do Aegis: **o mesmo grafo, agora num
> navegador**. Servidor **Bun** (dep no Pixi, `pixi run webui`) + **ponte Python**
> persistente que executa o LangGraph com streaming e fala **JSONL** com o Bun.
> Frontend **TypeScript vanilla** (zero deps npm — o Bun transpila e serve),
> painéis de instrumentação: stream de tokens, árvore viva do grafo/subgrafos,
> métricas por nó, vereditos do multiagente, auditoria e **modo wire** (frames
> crus do protocolo visíveis — para depurar o agente como engenheiro).

---

## 1. Objetivo e princípios

1. **Mesmo grafo, mesmo streaming da TUI** — nada de execução duplicada: a web
   UI consome o LangGraph igual à TUI (`montar_grafo` + `astream_events`), com
   todo o comportamento existente (multiagente F1/F2, subagentes, APF, memory,
   checkpoints SQLite por `thread_id`).
2. **Bun tem papel real** — é o servidor HTTP/SSE/WS, o compilador do front
   (TS vanilla → bundler nativo) e o orquestrador do ciclo de vida da ponte
   Python. `pixi add bun` (conda-forge, v1.3.11 — 17 MiB, verificado).
3. **"Mais técnica possível"** — a UI espelha o que um engenheiro quer ver:
   frames crus, latência por nó, tokens por nó, estado final serializado,
   auditoria JSONL ao vivo. Não é um chatbot bonito; é um painel do motor.
4. **Zero deps npm** — TypeScript compilado pelo próprio Bun (`bun build` /
   transpile no serve). React pode entrar depois (W6+ opcional): a API
   HTTP/SSE/WS é agnóstica de framework.
5. **Segurança por default** — bind `127.0.0.1`, credenciais NUNCA passam para
   o browser, sem CORS (same-origin: o Bun serve o front E as rotas de API).

## 2. Arquitetura

```
┌──────────────┐   SSE (EventSource)    ┌──────────────────────┐
│  Navegador    │──────────────────────▶│  Bun webui/server.ts │
│  (TS vanilla, │  POST /api/mensagem   │  :8788 (default)     │
│   painéis)    │──────────────────────▶│                      │
│              │  WS /api/hub           │  serve estático      │
│              │◀───────────────────────│  SSE por job_id      │
└──────────────┘                        │  WS multi-cliente    │
                                        └──────────┬───────────┘
                                                   │ spawn (1×)
                                                   │ stdin/stdout JSONL
                                        ┌──────────▼───────────┐
                                        │ aegis/webui_bridge.py│
                                        │ (python do Pixi)     │
                                        │ processa comandos    │
                                        │ e emite FRAMES       │
                                        └──────────┬───────────┘
                                                   │ montar_grafo(cfg)
                                        ┌──────────▼───────────┐
                                        │ LangGraph             │
                                        │ astream_events(v3)    │
                                        │ checkpointer SQLite   │
                                        │ (config/dados/)       │
                                        └──────────────────────┘
```

- **Uma ponte por processo** (persistente): grafo compilado UMA vez, histórico
  por `thread_id` no checkpointer. Multiplexação por `job_id` (uma mensagem =
  um job; fila FIFO no Bun; um job por vez, como a TUI).
- **Morte do bridge** (crash/restart): o Bun detecta `exit` e marcai os jobs
  ativos com frame `erro`; `pixi run webui` reinicia a ponte automaticamente
  (política: 3 tentativas com backoff, depois serve o front com banner de erro).
- **Portas**: `AEGIS_WEBUI_PORT` default `8788` (o gateway Python ocupa 8787).
  `AEGIS_WEBUI_HOST` default `127.0.0.1`.

## 3. Protocolo da ponte (JSONL sobre stdio)

A ponte é um processo persistente: **lê 1 comando por linha no stdin**, emite
**1 frame por linha no stdout** (sempre com `job_id`, exceto pong/estado/hist).

### 3.1 Comandos (stdin → ponte)

```jsonl
{"cmd":"mensagem","job_id":"j-abc","texto":"implemente um cli em python","thread_id":"web-1"}
{"cmd":"historico","limit":50}          // threads com checkpoints (id, qtd msgs, última)
{"cmd":"estado"}                        // snapshot seguro: versão, switches, limites, n_ferramentas
{"cmd":"ping"}
```

### 3.2 Frames (ponte → stdout)

```jsonl
{"job_id":"j-abc","kind":"token","texto":"def "}                       // deltas de texto (tag resposta)
{"job_id":"j-abc","kind":"tool_inicio","id":"r9","nome":"buscar_web","args":{...}}
{"job_id":"j-abc","kind":"tool_fim","id":"r9","nome":"buscar_web","saida":"... (truncado 2000)"}
{"job_id":"j-abc","kind":"subgrafo","nome":"programacao","evento":"start|end","nivel":1}
{"job_id":"j-abc","kind":"veredito","veredito":{"status":"aprovado","nota":8.5,"detalhe":"..."}}
{"job_id":"j-abc","kind":"estado","parcial":{...}}                     // snapshots stream.values (top-level)
{"job_id":"j-abc","kind":"fim","estado_final":{...}}                   // stream.output — estado COMPLETO
{"job_id":"j-abc","kind":"erro","tipo":"GraphRecursionError","mensagem":"..."}
{"job_id":"j-abc","kind":"metrica","tokens":1234,"duracao_s":9.2,"tps":134.1}  // ao fim: métricas do turno
{"cmd":"pong"}
{"cmd":"estado","dados":{...}}
{"cmd":"historico","threads":[...]}
// respostas de comandos usam {"cmd": ...} como "job_id"; jobs usam job_id real
```

- **`fim` carrega o estado final inteiro** (serializado `default=str`) — o
  front monta a árvore, os vereditos, o domínio e os rascunhos do multiagente
  SEM adivinhar nada.
- **`veredito`** é derivado do estado quando `orquestracao`/`vereditos` mudam
  (redundante com o estado final, mas permite renderizar o avaliador em tempo
  real durante o turno multiagente).
- **Tolerância**: ponte nunca aborta por uma linha malformada (log em
  `config/dados/webui_bridge.log` + continua); frame `erro` para exceções de
  execução do grafo (mesmo contrato do gateway).

## 4. API HTTP do Bun (`webui/server.ts`)

| Rota | Método | Função |
|---|---|---|
| `/` | GET | front (index.html + dist/app.js) |
| `/api/estado` | GET | snapshot seguro (mesmo payload do cmd `estado`) |
| `/api/mensagem` | POST | `{"texto","thread_id"}` → **202** `{"job_id"}`; enfileira na ponte |
| `/api/stream?job_id=...` | GET | **SSE** com os frames do job (até `fim`/`erro`) |
| `/api/historico?limit=50` | GET | lista de threads (checkpoints) |
| `/api/auditoria?linhas=50` | GET | tail de `config/dados/orquestracoes.jsonl` (multiagente) |
| `/api/healthz` | GET | `{"status":"ok","bun":"1.3.11","ponte":"ok|morto"}` |
| `/api/hub` | WS | broadcast de jobs ativos (multi-tab/multi-dispositivo) |

Contrato de erro: `{"erro": "..."}` com 400/404/409/500; `408` se o bridge
não responder `pong` em 5 s.

## 5. SSE — spec e cuidados reais

- Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`,
  `Connection: keep-alive`, `X-Accel-Buffering: no`.
- **`server.timeout(req, 0)`** — o Bun fecha conexões idle em 10 s por default;
  um SSE quieto conta como idle (lição da doc oficial do Bun).
- Formato por frame: `event: <kind>\ndata: <json>\n\n` — `EventSource` nativo
  do browser; `fim`/`erro` fecham com `event: fim` e o cliente dá `close()`.
- Reconnect: `retry: 2000` no início do stream; sem auto-replay de job (cliente
  pede novo stream com o mesmo `job_id` → Bun responde 404 se o job já saiu da
  fila de 100 últimas conclusões).
- Backpressure: o Bun não acumula frames de jobs sem cliente — se ninguém
  consumir `job_id` em 30 s, descarta (log).

## 6. Frontend — painéis (TS vanilla + CSS dark técnico)

Estrutura `webui/`:

```
webui/
├── server.ts            # Bun: serve, API, SSE, WS, ciclo de vida da ponte
├── bridge.ts            # spawn do python Pixi + parser JSONL + fila de jobs
├── src/
│   ├── app.ts           # bootstrap, routing de abas, estado global
│   ├── chat.ts          # stream Markdown (renderização leve própria), tool blobs
│   ├── wire.ts          # modo WIRE: cada frame cru com timestamp, filtro por kind
│   ├── arvore.ts        # árvore viva do grafo + subgrafos (fase/estado/nó ativo)
│   ├── metricas.ts      # tokens/tps/duração por turno e por nó; sparklines CSS
│   ├── auditoria.ts     # tail orquestracoes.jsonl + threads (historico)
│   └── config.ts        # leitura de /api/estado: switches, limites, versão
├── index.html           # layout: chat | direita (abas: Árvore/Métricas/Wire/Audit)
└── public/dist/app.js   # saída de bun build (gerado)
```

**Abas (direita do chat):**
1. **Árvore** — execução viva: nós do grafo (incl. `no_orquestrador`,
   `sub_<domínio>` expandível, slots dos especialistas) destacando o nó ativo
   por frame `subgrafo`; vereditos do avaliador com cor (aprovado/reprovado).
2. **Métricas** — por turno: tokens, duração, tps (frame `metrica`); por nó:
   contagem de chamadas de ferramenta (agrega frames `tool_*`); sparkline de
   tokens por turno (histórico em memória no cliente).
3. **Wire** — log cru de frames `{ts, kind, payload}` com filtros e pause;
   é o painel que prova que "o que a UI mostra é o que o agente emitiu".
4. **Auditoria** — tail de `orquestracoes.jsonl` + lista de threads (abrir uma
   thread = novo `thread_id` no chat).
5. **Config** — switches efetivos (`AEGIS_MULTIAGENTE`, subagentes, limites,
   modelo, versão) — **sem segredos** (a ponte redige chaves antes de servir).

**Chat**: igual à TUI — Markdown em tempo real, bloco de parâmetros/retornos de
ferramenta colapsável, rodapé com status "Pensando…"/tokens, `Enter` envia.
`Ctrl+Enter` = nova linha.

## 7. Integração com o LangGraph (ponte)

- `montar_grafo(llm, ferramentas, cfg)` — idêntico à TUI/gateway.
- **`stream_events(version="v3")`** (novo API do LangGraph 1.x — docs oficiais
  recomendam sobre v2): `stream.messages` (tokens), `stream.subgraphs`
  (nomes/níveis — árvore viva!), `stream.output` (estado final SEM o hack de
  `on_chain_end` da TUI). **Validação no W1**: se a versão instalada não tiver
  v3, ponte cai para `astream_events(version="v2")` com o mesmo produtor de
  frames da TUI (prova = teste rápido no W1, documented no commit).
- `cfg.thread_id` = o `thread_id` do comando; checkpoints por passo no SQLite
  existente → `historico` listado via `app.get_state` por thread.
- **Um job por vez** (FIFO) — mesma semântica da TUI (1 conversa); `409` para
  novo POST enquanto um job roda (front mostra "agente ocupado").

## 8. Pixi — deps e tasks

```toml
# [dependencies] — novo
bun = ">=1.3"          # runtime do servidor web + compilador do front (conda-forge)

# [tasks] — novo
webui        = "bun ./webui/server.ts"                          # dev: transpile no serve
webui-build  = "bun build ./webui/src/app.ts --outdir ./webui/public/dist"
webui-test   = "bun test ./webui/*.test.ts"
```

- `pixi run webui` exporta `PIXI_PROJECT_ROOT` → o server.ts localiza o Python
  em `$PIXI_PROJECT_ROOT/.pixi/envs/default/bin/python` e **spawna o módulo da
  ponte** (`python -m aegis.webui_bridge`) — sem re-`pixi run` (evita loop).
- Zero deps npm: TypeScript compilado pelo Bun (dev transplia `.ts` no serve;
  `webui-build` gera o bundle estático).

## 9. Segurança e limites

- Bind `127.0.0.1` (expor exige `AEGIS_WEBUI_HOST=0.0.0.0` explícito).
- **Nenhuma chave chega ao browser**: `/api/estado` e o frame `fim` passam por
  redator (`_redigir`) que substitui valores de env sensíveis por `[REDACTED]`.
- Sem CORS configurado (same-origin); `Origin` fora de localhost → 403 no
  POST/WS (defesa contra DNS rebinding).
- Truncamentos os mesmos da TUI (`limites.json`): tool `saida` 2000 chars,
  estado final limitado a `limite_resultado` 8000 por chave no frame `fim`.
- Rate limit simples por IP no Bun (token bucket, 30 req/min) para `/api/*`.
- A ponte roda com o mesmo usuário do Pixi; sem sandbox adicional (mesmo
  modelo da TUI/gateway — documentado como limite conhecido).

## 10. Testes (prova anti-alucinação — TDD obrigatório)

1. **pytest — `tests/test_webui_bridge.py`** (sem subprocess):
   - `executar_job(app, texto, thread_id)` com `ModeloFake`: emite frames
     `token` + `fim` com `estado_final` correto; multiagente (aprovado) emite
     `subgrafo` + `veredito` + `fim` com `dominio`/`vereditos`.
   - loop de reprovação: 2× veredito `reprovado` → frames `veredito` antes do
     `fim`.
   - `_redigir` nunca vaza `OPENAI_API_KEY`; `historico` devolve threads do
     checkpointer; comando malformado não derruba a ponte.
2. **`bun test` — `webui/server.test.ts`** (integração REAL):
   - `BRIDGE_CMD` injetável apontando p/ um **bridge fake** (`webui/fixtures/
     bridge_fake.mjs` que emite N frames JSONL e mantém o loop).
   - `GET /` → 200 HTML; `GET /api/healthz` → ponte status.
   - `POST /api/mensagem` → 202 `job_id`; `GET /api/stream?job_id=...`
     (fetch + reader) entrega `token`… `fim` na ordem; SSE com `server.timeout`
     ativo não fecha em idle (espera > timeout do fake).
   - job sem cliente é descartado; `408` quando o bridge fake não responde
     `pong`.
3. **Smoke manual** (final): `pixi run webui` + pergunta simples
   (`AEGIS_MULTIAGENTE=false`), depois uma de domínio (multiagente real) —
   árvore e vereditos visíveis. Evidência: screenshot.

## 11. Fases (cada fase = commit verde)

1. **W1 · Alvenaria** — `pixi add bun`; tasks `webui`/`webui-build`/
   `webui-test`; `server.ts` servindo `index.html` estático + `/api/healthz`;
   `bridge.ts` com spawn do python + `ping`; **validação do `stream_events`
   v3 vs v2** (teste rápido documentado). `bun test` do esqueleto. Commit.
2. **W2 · Ponte Python** — `aegis/webui_bridge.py`: protocolo JSONL (cmd
   mensagem/estado/historico/ping), frames token/tool/subgrafo/estado/fim/erro/
   metrica/veredito, `_redigir`, log de linha malformada. pytest do bridge
   (ModeloFake, multiagente, reprovação, segredos). Commit.
3. **W3 · Bun ↔ ponte** — fila FIFO de jobs, `POST /api/mensagem` → 202,
   `GET /api/stream` SSE com `server.timeout(req,0)`, descarte de job órfão,
   reinício automático da ponte. `bun test` com bridge fake (ordem dos frames,
   idle, 408). Commit.
4. **W4 · Chat + métricas + wire** — front: chat com stream Markdown e tool
   blobs, abas Métricas (tokens/tps/duração) e Wire (frames crus + filtros),
   rodapé de status. Teste manual + screenshot. Commit.
5. **W5 · Árvore + auditoria + config** — aba Árvore (nós/subgrafos/vereditos
   ao vivo), aba Auditoria (tail orquestracoes.jsonl + threads), aba Config
   (redigida). Commit.
6. **W6 · Polimento** — WS `/api/hub` multi-tab, histórico de threads
   navegável, rate limit, `bun build` de produção, README + `pixi run help`
   (linha webui), screenshot final no README. Commit.

## 12. Riscos e limites

- **`stream_events` v3**: API nova — se a versão instalada (1.2.10) não expuser
  `stream.output`/`stream.subgraphs`, a ponte usa v2 (produtor da TUI); o
  contrato de frames NÃO muda (camada de tradução isola).
- **Job único**: enquanto um turno roda, POSTs novos recebem `409` (mesma
  semântica da TUI). Multi-jobs paralelos exigiriam múltiplas compilações do
  grafo (X memoria) — fora de escopo, registrado para F3/west futuro.
- **Ponte morre**: Bun reinicia com backoff (3 tentativas); jobs ativos em
  crash perdem o streaming (o checkpointer preserva o histórico; reenviar a
  mensagem retoma com o estado salvo — comportamento igual à TUI pós-crash).
- **Front sem framework**: mais código manual de DOM; mitigado por estrutura
  pequena (`src/` 7 módulos) e o wire mode como oráculo de verdade. React
  (Vite+bun install) é caminho W6+ sem mudar a API.
- **Segurança**: expor em rede exige decisão consciente (`AEGIS_WEBUI_HOST`);
  sem auth — documentado como limite (mesmo do gateway atual).

## 13. Referências (pesquisa real desta leva)

- **Bun SSE — doc oficial**: SSE = `Response` com async generator +
  `Content-Type: text/event-stream`; **`server.timeout(req, 0)`** para streams
  quietos (idle 10 s default); `finally` no generator para cleanup de
  disconnect. https://bun.com/docs/guides/http/sse
- **LangGraph event streaming — docs oficiais**: `stream_events(version="v3")`
  com projeções tipadas `messages`/`values`/`subgraphs`/`output`/`interrupts`;
  `stream.output` entrega o estado final direto; múltiplos consumidores
  simultâneos. https://docs.langchain.com/oss/python/langgraph/event-streaming
- **langgraph-fullstack-python (repo de referência oficial)**: padrão
  POST cria o run → placeholder no chat → endpoint SSE puxa chunks; headers
  `text/event-stream` + `Cache-Control: no-cache` + `Connection: keep-alive`;
  evento `close` no fim do stream. https://deepwiki.com/langchain-ai/langgraph-fullstack-python/2.3-sse-streaming
- Infra: SearXNG local voltou a ser consultável na **:8081** (o backend de
  pesquisa do Hermes aponta para 8888 — nota operacional).

---
*Convenções respeitadas: pt-BR em código/comentários/README; TDD (testes antes
do commit verde); `pixi run` tasks; `.pixi/envs/default/bin/python -m pytest
--tb=short` sem `-q`; `bun test` para o servidor; git push exige
`miguel9w@users.noreply.github.com`.*