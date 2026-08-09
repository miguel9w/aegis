/**
 * Ponte Python — processo persistente que executa o LangGraph e fala JSONL.
 *
 * O Bun spawna `python -m aegis.webui_bridge` (1 comando por linha no stdin,
 * 1 frame por linha no stdout) e roteia os frames para os consumidores
 * (SSE por job_id no W4). `comando` é injetável nos testes (bridge fake).
 */
import { spawn, type Subprocess } from "bun";

export interface Frame {
  job_id?: string;
  cmd?: string;
  kind?: string;
  [chave: string]: unknown;
}

export interface OpcoesPonte {
  comando?: string[];
  aoFechar?: (codigo: number | null, sinal: string | null) => void;
}

function caminhoPython(): string {
  const env = process.env.AEGIS_PYTHON;
  if (env) return env;
  const raiz = process.env.PIXI_PROJECT_ROOT;
  if (raiz) return `${raiz}/.pixi/envs/default/bin/python`;
  return "python";
}

export class Ponte {
  proc: Subprocess | null = null;
  private ouvintes: Array<(f: Frame) => void> = [];
  private filaPings: Array<(ok: boolean) => void> = [];

  constructor(private opcoes: OpcoesPonte = {}) {}

  /** Inicia o processo (idempotente). */
  iniciar(): this {
    if (this.proc) return this;
    const comando = this.opcoes.comando ?? [caminhoPython(), "-m", "aegis.webui_bridge"];
    this.proc = spawn({
      cmd: comando,
      stdout: "pipe",
      stderr: "pipe",
      stdin: "pipe",
      env: { ...process.env as Record<string, string> },
    });
    this.proc.exited.then((codigo) => {
      this.opcoes.aoFechar?.(codigo, null);
      this.proc = null;
      this.filaPings.forEach((fn) => fn(false));
      this.filaPings = [];
    });
    // stderr → log do servidor (diagnóstico sem quebrar o stream)
    (async () => {
      for await (const linha of this.proc!.stderr!.values()) {
        console.error("[ponte]", new TextDecoder().decode(linha).trimEnd());
      }
    })();
    // stdout → 1 linha = 1 frame JSON
    const rl = this.proc.stdout!;
    const dec = new TextDecoder();
    let buffer = "";
    (async () => {
      for await (const pedaco of rl.values()) {
        buffer += dec.decode(pedaco, { stream: true });
        let nl = buffer.indexOf("\n");
        while (nl >= 0) {
          const linha = buffer.slice(0, nl).trim();
          buffer = buffer.slice(nl + 1);
          if (linha) this.emitir(linha);
          nl = buffer.indexOf("\n");
        }
      }
    })();
    return this;
  }

  private emitir(linha: string): void {
    let f: Frame | null = null;
    try {
      f = JSON.parse(linha) as Frame;
    } catch {
      console.error("[ponte] linha não-JSON ignorada:", linha.slice(0, 200));
      return;
    }
    if (f.cmd === "pong") {
      const fn = this.filaPings.shift();
      fn?.(true);
      return;
    }
    for (const o of this.ouvintes) o(f);
  }

  /** Envia um comando e resolve com a próxima resposta do mesmo cmd (estado/historico). */
  comandar(obj: Record<string, unknown>, timeoutMs = 4_000): Promise<Frame | null> {
    return new Promise((resolver) => {
      const cmd = obj.cmd as string;
      const timer = setTimeout(() => {
        this.ouvintes = this.ouvintes.filter((o) => o !== ouvinte);
        resolver(null);
      }, timeoutMs);
      const ouvinte = (f: Frame) => {
        if (f.cmd !== cmd) return;
        clearTimeout(timer);
        this.ouvintes = this.ouvintes.filter((o) => o !== ouvinte);
        resolver(f as Frame);
      };
      this.ouvintes.push(ouvinte);
      this.enviar(obj);
    });
  }

  /** Envia um comando (objeto → JSONL). */
  enviar(obj: Record<string, unknown>): void {
    if (!this.proc) this.iniciar();
    const payload = JSON.stringify(obj) + "\n";
    const ok = this.proc!.stdin?.write(payload);
    if (!ok) console.error("[ponte] stdin fechado — comando não enviado:", obj.cmd);
  }

  /** Ping com timeout — true se a ponte respondeu pong. */
  ping(timeoutMs = 2000): Promise<boolean> {
    if (!this.proc) this.iniciar();
    return new Promise((resolve) => {
      this.filaPings.push(resolve);
      try {
        this.enviar({ cmd: "ping" });
      } catch {
        resolve(false);
        return;
      }
      setTimeout(() => {
        const i = this.filaPings.indexOf(resolve);
        if (i >= 0) this.filaPings.splice(i, 1);
        resolve(false);
      }, timeoutMs);
    });
  }

  /** Registra consumidor de frames (kind: token/tool_inicio/fim/erro/...). */
  quandoFrame(fn: (f: Frame) => void): void {
    this.ouvintes.push(fn);
  }

  fechar(): void {
    try {
      this.proc?.kill();
    } catch {
      /* processo já encerrado */
    }
  }
}