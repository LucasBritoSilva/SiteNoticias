/* Edição de nichos.json pela API de conteúdo do GitHub.
 * O token é digitado pelo usuário e guardado só no localStorage deste navegador
 * (ver editar.html). Nada aqui contém token nem é enviado para outro lugar que não
 * api.github.com. */
(function (raiz) {
  "use strict";

  var API = "https://api.github.com";
  var ARQUIVO = "nichos.json";
  var BRANCH = "main";
  var MAX_NICHOS = 10;

  function ErroApi(mensagem, status) {
    var e = new Error(mensagem);
    e.name = "ErroApi";
    e.status = status;
    return e;
  }

  function ErroConflito(atual) {
    var e = new Error(
      "O arquivo nichos.json mudou no GitHub desde que você abriu esta página. " +
        "Clique em “Recarregar do GitHub”, confira a lista e salve de novo."
    );
    e.name = "ErroConflito";
    e.atual = atual;
    return e;
  }

  /* Uma linha por nicho; remove vazios e repetidos (sem diferenciar maiúsculas). */
  function analisarLinhas(texto) {
    var vistos = {};
    var lista = [];
    String(texto || "").split(/\r?\n/).forEach(function (linha) {
      var t = linha.replace(/\s+/g, " ").trim();
      var chave = t.toLocaleLowerCase("pt-BR");
      if (t && !vistos[chave]) {
        vistos[chave] = true;
        lista.push(t);
      }
    });
    return lista;
  }

  function validarLista(lista) {
    if (!lista.length) throw new Error("Informe pelo menos um nicho.");
    if (lista.length > MAX_NICHOS) throw new Error("Use no máximo " + MAX_NICHOS + " nichos.");
    lista.forEach(function (t) {
      if (t.length > 100) throw new Error("Cada nicho deve ter até 100 caracteres.");
    });
  }

  function serializar(lista) {
    return JSON.stringify({ nichos: lista }, null, 2) + "\n";
  }

  function extrairLista(raw) {
    var dados = JSON.parse(raw);
    var lista = Array.isArray(dados) ? dados : dados && dados.nichos;
    if (!Array.isArray(lista)) throw new Error("nichos.json em formato inesperado.");
    return lista.filter(function (t) { return typeof t === "string" && t.trim(); });
  }

  /* "dono/repo" a partir de https://dono.github.io/repo/editar.html */
  function repoDaUrl(loc) {
    var host = String(loc.hostname || "");
    if (!/\.github\.io$/i.test(host)) return "";
    var dono = host.split(".")[0];
    var partes = String(loc.pathname || "").split("/").filter(Boolean);
    var repo = partes.length >= 2 ? partes[0] : host;
    return dono + "/" + repo;
  }

  function paraBase64(texto) {
    var bytes = new TextEncoder().encode(texto);
    var bin = "";
    bytes.forEach(function (b) { bin += String.fromCharCode(b); });
    return btoa(bin);
  }

  function deBase64(b64) {
    var bin = atob(String(b64).replace(/\s/g, ""));
    var bytes = new Uint8Array(bin.length);
    for (var i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
    return new TextDecoder("utf-8").decode(bytes);
  }

  function cabecalhos(token, comCorpo) {
    var h = { Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28" };
    if (token) h.Authorization = "Bearer " + token;
    if (comCorpo) h["Content-Type"] = "application/json";
    return h;
  }

  function mensagemHttp(status, corpo) {
    if (status === 401) return "Token recusado pelo GitHub (inválido ou expirado).";
    if (status === 403 || status === 404)
      return "Sem acesso. Confira o repositório e se o token tem permissão de Contents (leitura e escrita) neste repositório.";
    if (status === 409) return "Conflito ao gravar no GitHub.";
    return "O GitHub respondeu " + status + (corpo && corpo.message ? ": " + corpo.message : ".");
  }

  async function falha(resp) {
    var corpo = null;
    try { corpo = await resp.json(); } catch (e) { /* sem corpo JSON */ }
    return ErroApi(mensagemHttp(resp.status, corpo), resp.status);
  }

  function urlArquivo(repo) {
    return API + "/repos/" + repo + "/contents/" + ARQUIVO;
  }

  /* Lê o arquivo atual: { sha, raw, lista }. */
  async function lerArquivo(f, repo, token) {
    var resp = await f(urlArquivo(repo) + "?ref=" + BRANCH, { headers: cabecalhos(token), cache: "no-store" });
    if (!resp.ok) throw await falha(resp);
    var j = await resp.json();
    var raw = deBase64(j.content);
    return { sha: j.sha, raw: raw, lista: extrairLista(raw) };
  }

  /* Grava a lista. `baseRaw` é o conteúdo que a página carregou: se o arquivo no GitHub for
   * outro, não grava (não sobrescreve a mudança de outra pessoa). Se o GitHub devolver 409
   * porque a branch andou (ex.: o workflow fez commit no mesmo instante), lê o SHA de novo e
   * repete, desde que o arquivo continue igual ao que carregamos. */
  async function salvar(opc) {
    var f = opc.fetch;
    var esperar = opc.esperar || function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
    if (typeof opc.baseRaw !== "string") throw new Error("Carregue a lista atual antes de salvar.");
    validarLista(opc.lista);
    var novoRaw = serializar(opc.lista);
    for (var tentativa = 1; tentativa <= 4; tentativa++) {
      var atual = await lerArquivo(f, opc.repo, opc.token);
      if (atual.raw !== opc.baseRaw) throw new ErroConflito(atual);
      if (atual.raw === novoRaw) return { raw: novoRaw, alterado: false };
      var resp = await f(urlArquivo(opc.repo), {
        method: "PUT",
        headers: cabecalhos(opc.token, true),
        body: JSON.stringify({
          message: "Atualiza nichos pela página",
          content: paraBase64(novoRaw),
          sha: atual.sha,
          branch: BRANCH,
        }),
      });
      if (resp.ok) return { raw: novoRaw, alterado: true };
      if (resp.status !== 409) throw await falha(resp);
      await esperar(tentativa * 700);
    }
    throw ErroApi("O GitHub continuou com conflito após várias tentativas. Tente de novo em instantes.", 409);
  }

  var Nichos = {
    analisarLinhas: analisarLinhas,
    validarLista: validarLista,
    serializar: serializar,
    extrairLista: extrairLista,
    repoDaUrl: repoDaUrl,
    paraBase64: paraBase64,
    deBase64: deBase64,
    lerArquivo: lerArquivo,
    salvar: salvar,
    MAX_NICHOS: MAX_NICHOS,
  };

  if (typeof module !== "undefined" && module.exports) module.exports = Nichos;
  else raiz.Nichos = Nichos;
})(typeof window !== "undefined" ? window : globalThis);
