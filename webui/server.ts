/**
 * Aegis Web UI — servidor Bun (:8788).
 *
 * W1: serve o front, /api/healthz e gerencia o ciclo de vida da ponte Python.
 * W4+: fila FIFO de jobs, POST /api/mensagem → 202 job_id, GET /api/stream
 * (SSE com :open + :ping), descarte de órfãos e reinício da ponte.
 */
import { Ponte } from "./bridge.ts";

export interface OpcoesServidor {
  ponte?: Ponte;
  porta?: number;
  host?: string;
  diretorioWeb?: string;
}

export function criarServidor(opcoes: OpcoesServidor = {}) {
  const ponte = opcoes.ponte ?? new Ponte();
  const dir = opcoes.diretorioWeb ?? new URL(".", import.meta.url).pathname;
  const host = opcoes.host ?? process.env.AEGIS_WEBUI_HOST ?? "127.0.0.1";
  const porta = opcoes.porta ?? Number(process.env.AEGIS_WEBUI_PORT ?? 8788);

  const html = awaitCache(() => Bun.file(`${dir}/index.html`).text());

  const servidor = Bun.serve({
    hostname: host,
    port: porta,
    async fetch(req) {
      const url = new URL(req.url);

      // Front
      if (url.pathname === "/" || url.pathname === "/index.html") {
        return new Response(await html(), {
          headers: { "Content-Type": "text/html; charset=utf-8" },
        });
      }

      // Saúde: status da ponte em tempo real (ping com timeout)
      if (url.pathname === "/api/healthz") {
        const ponteOk = await ponte.ping(2000);
        return Response.json({
          status: "ok",
          bun: Bun.version,
          ponte: ponteOk ? "ok" : "morto",
        });
      }

      return Response.json({ erro: "rota não encontrada" }, { status: 404 });
    },
  });

  return { servidor, ponte };
}

/** Cache simples de leitura de arquivo (evita re-ler o HTML a cada request). */
function awaitCache(fn: () => Promise<string>): () => Promise<string> {
  let valor: string | null = null;
  return async () => {
    if (valor === null) valor = await fn();
    return valor;
  };
}

// Execução direta: `pixi run webui`
if (import.meta.main) {
  const { servidor } = criarServidor();
  console.log(`🌐 Aegis Web UI → http://${servidor.hostname}:${servidor.port}`);
  console.log("   encerre com Ctrl+C; a ponte Python é gerenciada pelo servidor.");
  process.on("SIGINT", () => process.exit(0));
}