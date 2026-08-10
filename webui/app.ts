/**
 * Aegis Web UI — front (TS vanilla, bundlado pelo Bun).
 * Consome o SSE do server.ts e pinta: chat streaming, thinking,
 * feed de atividade (tools/arquivo/comando/subgrafo/veredito),
 * painel técnico (métricas, wire, config, histórico).
 */
import { renderDiff } from "./diff.ts";
import { renderMarkdown } from "./markdown.ts";
import { executarMermaid, renderarMarkdownAvancado } from "./markdown2.ts";

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
const interromperBtn = byId<HTMLButtonElement>("interromper-btn");
const modalEl = byId<HTMLDivElement>("modal-perguntas");
const modalComandoEl = byId<HTMLPreElement>("modal-comando");
const modalPermitirBtn = byId<HTMLButtonElement>("modal-permitir");
const modalRecusarBtn = byId<HTMLButtonElement>("modal-recusar");
const wRelogioEl = byId<HTMLSpanElement>("w-relogio");
const wTokensEl = byId<HTMLSpanElement>("w-tokens");
const wPingEl = byId<HTMLSpanElement>("w-ping");
const tabBotoes = document.querySelectorAll<HTMLButtonElement>("[data-aba]");
const canvasSpark = byId<HTMLCanvasElement>("spark");
const sparkCtx = canvasSpark.getContext("2d")!;

// ------------------------------------------------------------------- estado
const estado = {
  threadId: "default",
  jobAtivo: false,
  jobIdAtual: "",
  ultimaMensagem: "",
  tokensSessao: 0,
  torneio: 0 as number,
  turnoAtual: null as null | {
    resp: HTMLDivElement; thinking: HTMLDivElement; thinkingWrap: HTMLDivElement;
    metrica: HTMLDivElement; acumulado: string;
  },
  tokens: 0,
  metricas: [] as Array<[number, number, number]>, // [tokens, duração, tps]
  textosTurno: [] as string[],
  esAtual: null as EventSource | null,
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

/** Encerra o turno (fim/erro/interrompido) e devolve a UI ao estado pronto. */
function finalizarTurno(rotulo: string) {
  fecharTurno();
  estado.jobAtivo = false;
  estado.jobIdAtual = "";
  interromperBtn.classList.add("oculto");
  statusEl.textContent = rotulo;
  enviarBtn.disabled = false;
  inputEl.disabled = false;
}

// ------------------------------------------------------------------- interromper
async function interromper() {
  if (!estado.jobAtivo || !estado.jobIdAtual) return;
  interromperBtn.disabled = true;
  statusEl.textContent = "interrompendo…";
  try {
    await fetch("/api/interromper", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: estado.jobIdAtual }),
    });
  } catch { /* ponte offline — o SSE morre sozinho; segue o baile */ }
  estado.esAtual?.close();
  estado.esAtual = null;
  // o SSE encerra quando a ponte emitir `fim` interrompido; se já caiu, fecha local:
  const t = estado.turnoAtual;
  if (t && t.acumulado === "" && estado.jobAtivo) {
    t.acumulado = "(interrompido)";
    t.resp.innerHTML = renderMarkdown(t.acumulado);
  }
  finalizarTurno("interrompido");
}

// ------------------------------------------------------------------- janela de perguntas
function abrirModalPergunta(comando: string) {
  modalComandoEl.textContent = comando;
  modalEl.classList.remove("oculto");
}

function fecharModalPergunta() {
  modalEl.classList.add("oculto");
}

async function responderPermitir() {
  const comando = modalComandoEl.textContent ?? "";
  fecharModalPergunta();
  try {
    await fetch("/api/autorizar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ comando }),
    });
  } catch { /* falha silenciosa — o próximo turno re-tenta (agora com o botão) */ }
  // reenvia o turno que pediu aprovação — agora o comando executa na sessão
  if (estado.ultimaMensagem && !estado.jobAtivo) {
    inputEl.value = estado.ultimaMensagem;
    iniciarTurno();
  }
}

