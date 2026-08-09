/**
 * Testes W1 do servidor web — ponte FAKE injetada (sem rede/LLM).
 * Prova real: o server.ts fala JSONL com o processo fake e responde HTTP.
 */
import { afterAll, beforeAll, describe, expect, test } from "bun:test";
import { criarServidor } from "./server.ts";
import { Ponte } from "./bridge.ts";

const ponte = new Ponte({
  comando: ["bun", "./webui/fixtures/bridge_fake.mjs"],
  aoFechar: (c) => console.error("[teste] ponte fechou com código", c),
});

let servidor: ReturnType<typeof criarServidor>["servidor"];
let base = "";

beforeAll(() => {
  servidor = criarServidor({ ponte, porta: 0, diretorioWeb: "./webui" }).servidor;
  base = `http://127.0.0.1:${servidor.port}`;
});

afterAll(() => {
  ponte.fechar();
  servidor.stop();
});

describe("esqueleto W1", () => {
  test("GET / serve o front (HTML)", async () => {
    const r = await fetch(base + "/");
    expect(r.status).toBe(200);
    const html = await r.text();
    expect(html).toContain("<title>Aegis Web UI</title>");
  });

  test("GET /api/healthz reporta a ponte viva (ping→pong)", async () => {
    const r = await fetch(base + "/api/healthz");
    expect(r.status).toBe(200);
    const d = await r.json();
    expect(d.status).toBe("ok");
    expect(d.ponte).toBe("ok");
  });

  test("rota desconhecida → 404 JSON", async () => {
    const r = await fetch(base + "/nao-existe");
    expect(r.status).toBe(404);
    const d = await r.json();
    expect(d.erro).toBeTruthy();
  });
});