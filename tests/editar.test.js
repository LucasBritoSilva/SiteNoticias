// Testes da lógica de docs/editar.js com um "fetch" simulado (nenhuma chamada real ao GitHub).
// Execução: node tests/editar.test.js
const assert = require("node:assert/strict");
const path = require("node:path");
const Nichos = require(path.join(__dirname, "..", "docs", "editar.js"));

const REPO = "dono/repo";
const TOKEN = "github_pat_TOKEN_DE_TESTE";
const URL_ARQ = `https://api.github.com/repos/${REPO}/contents/nichos.json`;

function resposta(status, corpo) {
  return { ok: status >= 200 && status < 300, status, json: async () => corpo };
}

function arquivo(raw, sha) {
  return resposta(200, { sha, content: Nichos.paraBase64(raw) });
}

/** Simula a API: `roteiro` é a lista de respostas na ordem das chamadas. */
function falso(roteiro) {
  const chamadas = [];
  const f = async (url, opcoes = {}) => {
    chamadas.push({ url, metodo: opcoes.method || "GET", headers: opcoes.headers, body: opcoes.body });
    if (!roteiro.length) throw new Error("chamada inesperada: " + url);
    return roteiro.shift();
  };
  f.chamadas = chamadas;
  return f;
}

const sem_espera = async () => {};
const RAW0 = Nichos.serializar(["economia"]);
const testes = [];
const teste = (nome, fn) => testes.push([nome, fn]);

teste("analisarLinhas limpa, remove vazios e repetidos", () => {
  assert.deepEqual(Nichos.analisarLinhas("  IA \r\n\nia\n  inteligência   artificial \n"), ["IA", "inteligência artificial"]);
});

teste("validarLista", () => {
  assert.throws(() => Nichos.validarLista([]), /pelo menos um/);
  assert.throws(() => Nichos.validarLista(Array.from({ length: 11 }, (_, i) => "n" + i)), /no máximo/);
  assert.throws(() => Nichos.validarLista(["x".repeat(101)]), /100 caracteres/);
  Nichos.validarLista(["ok"]);
});

teste("repoDaUrl", () => {
  assert.equal(Nichos.repoDaUrl({ hostname: "lucas.github.io", pathname: "/MeuSite/editar.html" }), "lucas/MeuSite");
  assert.equal(Nichos.repoDaUrl({ hostname: "lucas.github.io", pathname: "/editar.html" }), "lucas/lucas.github.io");
  assert.equal(Nichos.repoDaUrl({ hostname: "localhost", pathname: "/editar.html" }), "");
});

teste("base64 preserva acentos", () => {
  const t = "inteligência artificial — ação\n";
  assert.equal(Nichos.deBase64(Nichos.paraBase64(t)), t);
});

teste("salvar: lê o SHA atual e faz PUT com token, sha, branch e conteúdo corretos", async () => {
  const f = falso([arquivo(RAW0, "sha1"), resposta(200, { content: { sha: "sha2" } })]);
  const r = await Nichos.salvar({ fetch: f, repo: REPO, token: TOKEN, lista: ["inteligência artificial", "economia"], baseRaw: RAW0, esperar: sem_espera });
  assert.equal(r.alterado, true);
  const [get, put] = f.chamadas;
  assert.equal(get.metodo, "GET");
  assert.equal(get.url, URL_ARQ + "?ref=main");
  assert.equal(put.metodo, "PUT");
  assert.equal(put.url, URL_ARQ);
  assert.equal(put.headers.Authorization, "Bearer " + TOKEN);
  const corpo = JSON.parse(put.body);
  assert.equal(corpo.sha, "sha1");
  assert.equal(corpo.branch, "main");
  assert.deepEqual(JSON.parse(Nichos.deBase64(corpo.content)), { nichos: ["inteligência artificial", "economia"] });
  assert.ok(!put.body.includes(TOKEN), "o token não deve ir no corpo");
  assert.ok(!put.url.includes(TOKEN) && !get.url.includes(TOKEN), "o token não deve ir na URL");
});

