/**
 * W5e — markdown avançado (KaTeX + mermaid + tabelas + links) sobre o
 * renderizador leve escape-first. Roda em node puro (sem DOM): o mermaid
 * aqui é só a EXTRAÇÃO do bloco; o render real é browser (`executarMermaid`).
 */
import { describe, expect, test } from "bun:test";
import katex from "katex";
import { definirKatex, renderarMarkdownAvancado } from "./markdown2.ts";

// no browser o katex é vendor global (window.katex); nos testes injeta o real
definirKatex(() => katex);

describe("W5e — markdown avançado", () => {
  test("latex inline $..$ vira KaTeX", () => {
    const html = renderarMarkdownAvancado("a fórmula $x^2 + y^2$ aqui");
    expect(html).toContain('class="katex"');
    expect(html).toContain("x");
  });

  test("latex display $$..$$ entra como bloco", () => {
    const html = renderarMarkdownAvancado("$$E = mc^2$$");
    expect(html).toContain('class="katex-display"');
  });

  test("bloco ```mermaid vira placeholder p/ DOM", () => {
    const html = renderarMarkdownAvancado('texto\n\n```mermaid\ngraph TD;\n  A-->B;\n```\n\nfim');
    expect(html).toContain('<div class="mermaid">');
    expect(html).toContain("graph TD;");
    // o código do diagrama está ESCAPADO (nunca vira HTML executável)
    expect(html).toContain("A--&gt;B");
  });

  test("tabela | a | b | vira <table>", () => {
    const html = renderarMarkdownAvancado(
      "| nome | nota |\n| --- | --- |\n| rust | 10 |\n| python | 9 |",
    );
    expect(html).toContain("<table>");
    expect(html).toContain("<th>nome</th>");
    expect(html).toContain("<td>rust</td>");
  });

  test("tabela com LaTeX resolve slots ANINHADOS (regressão @@SLOT_N@@)", () => {
    // o katex roda ANTES da tabela → os slots de fórmula ficam dentro do slot
    // da tabela; o replace de uma passagem vazava @@SLOT_N@@ literal
    const html = renderarMarkdownAvancado(
      "| Método | Fórmula |\n| --- | --- |\n| Potência | $\\frac{x^{n+1}}{n+1}$ |\n| Exponencial | $e^x$ |",
    );
    expect(html).not.toContain("@@SLOT_");
    expect(html).toContain("<table>");
    expect(html).toContain("<td>Potência</td>");
    expect(html).toContain('class="katex"'); // fórmula renderizada dentro da célula
  });

  test("link http vira <a> seguro e caminho interno vira <code>", () => {
    const html = renderarMarkdownAvancado("[docs](https://aegis.dev) e [x](./local.md)");
    expect(html).toContain('<a href="https://aegis.dev"');
    expect(html).toContain("rel=\"noopener noreferrer\"");
    expect(html).not.toContain('href="./local.md"');
  });

  test("HTML arbitrário continua escapado (segurança do leve)", () => {
    const html = renderarMarkdownAvancado("<script>alert(1)</script> e $x$");
    expect(html).not.toContain("<script>");
    expect(html).toContain("&lt;script&gt;");
  });
});