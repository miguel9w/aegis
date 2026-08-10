/**
 * Markdown AVANÇADO para o chat (W5e) — camada sobre o `markdown.ts` leve.
 *
 * O leve é escape-first (zero HTML arbitrário), então o HTML pronto gerado
 * aqui (KaTeX, tabelas, links) entra em SLOTS temporários, o restante passa
 * pelo renderizador leve e os slots são restaurados no final. O mermaid sai
 * como placeholder porque o render real precisa de DOM (`executarMermaid`).
 */
import { renderMarkdown } from "./markdown.ts";

const MERMAID = /```mermaid\s*\n([\s\S]*?)```/g;

/**
 * KaTeX é carregado como vendor global (`/vendor/katex.min.js`, script tag no
 * index.html) — o bundle fica enxuto e a dep é cacheada como imutável. Para
 * testes (bun/node) o resolver é injetável via `definirKatex`.
 */
let obterKatex: () => any = () => (globalThis as any).katex;
export function definirKatex(fn: () => any): void {
  obterKatex = fn;
}

function escapeHtml(s: string): string {
  return s.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

/** Guarda HTML pronto num slot e devolve o placeholder correspondente. */
function slot(html: string, lista: string[]): string {
  lista.push(html);
  return `@@SLOT_${lista.length - 1}@@`;
}

/** `| a | b |` → tabela <table> (linha de separação vira thead). */
function renderizarTabelas(texto: string, slots: string[]): string {
  if (!texto.includes("|")) return texto;
  const partes = texto.split(/\n\n+/);
  return partes.map((bloco) => {
    const linhas = bloco.split("\n").filter((l) => l.trim().startsWith("|") && l.trim().endsWith("|"));
    if (linhas.length < 2) return bloco;
    const ehSep = (l: string) => /^\|[\s:|-]+\|$/.test(l);
    const celulas = (l: string) =>
      l.trim().slice(1, -1).split("|").map((c) => escapeHtml(c.trim()).replaceAll("**", ""));
    const corpo = linhas.filter((l) => !ehSep(l));
    if (!corpo.length) return bloco;
    const html = `<table><thead><tr>${celulas(corpo[0]).map((c) => `<th>${c}</th>`).join("")}</tr></thead><tbody>${corpo.slice(1).map((l) => `<tr>${celulas(l).map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")}</tbody></table>`;
    return slot(html, slots);
  }).join("\n\n");
}

/** `[texto](http...)` → <a> seguro (target blank, rel noopener). */
function renderizarLinks(texto: string, slots: string[]): string {
  return texto
    .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, (_t, t: string, u: string) =>
      slot(`<a href="${escapeHtml(u)}" target="_blank" rel="noopener noreferrer">${escapeHtml(t)}</a>`, slots))
    .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_t, t: string, u: string) =>
      slot(`<code>${escapeHtml(u)}</code>`, slots)); // caminho interno: não vira link
}

/** Compila `$..$`, `$$..$$` e `\(..\)` com KaTeX; sem mudança em falha. */
function renderizarMath(texto: string, slots: string[]): string {
  const katex = obterKatex();
  if (!katex) return texto; // vendor ainda não carregou: mantém o texto cru
  texto = texto.replace(/\$\$([\s\S]+?)\$\$/g, (_t, expr: string) => {
    try {
      return slot(katex.renderToString(expr.trim(), { displayMode: true, throwOnError: false }), slots);
    } catch {
      return `$${expr}$$`;
    }
  });
  texto = texto.replace(/\$([^\s$][^$]*)\$/g, (_t, expr: string) => {
    try {
      return slot(katex.renderToString(expr.trim(), { displayMode: false, throwOnError: false }), slots);
    } catch {
      return `$${expr}$`;
    }
  });
  texto = texto.replace(/\\\(([\s\S]+?)\\\)/g, (_t, expr: string) => {
    try {
      return slot(katex.renderToString(expr.trim(), { displayMode: false, throwOnError: false }), slots);
    } catch {
      return `\\(${expr}\\)`;
    }
  });
  return texto;
}

/** Renderiza o markdown avançado (mermaid sai como placeholder p/ DOM). */
export function renderarMarkdownAvancado(texto: string): string {
  const slots: string[] = [];
  const blocosMermaid: string[] = [];
  const semMermaid = texto.replace(MERMAID, (_t, codigo: string) => {
    blocosMermaid.push(codigo);
    return `\n\n@@MERMAID_${blocosMermaid.length - 1}@@\n\n`;
  });
  let trabalho = renderizarMath(semMermaid, slots);
  trabalho = renderizarTabelas(trabalho, slots);
  trabalho = renderizarLinks(trabalho, slots);
  let html = renderMarkdown(trabalho);
  html = html.replace(/@@SLOT_(\d+)@@/g, (_t, i: string) => slots[Number(i)] ?? "");
  if (blocosMermaid.length) {
    html = html.replace(/@@MERMAID_(\d+)@@/g, (_t, i: string) =>
      `<div class="mermaid">${escapeHtml(blocosMermaid[Number(i)])}</div>`);
  }
  return html;
}

/** Carrega um script vendor sob demanda (mermaid é ~3 MB — só quando há diagrama). */
function carregarScript(src: string): Promise<void> {
  return new Promise((resolve, rejeita) => {
    const s = document.createElement("script");
    s.src = src;
    s.onload = () => resolve();
    s.onerror = () => rejeita(new Error(`vendor não carregou: ${src}`));
    document.head.appendChild(s);
  });
}

/** Renderiza os diagramas mermaid já presentes no container (browser). */
export async function executarMermaid(container: HTMLElement): Promise<void> {
  const alvos = container.querySelectorAll(".mermaid");
  if (!alvos.length) return;
  try {
    const mermaid = (globalThis as any).mermaid ?? ((await carregarScript("/vendor/mermaid.min.js")), (globalThis as any).mermaid);
    mermaid.initialize({ startOnLoad: false, theme: "dark", securityLevel: "strict" });
    await mermaid.run({ nodes: alvos as unknown as Array<Element> });
  } catch {
    for (const a of alvos) a.textContent = "(falha ao renderizar o diagrama)";
  }
}