teste("salvar: 409 (branch andou) -> relê o SHA e repete, sem perder a edição", async () => {
  const f = falso([arquivo(RAW0, "sha1"), resposta(409, { message: "does not match" }), arquivo(RAW0, "sha1b"), resposta(200, { content: { sha: "x" } })]);
  const r = await Nichos.salvar({ fetch: f, repo: REPO, token: TOKEN, lista: ["a"], baseRaw: RAW0, esperar: sem_espera });
  assert.equal(r.alterado, true);
  assert.equal(JSON.parse(f.chamadas[3].body).sha, "sha1b");
});

teste("salvar: se o arquivo mudou por outra pessoa, NÃO grava", async () => {
  const outro = Nichos.serializar(["economia", "esportes"]);
  const f = falso([arquivo(outro, "shaX")]);
  await assert.rejects(
    Nichos.salvar({ fetch: f, repo: REPO, token: TOKEN, lista: ["a"], baseRaw: RAW0, esperar: sem_espera }),
    (e) => e.name === "ErroConflito" && /mudou no GitHub/.test(e.message)
  );
  assert.equal(f.chamadas.filter((c) => c.metodo === "PUT").length, 0);
});

teste("salvar: conflito persistente desiste depois de 4 tentativas", async () => {
  const roteiro = [];
  for (let i = 0; i < 4; i++) roteiro.push(arquivo(RAW0, "s" + i), resposta(409, {}));
  const f = falso(roteiro);
  await assert.rejects(Nichos.salvar({ fetch: f, repo: REPO, token: TOKEN, lista: ["a"], baseRaw: RAW0, esperar: sem_espera }), /conflito/);
  assert.equal(f.chamadas.filter((c) => c.metodo === "PUT").length, 4);
});

teste("salvar: sem alteração não faz PUT", async () => {
  const f = falso([arquivo(RAW0, "sha1")]);
  const r = await Nichos.salvar({ fetch: f, repo: REPO, token: TOKEN, lista: ["economia"], baseRaw: RAW0, esperar: sem_espera });
  assert.equal(r.alterado, false);
  assert.equal(f.chamadas.length, 1);
});

teste("salvar: erros de token e de permissão têm mensagem clara", async () => {
  await assert.rejects(
    Nichos.salvar({ fetch: falso([resposta(401, {})]), repo: REPO, token: TOKEN, lista: ["a"], baseRaw: RAW0 }),
    /Token recusado/
  );
  await assert.rejects(
    Nichos.salvar({ fetch: falso([arquivo(RAW0, "s"), resposta(403, {})]), repo: REPO, token: TOKEN, lista: ["a"], baseRaw: RAW0, esperar: sem_espera }),
    /Contents \(leitura e escrita\)/
  );
});

teste("salvar: exige lista carregada e lista válida", async () => {
  await assert.rejects(Nichos.salvar({ fetch: falso([]), repo: REPO, token: TOKEN, lista: ["a"], baseRaw: null }), /Carregue a lista/);
  await assert.rejects(Nichos.salvar({ fetch: falso([]), repo: REPO, token: TOKEN, lista: [], baseRaw: RAW0 }), /pelo menos um/);
});

teste("lerArquivo aceita lista simples e ignora itens inválidos", async () => {
  const f = falso([arquivo(JSON.stringify(["a", "", 3, "b"]), "s")]);
  const r = await Nichos.lerArquivo(f, REPO, "");
  assert.deepEqual(r.lista, ["a", "b"]);
  assert.equal(f.chamadas[0].headers.Authorization, undefined); // leitura pública sem token
});

(async () => {
  let falhas = 0;
  for (const [nome, fn] of testes) {
    try {
      await fn();
      console.log("ok   -", nome);
    } catch (e) {
      falhas++;
      console.log("FALHA -", nome, "\n", e.message);
    }
  }
  console.log(`${testes.length - falhas}/${testes.length} testes passaram`);
  process.exit(falhas ? 1 : 0);
})();