// ------------------------------------------------------------------- comandos do input
// `/comando` → sugestões do slash da TUI · `@agente` → personas/multiagente/APF
// · `-/arquivo` → anexa um arquivo do projeto (lido e enviado no turno).
// Tab completa a sugestão destacada (ou a primeira); ↑/↓ navegam; Esc fecha.

interface Sugestao {
  rotulo: string;
  descricao: string;
  grupo: "comandos" | "agentes" | "prompts" | "papeis" | "arquivos";
}

interface Catalogo {
  comandos: Array<{ nome: string; descricao: string }>;
  agentes: Array<{ nome: string; descricao: string }>;
  prompts: Array<{ id: string; versao?: string; descricao?: string }>;
  papeis: Array<{ nome: string; descricao?: string }>;
}

const CATALOGO_FALLBACK: Catalogo = {
  comandos: [
    { nome: "ajuda", descricao: "lista de comandos" },
    { nome: "status", descricao: "estado do Aegis (modelo, memória, multiagente)" },
    { nome: "prompt", descricao: "[id|nenhum] — ativa/mostra o prompt avançado (APF)" },
    { nome: "prompts", descricao: "lista os APFs disponíveis" },
    { nome: "papeis", descricao: "lista os papéis (CAMEL)" },
    { nome: "definir_papel", descricao: "[nome] — define o papel ativo" },
    { nome: "limpar", descricao: "limpa o chat local" },
    { nome: "novo", descricao: "nova thread" },
  ],
  agentes: [
    { nome: "programacao", descricao: "subgrafo multiagente — código/dev" },
    { nome: "pesquisa", descricao: "subgrafo multiagente — papers/web" },
    { nome: "escrita", descricao: "subgrafo multiagente — textos" },
    { nome: "obsidian", descricao: "subgrafo multiagente — notas MyLife" },
    { nome: "memoria", descricao: "subgrafo multiagente — memória" },
  ],
  prompts: [],
  papeis: [],
};

let catalogo: Catalogo = CATALOGO_FALLBACK;
const anexos = new Map<string, string>(); // caminho → rótulo do chip
let sugestoes: Sugestao[] = [];
let sugestaoSel = 0;
let seqArquivos = 0;
let consultaArquivos = "";

const sugestoesEl = byId<HTMLDivElement>("sugestoes");
const anexosEl = byId<HTMLDivElement>("anexos");

function tokenAtual(): { inicio: number; fim: number; token: string } {
  const v = inputEl.value;
  const cursor = inputEl.selectionStart >= 0 ? inputEl.selectionStart : v.length;
  const ateEspaco = v.lastIndexOf(" ", cursor - 1);
  const inicio = ateEspaco + 1;
  const m = v.slice(inicio).match(/^\S+/);
  const largura = m ? m[0].length : 0;
  return { inicio, fim: inicio + largura, token: v.slice(inicio, inicio + largura) };
}

function fecharSugestoes() {
  sugestoes = [];
  sugestaoSel = 0;
  sugestoesEl.classList.add("oculto");
  sugestoesEl.innerHTML = "";
}

function renderizarSugestoes() {
  if (!sugestoes.length) { fecharSugestoes(); return; }
  sugestoesEl.innerHTML = "";
  let grupoAtual = "";
  sugestoes.forEach((s, i) => {
    if (s.grupo !== grupoAtual) {
      grupoAtual = s.grupo;
      const g = document.createElement("div");
      g.className = "sug-grupo";
      g.textContent = grupoAtual;
      sugestoesEl.appendChild(g);
    }
    const d = document.createElement("div");
    d.className = `sug-item${i === sugestaoSel ? " selecionada" : ""}`;
    d.innerHTML = `<span class="sug-rotulo">${escapeHtml(s.rotulo)}</span>${s.descricao ? `<span class="sug-desc">${escapeHtml(s.descricao)}</span>` : ""}`;
    d.addEventListener("mousedown", (ev) => { ev.preventDefault(); completarSugestao(i); });
    sugestoesEl.appendChild(d);
  });
  sugestoesEl.classList.remove("oculto");
}

