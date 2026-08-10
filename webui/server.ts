/**
 * Aegis Web UI — servidor Bun (:8788).
 *
 * W4: fila FIFO de jobs, POST /api/mensagem → 202 {job_id},
 * GET /api/stream?job_id= → SSE com :open (HTTP 200 imediato) e keepalive
 * `: ping` (o DeepSeek fica mudo durante o reasoning — sem o keepalive o
 * proxy derruba o stream). server.timeout(req, 0) desliga o idle do Bun.
 */
import { build } from "bun";
import { readdirSync, readFileSync } from "node:fs";
import { extname, join, resolve } from "node:path";
import { Ponte, type Frame } from "./bridge.ts";

export interface JobSSE {
  frames: Frame[];
  escritores: Array<ReadableStreamDefaultController<Uint8Array>>;
  fechado: boolean;
}

export interface OpcoesServidor {
  ponte?: Ponte;
  porta?: number;
  host?: string;
  diretorioWeb?: string;
  intervaloPingMs?: number;
  timeoutComandoMs?: number;
}

const SSE_HEADERS = {
  "Content-Type": "text/event-stream; charset=utf-8",
  "Cache-Control": "no-cache",
  Connection: "keep-alive",
  "X-Accel-Buffering": "no",
};

// ---------------- arquivos do projeto (input `-/arquivo`) ----------------
// Listagem/leitura para o front carregar um arquivo no chat. A raiz do
// projeto é o pai de webui/; nada fora dela nem diretórios de máquina
// (node_modules/.git/.pixi) — e .env — aparecem. Leitura apenas: a escrita
// do agente continua restrita ao sandbox (config.artefatos_dir).

const IGNORAR_DIRS = new Set([
  "node_modules", ".git", ".pixi", "dist", ".hermes", "target",
  "__pycache__", ".venv", "config/dados/artefatos",
]);
const EXT_TEXTO = new Set([
  ".ts", ".tsx", ".js", ".mjs", ".json", ".md", ".txt", ".py", ".toml",
  ".yaml", ".yml", ".html", ".css", ".ron", ".sh", ".rs", ".sql", ".csv",
  ".apf", ".xml",
]);
const MAX_ARQUIVO_CHARS = 40_000;

