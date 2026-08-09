// Bridge FAKE para testes do Bun (W1/W4) — mesma carcaça do `aegis.webui_bridge`:
// lê 1 comando/linha no stdin, emite 1 frame/linha no stdout.
import readline from "node:readline";

const rl = readline.createInterface({ input: process.stdin });

rl.on("line", (linha) => {
  let c;
  try {
    c = JSON.parse(linha);
  } catch {
    return;
  }
  if (c.cmd === "ping") {
    console.log(JSON.stringify({ cmd: "pong" }));
  } else if (c.cmd === "mensagem") {
    // turno curto: token + tool + fim (determinístico)
    const j = c.job_id;
    console.log(JSON.stringify({ job_id: j, kind: "token", texto: "oi", cumulativo: "oi" }));
    console.log(JSON.stringify({ job_id: j, kind: "tool_inicio", id: "r1", nome: "ler_arquivo", args: { caminho: "artefatos/x.txt" } }));
    console.log(JSON.stringify({ job_id: j, kind: "tool_fim", id: "r1", nome: "ler_arquivo", saida: "conteudo", duracao_ms: 12 }));
    console.log(JSON.stringify({ job_id: j, kind: "arquivo", acao: "escrever", caminho: "artefatos/x.txt", diff: "@@ -0,0 +1 @@\n+oi", status: "ok" }));
    console.log(JSON.stringify({ job_id: j, kind: "comando", cmd: "git status", status: "ok", duracao_ms: 12, resumo: "branch master", confirmado: false }));
    console.log(JSON.stringify({ job_id: j, kind: "subgrafo", nome: "sub_programacao", evento: "start", nivel: 1, tipo: "multiagente" }));
    console.log(JSON.stringify({ job_id: j, kind: "subgrafo", nome: "sub_programacao", evento: "end", nivel: 1, tipo: "multiagente" }));
    console.log(JSON.stringify({ job_id: j, kind: "veredito", veredito: { dominio: "programacao", status: "aprovado", nota: 8.5 } }));
    console.log(JSON.stringify({ job_id: j, kind: "fim", estado_final: { mensagens: [{ content: "oi" }] } }));
  } else if (c.cmd === "historico") {
    console.log(JSON.stringify({ cmd: "historico", threads: [{ thread_id: "web-1", mensagens: 2 }] }));
  } else if (c.cmd === "estado") {
    console.log(JSON.stringify({ cmd: "estado", dados: { versao: "0.11.0", n_ferramentas: 47 } }));
  }
});