// Bridge FAKE para testes do Bun (W1/W4/W5b) — mesma carcaça do
// `aegis.webui_bridge`: lê 1 comando/linha no stdin, emite 1 frame/linha
// no stdout. Suporta:
//   FAKE_DELAY_MS  — atraso entre frames (simula turno longo para testar
//                    o botão interromper);
//   comando "interromper" — para o turno em andamento e emite fim
//                    com interrompido=true (igual à ponte real).
import readline from "node:readline";

const ATRASO = Number(process.env.FAKE_DELAY_MS ?? 0);
const dormir = (ms) => new Promise((r) => setTimeout(r, ms));

const rl = readline.createInterface({ input: process.stdin });

let interrompido = false;

rl.on("line", async (linha) => {
  let c;
  try {
    c = JSON.parse(linha);
  } catch {
    return;
  }
  if (c.cmd === "ping") {
    console.log(JSON.stringify({ cmd: "pong" }));
  } else if (c.cmd === "interromper") {
    interrompido = true;
    console.log(JSON.stringify({ cmd: "interromper", ok: true }));
  } else if (c.cmd === "mensagem") {
    // turno curto: token + tool + arquivo + comando + subgrafo + veredito + fim
    const j = c.job_id;
    const emite = (f) => console.log(JSON.stringify({ job_id: j, ...f }));
    const passos = [
      { kind: "token", texto: "oi", cumulativo: "oi" },
      { kind: "tool_inicio", id: "r1", nome: "ler_arquivo", args: { caminho: "artefatos/x.txt" } },
      { kind: "tool_fim", id: "r1", nome: "ler_arquivo", saida: "conteudo", duracao_ms: 12 },
      { kind: "arquivo", acao: "escrever", caminho: "artefatos/x.txt", diff: "@@ -0,0 +1 @@\n+oi", status: "ok" },
      { kind: "comando", cmd: "git status", status: "ok", duracao_ms: 12, resumo: "branch master", confirmado: false },
      { kind: "subgrafo", nome: "sub_programacao", evento: "start", nivel: 1, tipo: "multiagente" },
      { kind: "subgrafo", nome: "sub_programacao", evento: "end", nivel: 1, tipo: "multiagente" },
      { kind: "veredito", veredito: { dominio: "programacao", status: "aprovado", nota: 8.5 } },
    ];
    for (const f of passos) {
      if (interrompido) break;
      emite(f);
      if (ATRASO) await dormir(ATRASO);
    }
    if (interrompido) {
      emite({ kind: "fim", texto: "(turno interrompido pelo usuário)", estado_final: null, interrompido: true });
    } else {
      emite({ kind: "fim", estado_final: { mensagens: [{ content: "oi" }] } });
    }
  } else if (c.cmd === "historico") {
    console.log(JSON.stringify({ cmd: "historico", threads: [{ thread_id: "web-1", mensagens: 2 }] }));
  } else if (c.cmd === "estado") {
    console.log(JSON.stringify({ cmd: "estado", dados: { versao: "0.11.0", n_ferramentas: 47 } }));
  } else if (c.cmd === "autorizar") {
    console.log(JSON.stringify({ cmd: "autorizar", ok: true, comando: c.comando ?? "" }));
  } else if (c.cmd === "sugestoes") {
    console.log(JSON.stringify({
      cmd: "sugestoes",
      dados: {
        comandos: [{ nome: "ajuda", descricao: "Mostra a lista de comandos" },
                   { nome: "prompt", descricao: "[id|nenhum] — ativa/mostra o prompt avançado (APF)" }],
        agentes: [{ nome: "programacao", descricao: "subgrafo multiagente" }],
        prompts: [{ id: "revisor-codigo", versao: "1", descricao: "revisão de código" }],
        papeis: [{ nome: "cientista", descricao: "papel de pesquisa" }],
      },
    }));
  } else if (c.cmd === "slash") {
    console.log(JSON.stringify({ cmd: "slash", nome: c.nome ?? "", texto: `(slash fake) ${(c.nome ?? "")} ${c.arg ?? ""}` }));
  }
});