function completarSugestao(i?: number) {
  const s = sugestoes[i ?? sugestaoSel] ?? sugestoes[0];
  if (!s) return;
  const { inicio, fim } = tokenAtual();
  if (s.grupo === "arquivos") {
    // vira chip de anexo (o conteúdo é lido só no envio)
    inputEl.value = inputEl.value.slice(0, inicio) + inputEl.value.slice(fim);
    anexos.set(s.rotulo, s.rotulo);
    renderizarAnexos();
  } else {
    inputEl.value = inputEl.value.slice(0, inicio) + s.rotulo + " " + inputEl.value.slice(fim);
    inputEl.selectionStart = inputEl.selectionEnd = inicio + s.rotulo.length + 1;
  }
  fecharSugestoes();
  inputEl.focus();
  atualizarSugestoes();
  inputEl.dispatchEvent(new Event("input", { bubbles: true })); // recalcula autosize
}

function renderizarAnexos() {
  anexosEl.innerHTML = "";
  for (const caminho of anexos.keys()) {
    const chip = document.createElement("span");
    chip.className = "chip-anexo";
    chip.innerHTML = `📎 <span>${escapeHtml(caminho)}</span> <button title="remover anexo">✕</button>`;
    chip.querySelector("button")!.addEventListener("click", () => {
      anexos.delete(caminho);
      renderizarAnexos();
    });
    anexosEl.appendChild(chip);
  }
}

function atualizarSugestoes() {
  const { token } = tokenAtual();
  if (token.startsWith("-/")) {
    // arquivos do projeto — consulta assíncrona (uma por termo)
    const q = token.slice(2);
    if (q !== consultaArquivos || sugestoes.length === 0) {
      consultaArquivos = q;
      const meuSeq = ++seqArquivos;
      fetch(`/api/arquivos?q=${encodeURIComponent(q)}`)
        .then((r) => (r.ok ? r.json() : null))
        .catch(() => null)
        .then((dados) => {
          if (meuSeq !== seqArquivos) return;
          const nomes: string[] = (dados?.arquivos ?? []).slice(0, 40);
          sugestoes = nomes.map((n) => ({ rotulo: n, descricao: "", grupo: "arquivos" }));
          sugestaoSel = 0;
          renderizarSugestoes();
        });
    }
    return;
  }
  seqArquivos++; // invalida consultas de arquivo pendentes
  const resto = token.slice(1).toLowerCase();
  let lista: Sugestao[] = [];
  if (token.startsWith("/")) {
    lista = catalogo.comandos
      .filter((c) => c.nome.startsWith(resto))
      .slice(0, 30)
      .map((c) => ({ rotulo: `/${c.nome}`, descricao: c.descricao, grupo: "comandos" }));
  } else if (token.startsWith("@")) {
    const a = catalogo.agentes.filter((x) => x.nome.startsWith(resto))
      .map((x) => ({ rotulo: `@${x.nome}`, descricao: x.descricao, grupo: "agentes" }));
    const p = catalogo.prompts.filter((x) => x.id.startsWith(resto))
      .map((x) => ({ rotulo: `@${x.id}`, descricao: `APF v${x.versao ?? "?"}${x.descricao ? ` — ${x.descricao}` : ""}`, grupo: "prompts" }));
    const pa = catalogo.papeis.filter((x) => x.nome.startsWith(resto))
      .map((x) => ({ rotulo: `@${x.nome}`, descricao: `papel${x.descricao ? ` — ${x.descricao}` : ""}`, grupo: "papeis" }));
    lista = [...a, ...p, ...pa].slice(0, 40);
  }
  sugestoes = lista;
  sugestaoSel = 0;
  renderizarSugestoes();
}

async function carregarCatalogo() {
  try {
    const res = await fetch("/api/sugestoes");
    if (!res.ok) return;
    const dados = (await res.json()) as Catalogo;
    if (Array.isArray(dados.comandos) && Array.isArray(dados.agentes)) catalogo = dados;
  } catch { /* catálogo local permanece */ }
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
  const d = itemFeed("📄", `<span class="arquivo-caminho">${escapeHtml(caminho)}</span> ${status}<span class="chip chip-sandbox">sandbox</span>`, undefined, f.status === "ok" ? "ok" : "erro");
  const det = d.querySelector(".feed-detalhe") as HTMLDivElement;
  det.innerHTML = `<details><summary>diff (${String(f.acao)})</summary>${renderDiff(String(f.diff ?? ""))}</details>`;
}

