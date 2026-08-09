/**
 * Markdown leve e seguro para a Web UI — zero deps, foco no comum:
 * código inline/block, negrito, itálico, listas, títulos e quebras.
 * Sem renderização de HTML arbitrário (escape primeiro).
 */
export function escapar(s: string): string {
  return s
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;");
}

/** Realces inline (code/negrito/itálico) numa linha já escapada. */
function inline(linha: string): string {
  return linha
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/(^|[^\w*])\*([^*\n]+)\*(?=$|[^\w*])/g, "$1<em>$2</em>");
}

export function renderMarkdown(texto: string): string {
  const saida: string[] = [];
  let emCodigo = false;
  let bloco: string[] = [];
  let emLista = false;

  const fecharLista = () => {
    if (emLista) { saida.push("</ul>"); emLista = false; }
  };

  const flush = () => {
    if (emCodigo) {
      saida.push(`<pre><code>${escapar(bloco.join("\n"))}</code></pre>`);
      bloco = [];
      emCodigo = false;
    }
  };

  for (const linhaBruta of texto.split("\n")) {
    const linha = linhaBruta.trimEnd();

    if (emCodigo) {
      if (linha.startsWith("```")) { flush(); continue; }
      bloco.push(linha);
      continue;
    }
    if (linha.startsWith("```")) { fecharLista(); emCodigo = true; continue; }

    if (linha.startsWith("# ")) {
      fecharLista();
      saida.push(`<h3>${inline(escapar(linha.slice(2)))}</h3>`);
      continue;
    }
    if (linha.startsWith("## ")) {
      fecharLista();
      saida.push(`<h4>${inline(escapar(linha.slice(3)))}</h4>`);
      continue;
    }
    if (/^[-*•]\s+/.test(linha)) {
      if (!emLista) { saida.push("<ul>"); emLista = true; }
      saida.push(`<li>${inline(escapar(linha.replace(/^[-*•]\s+/, "")))}</li>`);
      continue;
    }
    fecharLista();
    if (!linha.trim()) { saida.push("<br/>"); continue; }
    saida.push(`<p>${inline(escapar(linha))}</p>`);
  }
  flush();
  fecharLista();
  return saida.join("\n");
}