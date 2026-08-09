/**
 * Aegis Web UI — front (TS vanilla, bundlado pelo Bun).
 * Consome o SSE do server.ts e pinta: chat streaming, thinking,
 * feed de atividade (tools/arquivo/comando/subgrafo/veredito),
 * painel técnico (métricas, wire, config, histórico).
 */
import { renderDiff } from "./diff.ts";
import { renderMarkdown } from "./markdown.ts";

type Frame = { job_id?: string; kind: string; [k: string]: unknown };

// ---------------------------------------------------------------- refs DOM
const byId = <T extends HTMLElement>(id: string) => document.getElementById(id) as T;

const chatEl = byId<HTMLDivElement>("chat");
const feedEl = byId<HTMLDivElement>("feed");
const wireEl = byId<HTMLDivElement>("wire-lista");
const inputEl = byId<HTMLTextAreaElement>("entrada");
const enviarBtn = byId<HTMLButtonElement>("enviar");
const threadBadgeEl = byId<HTMLSpanElement>("thread-badge");
const statusEl = byId<HTMLSpanElement>("status");
const tabBotoes = document.querySelectorAll<HTMLButtonElement>("[data-aba]");
const canvasSpark = byId<HTMLCanvasElement>("spark");
const sparkCtx = canvasSpark.getContext("2d")!;

// ------------------------------------------------------------------- estado
const estado = {
  threadId: "default",
  jobAtivo: false,
  torneio: 0 as number,
  turnoAtual: null as null | {
    resp: HTMLDivElement; thinking: HTMLDivElement; thinkingWrap: HTMLDivElement;
    metrica: HTMLDivElement; acumulado: string;
  },
  tokens: 0,
  metricas: [] as Array<[number, number, number]>, // [tokens, duração, tps]
  textosTurno: [] as string[],
};