function cardComando(f: Frame) {
  const statusTxt = f.status === "ok" ? "✓" : f.status === "recusado" ? "🛑 recusado" : "✗";
  const confirmado = f.confirmado ? " [confirmado]" : "";
  let chip = "";
  if (f.status === "recusado" && f.motivo === "politica") chip = '<span class="chip chip-politica">bloqueado</span>';
  else if (f.motivo === "confirmacao" || /confirmar=true/i.test(String(f.resumo ?? ""))) chip = '<span class="chip chip-politica">requer aprovação</span>';
  else chip = '<span class="chip chip-aprovado">política ok</span>';
  const d = itemFeed("❯", `<code>${escapeHtml(String(f.cmd ?? ""))}</code> <span class="tool-estado ${f.status === "ok" ? "feito" : "erro"}">${statusTxt}${confirmado}</span>${chip}`);
  const det = d.querySelector(".feed-detalhe") as HTMLDivElement;
  det.textContent = `${f.resumo ?? ""}${f.duracao_ms ? ` — ${tempoAgo(Number(f.duracao_ms))}` : ""}`;
  // janela de perguntas: comando fora da allowlist pode ser aprovado pelo usuário
  if (f.status === "recusado" && f.motivo === "confirmacao" && f.cmd) {
    d.classList.add("erro");
    const acao = document.createElement("button");
    acao.className = "modal-abrir-btn";
    acao.textContent = "❓ responder";
    acao.addEventListener("click", () => abrirModalPergunta(String(f.cmd)));
    d.querySelector(".feed-corpo")!.appendChild(acao);
  }
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
      estado.tokensSessao += Number(f.tokens ?? 0);
      wTokensEl.textContent = String(estado.tokensSessao);
      break;
    }
    case "fim": {
      if (t) {
        const textoFinal = t.acumulado || String(f.texto ?? "");
        if (textoFinal) {
          t.acumulado = textoFinal;
          t.resp.innerHTML = renderarMarkdownAvancado(textoFinal);
          void executarMermaid(t.resp);
        }
      }
      finalizarTurno(f.interrompido ? "interrompido" : "pronto");
      break;
    }
    case "erro": {
      itemFeed("💥", "erro no agente", escapeHtml(String(f.mensagem ?? f.tipo ?? "")), "erro");
      const ta = estado.turnoAtual;
      if (ta && ta.acumulado) {
        ta.resp.innerHTML = renderarMarkdownAvancado(ta.acumulado);
        void executarMermaid(ta.resp);
      }
      finalizarTurno("erro");
      break;
    }
  }
}