function listarArquivosTexto(base: string, consulta: string, max = 400): string[] {
  const baseNorm = base.endsWith("/") ? base : `${base}/`;
  const saida: string[] = [];
  const fila = [baseNorm];
  while (fila.length && saida.length < max) {
    const pasta = fila.pop()!;
    let entradas: string[];
    try {
      entradas = readdirSync(pasta, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entrada of entradas) {
      if (entrada.name.startsWith(".") && entrada.name !== ".env.example") continue;
      if (IGNORAR_DIRS.has(entrada.name)) continue;
      const caminho = join(pasta, entrada.name);
      if (entrada.isDirectory()) {
        fila.push(caminho);
      } else if (EXT_TEXTO.has(extname(entrada.name))) {
        const rel = caminho.slice(baseNorm.length);
        if (!consulta || rel.toLowerCase().includes(consulta)) saida.push(rel);
      }
    }
  }
  return saida.sort();
}

function lerArquivoTexto(
  base: string,
  relativo: string,
): { caminho: string; conteudo: string; tamanho: number; truncado: boolean } | null {
  if (!relativo || relativo.includes("\0")) return null;
  if (relativo.split("/").some((parte) => IGNORAR_DIRS.has(parte))) return null;
  const baseNorm = base.endsWith("/") ? base : `${base}/`;
  const alvo = resolve(base, relativo);
  if (!alvo.startsWith(baseNorm)) return null; // traversal fora da raiz
  if (!EXT_TEXTO.has(extname(alvo))) return null;
  try {
    const dados = readFileSync(alvo, "utf-8");
    const truncado = dados.length > MAX_ARQUIVO_CHARS;
    return {
      caminho: relativo,
      conteudo: truncado ? `${dados.slice(0, MAX_ARQUIVO_CHARS)}\n… (${dados.length} caracteres, truncado)` : dados,
      tamanho: dados.length,
      truncado,
    };
  } catch {
    return null;
  }
}

export function criarServidor(opcoes: OpcoesServidor = {}) {
  const ponte = opcoes.ponte ?? new Ponte();
  const dir = new URL(".", import.meta.url).pathname;
  const raizProjeto = new URL("../", import.meta.url).pathname;
  const intervaloPing = opcoes.intervaloPingMs ?? 15_000;
  const timeoutComando = opcoes.timeoutComandoMs ?? 4_000;
  const jobs = new Map<string, JobSSE>();
  const enc = new TextEncoder();

  /** Garante o bundle do front (dev: rebuilda se app.ts mais novo). */
  async function garantirBuild(): Promise<void> {
    const entrada = `${dir}app.ts`;
    const saida = `${dir}public/dist/app.js`;
    try {
      const estAtual = Bun.fs.statSync(entrada);
      const estDist = Bun.fs.statSync(saida);
      if (estDist.mtime >= estAtual.mtime) return;
    } catch { /* dist ausente → build */ }
    const r = await build({
      entrypoints: [entrada], outdir: `${dir}public/dist`,
      minify: true, sourcemap: "none",
    });
    if (!r.outputs.length) throw new Error("bun build falhou sem outputs");
  }

  function fecharJob(jobId: string) {
    const job = jobs.get(jobId);
    if (!job || job.fechado) return;
    job.fechado = true;
    for (const w of job.escritores) {
      try { w.close(); } catch { /* cliente já desconectou */ }
    }
    job.escritores = [];
  }

  ponte.quandoFrame((f) => {
    const jobId = f.job_id;
    if (!jobId) return;
    let job = jobs.get(jobId);
    if (!job) {
      job = { frames: [], escritores: [], fechado: false };
      jobs.set(jobId, job);
    }
    job.frames.push(f);
    const dados = `data: ${JSON.stringify(f)}\n\n`;
    for (const w of job.escritores) {
      try { w.enqueue(enc.encode(dados)); } catch { /* cliente desconectou */ }
    }
    if (f.kind === "fim" || f.kind === "erro") fecharJob(jobId);
  });

  const servidor = Bun.serve({
    hostname: opcoes.host ?? process.env.AEGIS_WEBUI_HOST ?? "127.0.0.1",
    port: opcoes.porta ?? Number(process.env.AEGIS_WEBUI_PORT ?? 8788),
    async fetch(req, server) {
      const url = new URL(req.url);
      const caminho = url.pathname;

      if (caminho === "/" || caminho === "/index.html") {
        await garantirBuild();
        const html = await Bun.file(`${dir}index.html`).text();
        return new Response(html, {
          headers: { "Content-Type": "text/html; charset=utf-8" },
        });
      }

      // vendor (deps do node_modules: katex, mermaid) — imutável, cache longo
      if (caminho.startsWith("/vendor/")) {
        const resto = caminho.slice("/vendor/".length);
        // aliases das deps + fontes do katex (o CSS usa url(fonts/…) relativo)
        const relativo = resto === "katex.min.js" ? "katex/dist/katex.min.js"
          : resto === "katex.min.css" ? "katex/dist/katex.min.css"
          : resto === "mermaid.min.js" ? "mermaid/dist/mermaid.min.js"
          : resto.startsWith("fonts/") ? `katex/dist/${resto}` : "";
        if (relativo) {
          const arquivo = Bun.file(`${dir}node_modules/${relativo}`);
          if (await arquivo.exists()) {
            const tipo = arquivo.name!.endsWith(".js") ? "application/javascript"
              : arquivo.name!.endsWith(".css") ? "text/css"
              : arquivo.name!.endsWith(".woff2") ? "font/woff2" : "application/octet-stream";
            return new Response(arquivo, {
              headers: { "Content-Type": tipo, "Cache-Control": "public, max-age=31536000, immutable" },
            });
          }
        }
      }

      // estáticos do front (dist foi gerado por bun build no dev/boot)
      if (caminho === "/app.js" || caminho === "/style.css" || caminho.startsWith("/dist/")
        || /\.(css|js|svg|woff2?|map)$/.test(caminho)) {
        const relativo = caminho === "/app.js" ? "public/dist/app.js"
          : caminho === "/style.css" ? "style.css" : caminho.slice(1);
        const arquivo = Bun.file(`${dir}${relativo}`);
        if (await arquivo.exists()) {
          const tipo = relativo.endsWith(".js") ? "application/javascript"
            : relativo.endsWith(".css") ? "text/css" : "application/octet-stream";
          return new Response(arquivo, {
            headers: { "Content-Type": tipo, "Cache-Control": "no-store" },
          });
        }
        if (caminho === "/style.css") {
          // CSS embutido no index.html — rota lógica por compatibilidade
          return new Response("", { headers: { "Content-Type": "text/css" } });
        }
        return new Response("não encontrado", { status: 404 });
      }

      if (caminho === "/api/healthz") {
        const ponteOk = await ponte.ping(2_000);
        return Response.json({
          status: "ok", bun: Bun.version,
          ponte: ponteOk ? "ok" : "morto",
          jobs: jobs.size,
        });
      }

      if (caminho === "/api/estado" || caminho === "/api/historico") {
        const cmd = caminho.endsWith("estado") ? "estado" : "historico";
        const resp = await ponte.comandar({ cmd }, timeoutComando);
        const dados = resp?.dados ?? (resp?.threads ? { threads: resp.threads } : null);
        return Response.json(dados ?? { erro: "ponte sem resposta" });
      }

      // sugestões do input (comandos `/`, agentes `@`) — vem da ponte real
      if (caminho === "/api/sugestoes") {
        const resp = await ponte.comandar({ cmd: "sugestoes" }, timeoutComando);
        return Response.json(
          resp?.dados ?? { comandos: [], agentes: [], prompts: [], papeis: [] },
        );
      }

      if (caminho === "/api/slash" && req.method === "POST") {
        const corpo = await req.json().catch(() => null);
        const nome = typeof corpo?.nome === "string" ? corpo.nome.trim() : "";
        if (!nome) {
          return Response.json({ erro: "campo 'nome' obrigatório" }, { status: 400 });
        }
        const resp = await ponte.comandar(
          { cmd: "slash", nome, arg: String(corpo?.arg ?? "") },
          timeoutComando,
        );
        return Response.json({ texto: resp?.texto ?? "sem resposta da ponte" });
      }

      // arquivos do projeto (input `-/arquivo`): lista + leitura de texto
      if (caminho === "/api/arquivos") {
        const consulta = (url.searchParams.get("q") ?? "").toLowerCase();
        return Response.json({ arquivos: listarArquivosTexto(raizProjeto, consulta) });
      }

      if (caminho === "/api/arquivo") {
        const relativo = url.searchParams.get("caminho") ?? "";
        const arquivo = lerArquivoTexto(raizProjeto, relativo);
        if (!arquivo) {
          return Response.json(
            { erro: "arquivo não encontrado, fora do projeto ou não é texto" },
            { status: 404 },
          );
        }
        return Response.json(arquivo);
      }

      if (caminho === "/api/mensagem" && req.method === "POST") {
        const corpo = await req.json().catch(() => null);
        const texto = typeof corpo?.texto === "string" ? corpo.texto.trim() : "";
        if (!texto) {
          return Response.json({ erro: "campo 'texto' obrigatório" }, { status: 400 });
        }
        const jobId = `j-${crypto.randomUUID().slice(0, 8)}`;
        const threadId = typeof corpo?.thread_id === "string" ? corpo.thread_id : "default";
        const dominio = typeof corpo?.dominio === "string" ? corpo.dominio.trim() : "";
        jobs.set(jobId, { frames: [], escritores: [], fechado: false });
        ponte.enviar({
          cmd: "mensagem", job_id: jobId, texto, thread_id: threadId,
          ...(dominio ? { dominio } : {}),
        });
        return Response.json({ job_id: jobId, thread_id: threadId }, { status: 202 });
      }

      if (caminho === "/api/interromper" && req.method === "POST") {
        const corpo = await req.json().catch(() => null);
        const jobId = typeof corpo?.job_id === "string" ? corpo.job_id : "";
        const job = jobId ? jobs.get(jobId) : undefined;
        if (!job || job.fechado) {
          return Response.json(
            { ok: false, erro: "job inexistente ou já concluído" },
            { status: 404 },
          );
        }
        // cancela na ponte; o turno encerra quando ela emitir `fim` com
        // interrompido=true (o SSE então fecha sozinho)
        const resp = await ponte.comandar({ cmd: "interromper", job_id: jobId }, timeoutComando);
        return Response.json({ ok: resp?.ok === true, job_id: jobId });
      }

      if (caminho === "/api/autorizar" && req.method === "POST") {
        const corpo = await req.json().catch(() => null);
        const comando = typeof corpo?.comando === "string" ? corpo.comando.trim() : "";
        if (!comando) {
          return Response.json({ ok: false, erro: "campo 'comando' obrigatório" },
            { status: 400 });
        }
        // aprovado na sessão — o turno reenviado executa sem confirmar=True
        const resp = await ponte.comandar({ cmd: "autorizar", comando }, timeoutComando);
        return Response.json({ ok: resp?.ok === true, comando });
      }

      if (caminho === "/api/stream") {
        // SSE: sem idle timeout do Bun (derrubaria stream quieto)
        try { server.timeout(req, 0); } catch { /* API tolerante */ }
        const jobId = url.searchParams.get("job_id") ?? "";
        if (!jobId) {
          return Response.json({ erro: "job_id obrigatório" }, { status: 400 });
        }
        let job = jobs.get(jobId);
        if (!job) {
          job = { frames: [], escritores: [], fechado: false };
          jobs.set(jobId, job);
        }
        const stream = new ReadableStream<Uint8Array>({
          start(c) {
            job!.escritores.push(c);
            c.enqueue(enc.encode(": open\n\n")); // primeiro byte — HTTP 200 já visível
            for (const f of job!.frames) {
              c.enqueue(enc.encode(`data: ${JSON.stringify(f)}\n\n`));
            }
            if (job!.fechado) {
              try { c.close(); } catch { /* já fechado */ }
            }
          },
          cancel() {
            if (job) {
              job.escritores = job.escritores.filter((w) => w !== (stream as any).controller);
            }
          },
        });
        return new Response(stream, { headers: SSE_HEADERS });
      }

      return Response.json({ erro: "rota não encontrada" }, { status: 404 });
    },
  });

  // keepalive: `: ping` nos jobs com cliente conectado e sem frames recentes
  const pingJobs = setInterval(() => {
    for (const job of jobs.values()) {
      if (job.fechado || job.escritores.length === 0) continue;
      for (const w of job.escritores) {
        try { w.enqueue(enc.encode(`: ping\n\n`)); } catch { /* cliente caiu */ }
      }
    }
  }, intervaloPing);

  const stopOriginal = servidor.stop.bind(servidor);
  (servidor as unknown as { stop: (opts?: unknown) => void }).stop = (opts?: unknown) => {
    clearInterval(pingJobs);
    for (const job of jobs.values()) fecharJob(job.job_id ?? "");
    return stopOriginal(opts);
  };
  void jobs;
  return servidor;
}

// Execução direta: `pixi run webui`
if (import.meta.main) {
  const servidor = criarServidor();
  console.log(`🌐 Aegis Web UI → http://${servidor.hostname}:${servidor.port}`);
  console.log(`   ponte python: ${process.env.PIXI_PROJECT_ROOT ?? "."}/.pixi/envs/default/bin/python -m aegis.webui_bridge`);
}