// ------------------------------------------------------------------- utilitários
const tempoAgo = (ms: number) =>
  ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`;

function escaparJson(qualquer: unknown, limite = 400): string {
  const s = JSON.stringify(qualquer) ?? String(qualquer);
  return s.length > limite ? s.slice(0, limite) + "…" : s;
}

// ------------------------------------------------------------------- chat
function bolha(classes: string, conteudo: string): HTMLDivElement {
  const d = document.createElement("div");
  d.className = `bolha ${classes}`;
  d.innerHTML = conteudo;
  return d;
}

function adicionarUsuario(texto: string) {
  const d = bolha("usuario", `<div class="avatar">U</div><div class="corpo"></div>`);
  d.querySelector(".corpo")!.textContent = texto;
  chatEl.appendChild(d);
}

function abrirTurnoAegis() {
  const wrap = document.createElement("div");
  wrap.className = "turno-aegis";
  const thinkingWrap = document.createElement("div");
  thinkingWrap.className = "thinking-wrap oculto";
  thinkingWrap.innerHTML = `<button class="thinking-toggle" title="pensamento do modelo">🧠 thinking</button><div class="thinking-corpo"></div>`;
  const thinking = thinkingWrap.querySelector(".thinking-corpo") as HTMLDivElement;
  thinkingWrap.querySelector("button")!.addEventListener("click", () =>
    thinkingWrap.classList.toggle("aberto"));
  const resp = document.createElement("div");
  resp.className = "resposta";
  const metrica = document.createElement("div");
  metrica.className = "metrica-turno oculto";
  const spinner = document.createElement("div");
  spinner.className = "spinner oculto";
  spinner.textContent = "trabalhando…";
  wrap.append(spinner, thinkingWrap, resp, metrica);
  chatEl.appendChild(wrap);
  chatEl.scrollTop = chatEl.scrollHeight;
  estado.turnoAtual = { resp, thinking, thinkingWrap, metrica, acumulado: "" };
  estado.tokens = 0;
  // re-render do markdown com rAF (fluidez)
  let pendente = false;
  Object.defineProperty(estado.turnoAtual, "atualizarMd", {
    value: () => {
      if (pendente) return;
      pendente = true;
      requestAnimationFrame(() => {
        pendente = false;
        const t = estado.turnoAtual;
        if (t && t.acumulado) t.resp.innerHTML = renderMarkdown(t.acumulado);
        chatEl.scrollTop = chatEl.scrollHeight;
      });
    },
  });
}

function fecharTurno() {
  const t = estado.turnoAtual;
  if (t) {
    const sp = chatEl.querySelector(".spinner:not(.oculto)");
    sp?.classList.add("oculto");
    t.metrica.classList.remove("oculto");
  }
}

// ------------------------------------------------------------------- feed
function itemFeed(icone: string, titulo: string, corpo?: string, classe = "") {
  const d = document.createElement("div");
  d.className = `feed-item ${classe}`;
  d.innerHTML = `<div class="feed-icone">${icone}</div><div class="feed-corpo"><div class="feed-titulo">${titulo}</div>${corpo ? `<div class="feed-detalhe">${corpo}</div>` : ""}</div>`;
  feedEl.appendChild(d);
  feedEl.scrollTop = feedEl.scrollHeight;
  return d;
}

function cardToolInicio(f: Frame) {
  const nome = String(f.nome ?? "?");
  const d = itemFeed("🕐", `<span class="tool-nome">${nome}</span> <span class="tool-estado ativo">executando</span>`);
  const det = d.querySelector(".feed-detalhe") as HTMLDivElement;
  const args = String(f.args && escaparJson(f.args) || "");
  det.innerHTML = `<details><summary>args</summary><pre>${escapeHtml(args)}</pre></details>`;
  return d;
}

function escapeHtml(s: string) {
  return s.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function cardArquivo(f: Frame) {
  const caminho = String(f.caminho ?? "?");
  const status = f.status === "ok" ? "✓" : "✗";
  const d = itemFeed("📄", `<span class="arquivo-caminho">${escapeHtml(caminho)}</span> ${status}`, undefined, f.status === "ok" ? "ok" : "erro");
  const det = d.querySelector(".feed-detalhe") as HTMLDivElement;
  det.innerHTML = `<details><summary>diff (${String(f.acao)})</summary>${renderDiff(String(f.diff ?? ""))}</details>`;
}

function cardComando(f: Frame) {
  const statusTxt = f.status === "ok" ? "✓" : f.status === "recusado" ? "🛑 recusado" : "✗";
  const confirmado = f.confirmado ? " [confirmado]" : "";
  const d = itemFeed("❯", `<code>${escapeHtml(String(f.cmd ?? ""))}</code> <span class="tool-estado ${f.status === "ok" ? "feito" : "erro"}">${statusTxt}${confirmado}</span>`);
  const det = d.querySelector(".feed-detalhe") as HTMLDivElement;
  det.textContent = `${f.resumo ?? ""}${f.duracao_ms ? ` — ${tempoAgo(Number(f.duracao_ms))}` : ""}`;
}

function cardSubgrafo(f: Frame) {
  const inicio = f.evento === "start";
  itemFeed(inicio ? "⏳" : "✅", `<span class="subgrafo-nome">${escapeHtml(String(f.nome))}</span> ${inicio ? "iniciou" : "concluiu"}`);
  // árvore viva do multiagente
  const corpo = byId<HTMLDivElement>("arvore-corpo");
  if (corpo.textContent?.startsWith("subgrafos do multiagente")) corpo.innerHTML = "";
  const nivel = Number(f.nivel ?? 1);
  const div = document.createElement("div");
  div.className = inicio ? "arvore-item arvore-start" : "arvore-item";
  div.style.paddingLeft = `${(nivel - 1) * 16 + 6}px`;
  div.textContent = `${inicio ? "⏳" : "✅"} ${String(f.nome)}`;
  corpo.appendChild(div);
}

function cardVeredito(f: Frame) {
  const v = (f.veredito ?? {}) as Record<string, unknown>;
  const status = String(v.status ?? "?");
  const ok = status === "aprovado";
  const cor = ok ? "ok" : "erro";
  itemFeed(ok ? "✓" : "✗", `<span class="veredito-${cor}">${escapeHtml(status)}</span> <span class="veredito-nota">nota ${String(v.nota ?? "–")}</span>`, String(v.feedback ?? "").slice(0, 200), cor);
}

function itemToolEnd(f: Frame, card: HTMLElement | null) {
  if (!card) return;
  const est = card.querySelector(".tool-estado") as HTMLSpanElement | null;
  if (est) {
    est.textContent = "✓";
    est.classList.remove("ativo");
    est.classList.add("feito");
  }
  const det = card.querySelector(".feed-detalhe") as HTMLDivElement;
  if (det && !det.innerHTML.includes("details")) {
    det.textContent = String(f.saida ?? "").slice(0, 160);
  }
}

// ------------------------------------------------------------------- wire
function wireFrame(f: Frame) {
  const d = document.createElement("div");
  d.className = `wire-linha wire-${f.kind}`;
  d.innerHTML = `<span class="wire-kind">${escapeHtml(String(f.kind))}</span> <code>${escapeHtml(escaparJson(f, 300))}</code>`;
  wireEl.appendChild(d);
  wireEl.scrollTop = wireEl.scrollHeight;
}

// ------------------------------------------------------------------- métricas
function desenharSpark() {
  const w = canvasSpark.width, h = canvasSpark.height;
  sparkCtx.clearRect(0, 0, w, h);
  if (estado.metricas.length < 2) return;
  const serie = estado.metricas.map((m) => m[2]); // tps
  const max = Math.max(...serie, 1);
  sparkCtx.strokeStyle = "#4f9cf9";
  sparkCtx.lineWidth = 1.5;
  sparkCtx.beginPath();
  serie.forEach((v, i) => {
    const x = (i / (serie.length - 1)) * (w - 4) + 2;
    const y = h - 3 - (v / max) * (h - 6);
    i === 0 ? sparkCtx.moveTo(x, y) : sparkCtx.lineTo(x, y);
  });
  sparkCtx.stroke();
}

function renderMetricas([tokens, duracao, tps]: [number, number, number]) {
  byId("m-tokens").textContent = String(tokens);
  byId("m-duracao").textContent = `${duracao.toFixed(2)} s`;
  byId("m-tps").textContent = tps.toFixed(1);
  desenharSpark();
}

// ------------------------------------------------------------------- frames
function aoFrame(f: Frame) {
  wireFrame(f);
  const t = estado.turnoAtual;
  switch (f.kind) {
    case "token": {
      t!.acumulado += String(f.texto ?? "");
      (t as unknown as { atualizarMd(): void }).atualizarMd();
      estado.tokens += 1;
      estado.textosTurno.push(String(f.texto ?? ""));
      break;
    }
    case "reasoning": {
      const corpo = t!.thinking;
      corpo.textContent += String(f.texto ?? "");
      t!.thinkingWrap.classList.remove("oculto");
      break;
    }
    case "tool_inicio": {
      const card = cardToolInicio(f);
      estado.toolsAbertos = estado.toolsAbertos ?? new Map<string, HTMLElement>();
      estado.toolsAbertos.set(String(f.id), card);
      break;
    }
    case "tool_fim": {
      const card = ((estado.toolsAbertos ?? new Map()) as Map<string, HTMLElement>).get(String(f.id));
      itemToolEnd(f, card ?? null);
      break;
    }
    case "arquivo": cardArquivo(f); break;
    case "comando": cardComando(f); break;
    case "subgrafo": cardSubgrafo(f); break;
    case "veredito": cardVeredito(f); break;
    case "metrica": {
      renderMetricas([Number(f.tokens ?? 0), Number(f.duracao_s ?? 0), Number(f.tps ?? 0)]);
      estado.metricas.push([Number(f.tokens), Number(f.duracao_s), Number(f.tps)]);
      if (estado.metricas.length > 60) estado.metricas.shift();
      break;
    }
    case "fim": {
      if (t && !t.acumulado) {
        t.acumulado = String(f.texto ?? "");
        t.resp.innerHTML = renderMarkdown(t.acumulado);
      }
      fecharTurno();
      estado.jobAtivo = false;
      statusEl.textContent = "pronto";
      enviarBtn.disabled = false;
      inputEl.disabled = false;
      break;
    }
    case "erro": {
      itemFeed("💥", "erro no agente", escapeHtml(String(f.mensagem ?? f.tipo ?? "")), "erro");
      fecharTurno();
      estado.jobAtivo = false;
      statusEl.textContent = "erro";
      enviarBtn.disabled = false;
      inputEl.disabled = false;
      break;
    }
  }
}

// ------------------------------------------------------------------- turno
async function iniciarTurno() {
  const texto = inputEl.value.trim();
  if (!texto || estado.jobAtivo) return;
  inputEl.value = "";
  adicionarUsuario(texto);
  abrirTurnoAegis();
  estado.jobAtivo = true;
  estado.toolsAbertos = new Map();
  enviarBtn.disabled = true;
  inputEl.disabled = true;
  statusEl.textContent = "agindo…";
  try {
    const res = await fetch("/api/mensagem", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texto, thread_id: estado.threadId }),
    });
    const { job_id: jobId } = (await res.json()) as { job_id?: string };
    if (!jobId) throw new Error("sem job_id");
    const es = new EventSource(`/api/stream?job_id=${jobId}`);
    es.onmessage = (ev) => { try { aoFrame(JSON.parse(ev.data) as Frame); } catch { /* frame corrompido */ } };
    es.onerror = () => es.close(); // o servidor encerra no fim/erro
  } catch (err) {
    aoFrame({ kind: "erro", mensagem: String(err) });
  }
}

// ------------------------------------------------------------------- painel direito
function ativarAba(nome: string) {
  tabBotoes.forEach((b) => b.classList.toggle("ativa", b.dataset.aba === nome));
  (["metricas", "arvore", "wire", "config", "historico"] as const).forEach((p) =>
    byId(`painel-${p}`).classList.toggle("ativo", p === nome || (p === "metricas" && nome === "metricas")));
}

async function carregarConfig() {
  try {
    const res = await fetch("/api/estado");
    const dados = (await res.json()) as Record<string, unknown>;
    const tbl = byId<HTMLDivElement>("config-corpo");
    tbl.innerHTML = Object.entries(dados)
      .map(([k, v]) => `<div class="cfg-linha"><span class="cfg-chave">${escapeHtml(k)}</span><span class="cfg-valor">${escapeHtml(String(v ?? ""))}</span></div>`)
      .join("");
  } catch (err) {
    byId("config-corpo").textContent = `falha: ${String(err)}`;
  }
}

async function carregarHistorico() {
  try {
    const res = await fetch("/api/historico");
    const dados = (await res.json()) as { threads?: Array<{ thread_id: string }> };
    const lista = byId<HTMLDivElement>("historico-corpo");
    lista.innerHTML = "";
    for (const t of dados.threads ?? []) {
      const b = document.createElement("button");
      b.className = "thread-botao";
      b.textContent = t.thread_id;
      b.addEventListener("click", () => { estado.threadId = t.thread_id; threadBadgeEl.textContent = `thread: ${t.thread_id}`; });
      lista.appendChild(b);
    }
  } catch (err) {
    byId("historico-corpo").textContent = `falha: ${String(err)}`;
  }
}

// ------------------------------------------------------------------- boot
function iniciar() {
  tabBotoes.forEach((b) => b.addEventListener("click", () => ativarAba(b.dataset.aba!)));
  enviarBtn.addEventListener("click", iniciarTurno);
  inputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); iniciarTurno(); }
  });
  threadBadgeEl.textContent = `thread: ${estado.threadId}`;
  ativarAba("metricas");
  carregarConfig();
  carregarHistorico();
  inputEl.focus();
}

iniciar();