// ------------------------------------------------------------------- turno
async function iniciarTurno(textoForcado?: string) {
  fecharSugestoes();
  let texto = (textoForcado ?? inputEl.value).trim();
  if (!texto || estado.jobAtivo) return;
  inputEl.value = "";

  // `@agente` no primeiro token: força o subgrafo (dominio) ou ativa APF/papel
  let dominio = "";
  const mInicio = texto.match(/^@([-\w]+)/);
  if (mInicio) {
    const alvo = mInicio[1];
    if (catalogo.agentes.some((x) => x.nome === alvo)) {
      dominio = alvo;
      texto = texto.slice(mInicio[0].length).trim();
    } else {
      const apf = catalogo.prompts.find((x) => x.id === alvo);
      const papel = catalogo.papeis.find((x) => x.nome === alvo);
      if (apf || papel) {
        const resp = await fetch("/api/slash", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            nome: apf ? "prompt" : "definir_papel",
            arg: apf ? apf.id : (papel?.nome ?? ""),
          }),
        }).catch(() => null);
        const corpo = resp?.ok ? await resp.json() : null;
        itemFeed(
          apf ? "📌" : "👤",
          apf ? `APF ativado: @${apf.id}` : `papel definido: @${papel?.nome}`,
          corpo?.texto ?? "sem resposta da ponte",
        );
        texto = texto.slice(mInicio[0].length).trim();
      }
    }
  }

  // anexos `-/arquivo`: lidos agora e embutidos na mensagem do turno
  if (anexos.size) {
    const blocos: string[] = [];
    for (const caminho of anexos.keys()) {
      // eslint-disable-next-line no-await-in-loop
      const res = await fetch(`/api/arquivo?caminho=${encodeURIComponent(caminho)}`).catch(() => null);
      if (!res?.ok) {
        itemFeed("❌", "anexo falhou", `${caminho} não pôde ser lido — remova o chip e tente de novo`);
        inputEl.value = texto;
        return;
      }
      const dados = (await res.json()) as { conteudo: string };
      blocos.push(`📎 anexo: ${caminho}\n\`\`\`text\n${dados.conteudo}\n\`\`\``);
    }
    anexos.clear();
    renderizarAnexos();
    texto = `${blocos.join("\n\n")}\n\n${texto}`;
  }

  adicionarUsuario(texto);
  abrirTurnoAegis();
  estado.jobAtivo = true;
  estado.ultimaMensagem = texto;
  estado.toolsAbertos = new Map();
  enviarBtn.disabled = true;
  inputEl.disabled = true;
  interromperBtn.disabled = false;
  interromperBtn.classList.remove("oculto");
  statusEl.textContent = "agindo…";
  try {
    const res = await fetch("/api/mensagem", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        texto, thread_id: estado.threadId, ...(dominio ? { dominio } : {}),
      }),
    });
    const { job_id: jobId } = (await res.json()) as { job_id?: string };
    if (!jobId) throw new Error("sem job_id");
    estado.jobIdAtual = jobId;
    const es = new EventSource(`/api/stream?job_id=${jobId}`);
    estado.esAtual = es;
    es.onmessage = (ev) => { try { aoFrame(JSON.parse(ev.data) as Frame); } catch { /* frame corrompido */ } };
    es.onerror = () => { es.close(); if (estado.esAtual === es) estado.esAtual = null; };
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
function iniciarWidgets() {
  // relógio do host
  const tickRelogio = () => {
    const agora = new Date();
    wRelogioEl.textContent = agora.toLocaleTimeString("pt-BR", { hour12: false });
  };
  tickRelogio();
  setInterval(tickRelogio, 1000);
  // ping da ponte python (healthz)
  const tickPing = async () => {
    try {
      const res = await fetch("/api/healthz");
      const h = (await res.json()) as { ponte?: string };
      wPingEl.textContent = "●";
      wPingEl.className = h.ponte === "ok" ? "ping-vivo" : "ping-dead";
      wPingEl.title = h.ponte === "ok" ? "ponte ok" : "ponte morta (o servidor reinicia sozinho)";
    } catch {
      wPingEl.className = "ping-dead";
    }
  };
  tickPing();
  setInterval(tickPing, 10_000);
}

function iniciar() {
  tabBotoes.forEach((b) => b.addEventListener("click", () => ativarAba(b.dataset.aba!)));
  enviarBtn.addEventListener("click", () => iniciarTurno());
  inputEl.addEventListener("input", () => atualizarSugestoes());
  inputEl.addEventListener("keydown", (e) => {
    const aberto = sugestoes.length > 0;
    if (aberto && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
      e.preventDefault();
      sugestaoSel = (sugestaoSel + (e.key === "ArrowDown" ? 1 : -1) + sugestoes.length) % sugestoes.length;
      renderizarSugestoes();
      return;
    }
    if (aberto && e.key === "Tab") { e.preventDefault(); completarSugestao(); return; }
    if (aberto && e.key === "Escape") { fecharSugestoes(); return; }
    if (aberto && e.key === "Enter") { e.preventDefault(); completarSugestao(); return; }
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); iniciarTurno(); }
  });
  interromperBtn.addEventListener("click", interromper);
  modalPermitirBtn.addEventListener("click", responderPermitir);
  modalRecusarBtn.addEventListener("click", fecharModalPergunta);
  threadBadgeEl.textContent = `thread: ${estado.threadId}`;
  void carregarCatalogo();
  ativarAba("metricas");
  carregarConfig();
  carregarHistorico();
  iniciarWidgets();
  inputEl.focus();
}

iniciar();