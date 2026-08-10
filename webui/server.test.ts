/**
 * Testes do servidor web — ponte FAKE injetada (sem rede/LLM).
 * W4: prova o pipeline POST /api/mensagem → 202 job_id → SSE entrega os
 * frames do protocolo até o fim (token → tool → … → fim).
 */
import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { criarServidor } from "./server.ts";
import { Ponte } from "./bridge.ts";

let servidor: ReturnType<typeof criarServidor>;
let ponte: Ponte;
let base: string;

beforeAll(() => {
  const bunBin = process.env.BUN_BIN ?? "bun";
  ponte = new Ponte({ comando: [bunBin, "webui/fixtures/bridge_fake.mjs"] });
  servidor = criarServidor({ ponte, porta: 0, intervaloPingMs: 500 });
  const endereco = servidor.url;
  base = endereco.href.replace(/\/$/, "");
});

afterAll(() => {
  servidor.stop();
  ponte.fechar();
});

describe("W1 — estático e saúde", () => {
  test("GET / serve o HTML", async () => {
    const res = await fetch(`${base}/`);
    expect(res.status).toBe(200);
    expect(await res.text()).toContain("<title>Aegis Web UI</title>");
  });

  test("GET /api/healthz responde com ponte ok", async () => {
    const res = await fetch(`${base}/api/healthz`);
    expect(res.status).toBe(200);
    const corpo = (await res.json()) as { ponte: string; status: string };
    expect(corpo.status).toBe("ok");
    expect(corpo.ponte).toBe("ok"); // fake respondeu pong
  });
});

describe("W4 — fila de jobs e SSE", () => {
  test("POST /api/mensagem → 202 com job_id; SSE entrega frames até o fim", async () => {
    const res = await fetch(`${base}/api/mensagem`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ texto: "oi" }),
    });
    expect(res.status).toBe(202);
    const { job_id: jobId } = (await res.json()) as { job_id: string };
    expect(jobId).toMatch(/^j-/);

    // abre o SSE e consome até o fim
    const sse = await fetch(`${base}/api/stream?job_id=${jobId}`);
    expect(sse.status).toBe(200);
    expect(sse.headers.get("content-type")).toContain("text/event-stream");
    let corpo = "";
    for await (const pedaco of sse.body!) {
      corpo += new TextDecoder().decode(pedaco);
    }
    // o fake emite: token → tool_inicio → tool_fim → arquivo → comando →
    // subgrafo → veredito → fim
    expect(corpo).toContain(`"job_id":"${jobId}"`);
    expect(corpo).toContain('"kind":"token"');
    expect(corpo).toContain('"kind":"tool_inicio"');
    expect(corpo).toContain('"kind":"arquivo"');
    expect(corpo).toContain('"kind":"veredito"');
    expect(corpo).toContain('"kind":"fim"');
  });

  test("POST sem texto → 400", async () => {
    const res = await fetch(`${base}/api/mensagem`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    expect(res.status).toBe(400);
  });

  test("GET /api/estado responde via ponte (comandar)", async () => {
    const res = await fetch(`${base}/api/estado`);
    expect(res.status).toBe(200);
    const corpo = (await res.json()) as { versao: string };
    expect(corpo.versao).toBeTruthy();
  });

  test("GET /api/historico responde threads via ponte", async () => {
    const res = await fetch(`${base}/api/historico`);
    expect(res.status).toBe(200);
    const corpo = (await res.json()) as { threads: Array<{ thread_id: string }> };
    expect(corpo.threads.length).toBeGreaterThan(0);
  });
});

describe("W5b — interromper e autorizar", () => {
  test("interromper job inexistente → 404", async () => {
    const res = await fetch(`${base}/api/interromper`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: "j-inexistente" }),
    });
    expect(res.status).toBe(404);
    expect(((await res.json()) as { ok: boolean }).ok).toBe(false);
  });

  test("interromper cancela turno em andamento (fake lento) e o SSE fecha com fim interrompido", async () => {
    // ponte fake LENTA (40ms/frame) + servidor próprio, para dar tempo de cancelar
    const ponteLenta = new Ponte({
      comando: [process.env.BUN_BIN ?? "bun", "webui/fixtures/bridge_fake.mjs"],
      env: { ...process.env, FAKE_DELAY_MS: "40" },
    });
    const servidorLento = criarServidor({ ponte: ponteLenta, porta: 0, intervaloPingMs: 500 });
    const baseLenta = servidorLento.url.href.replace(/\/$/, "");
    try {
      const res = await fetch(`${baseLenta}/api/mensagem`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ texto: "conte devagar" }),
      });
      const { job_id: jobId } = (await res.json()) as { job_id: string };

      // consome o SSE em paralelo
      const sse = await fetch(`${baseLenta}/api/stream?job_id=${jobId}`);
      const leitura = (async () => {
        let corpo = "";
        for await (const pedaco of sse.body!) {
          corpo += new TextDecoder().decode(pedaco);
        }
        return corpo;
      })();

      // espera alguns frames saírem, então interrompe
      await Bun.sleep(120);
      const parar = await fetch(`${baseLenta}/api/interromper`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job_id: jobId }),
      });
      expect(parar.status).toBe(200);
      expect(((await parar.json()) as { ok: boolean }).ok).toBe(true);

      const corpo = await leitura;
      // saíram frames reais antes do cancelamento…
      expect(corpo).toContain('"kind":"token"');
      // …e o stream FECHOU com o fim interrompido (não com o fim normal)
      expect(corpo).toContain('"interrompido":true');
      expect(corpo).toContain('"kind":"fim"');
    } finally {
      servidorLento.stop();
      ponteLenta.fechar();
    }
  });

  test("POST /api/autorizar aprova comando na ponte", async () => {
    const res = await fetch(`${base}/api/autorizar`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ comando: "git status" }),
    });
    expect(res.status).toBe(200);
    expect(((await res.json()) as { ok: boolean }).ok).toBe(true);
  });

  test("POST /api/autorizar sem comando → 400", async () => {
    const res = await fetch(`${base}/api/autorizar`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    expect(res.status).toBe(400);
  });
});