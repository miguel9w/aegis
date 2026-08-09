/**
 * Render de diff unified colorido (linha a linha) — próprio, zero deps.
 * Cobre o formato dos diff gerados pelas tools de arquivo do Aegis
 * (unified_diff do difflib) e qualquer diff unified padrão.
 */
import { escapar } from "./markdown.ts";

export function renderDiff(texto: string): string {
  const linhas = texto.split("\n");
  const saida: string[] = ['<div class="diff">'];
  let emHunk = false;
  for (const linha of linhas) {
    if (linha.startsWith("+++") || linha.startsWith("---")) {
      saida.push(`<div class="diff-cab"><span>${escapar(linha.slice(0, 4))}</span>${escapar(linha.slice(4)) || "&nbsp;"}</div>`);
      continue;
    }
    if (linha.startsWith("@@")) {
      emHunk = true;
      saida.push(`<div class="diff-hunk">${escapar(linha)}</div>`);
      continue;
    }
    if (linha.startsWith("+") && !linha.startsWith("+++")) {
      saida.push(`<div class="diff-add"><span class="diff-sinal">+</span><code>${escapar(linha.slice(1)) || "&nbsp;"}</code></div>`);
      continue;
    }
    if (linha.startsWith("-") && !linha.startsWith("---")) {
      saida.push(`<div class="diff-rem"><span class="diff-sinal">-</span><code>${escapar(linha.slice(1)) || "&nbsp;"}</code></div>`);
      continue;
    }
    if (emHunk && !linha.trim()) continue;
    saida.push(`<div class="diff-ctx"><span class="diff-sinal"> </span>${escapar(linha) || "&nbsp;"}</div>`);
  }
  saida.push("</div>");
  return saida.join("\n");
}