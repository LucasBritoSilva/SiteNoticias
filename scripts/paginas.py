"""Acesso às páginas originais das matérias: URL real, trecho de texto e imagem (og:image).

Respeita o robots.txt: se o site proíbe a coleta (ou responde 401/403), não pegamos nada dele
e o cartão usa a imagem genérica.
"""
from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from coleta import Artigo

log = logging.getLogger(__name__)

AGENTE = "SiteNoticiasBot"
USER_AGENT = f"{AGENTE}/1.0"
TIMEOUT = 15
MAX_HTML = 2_000_000
MAX_IMAGEM = 5_000_000
MAX_TRECHO = 2500
TRABALHADORES = 6  # leituras de página em paralelo
TIPOS_IMAGEM = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}
BATCHEXECUTE = "https://news.google.com/_/DotsSplashUi/data/batchexecute"
NAVEGADOR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


# ---------------------------------------------------------------- URL original

def resolver_url_google(link: str, sessao: requests.Session | None = None) -> str:
    """Descobre a URL original de um link news.google.com/rss/articles/....

    O Google não devolve um redirecionamento HTTP simples: é preciso ler a página do artigo
    e perguntar ao endpoint interno `batchexecute`. Em qualquer falha devolve "" (o chamador
    usa o próprio link do Google como alternativa).
    """
    http = sessao or requests
    try:
        pagina = http.get(link, headers={"User-Agent": NAVEGADOR}, timeout=TIMEOUT)
        pagina.raise_for_status()
        valores = {}
        for chave in ("data-n-a-id", "data-n-a-ts", "data-n-a-sg"):
            m = re.search(chave + r'="([^"]+)"', pagina.text)
            if not m:
                raise ValueError(f"campo {chave} não encontrado")
            valores[chave] = m.group(1)
        interno = json.dumps(
            [
                "garturlreq",
                [["X", "X", ["X", "X"], None, None, 1, 1, "US:en", None, 1, None, None, None, None, None, 0, 1],
                 "X", "X", 1, [1, 1, 1], 1, 1, None, 0, 0, None, 0],
                valores["data-n-a-id"],
                int(valores["data-n-a-ts"]),
                valores["data-n-a-sg"],
            ]
        )
        corpo = "f.req=" + quote(json.dumps([[["Fbv4je", interno, None, "generic"]]]))
        resp = http.post(
            BATCHEXECUTE,
            data=corpo,
            headers={"User-Agent": NAVEGADOR, "Content-Type": "application/x-www-form-urlencoded;charset=UTF-8"},
            timeout=TIMEOUT,
        )
        resp.raise_for_status()
        analisado = json.loads(resp.text.split("\n\n")[1])
        url = json.loads(analisado[0][2])[1]
        if not (isinstance(url, str) and url.startswith(("http://", "https://"))):
            raise ValueError("URL inesperada na resposta")
        return url
    except Exception as exc:  # noqa: BLE001 - qualquer falha cai na alternativa
        log.warning("Não consegui resolver %s...: %s", link[:70], exc)
        return ""


# ---------------------------------------------------------------- robots.txt

class Robots:
    """Consulta (com cache por site) se o nosso agente pode acessar uma URL."""

    def __init__(self, sessao: requests.Session | None = None):
        self.http = sessao or requests
        self._cache: dict[str, RobotFileParser | bool] = {}

    def permitido(self, url: str) -> bool:
        partes = urlsplit(url)
        if partes.scheme not in ("http", "https") or not partes.netloc:
            return False
        origem = f"{partes.scheme}://{partes.netloc}"
        if origem not in self._cache:
            self._cache[origem] = self._carregar(origem)
        regra = self._cache[origem]
        return regra if isinstance(regra, bool) else regra.can_fetch(AGENTE, url)

    def _carregar(self, origem: str) -> RobotFileParser | bool:
        try:
            resp = self.http.get(origem + "/robots.txt", headers={"User-Agent": USER_AGENT}, timeout=10)
        except requests.RequestException:
            return False  # sem conseguir ler as regras, não arrisca
        if resp.status_code in (401, 403) or resp.status_code >= 500:
            return False
        if resp.status_code >= 400:
            return True  # 404 etc.: o site não publica regras
        rp = RobotFileParser()
        rp.parse(resp.text.splitlines())
        return rp


# ---------------------------------------------------------------- páginas

@dataclass
class Pagina:
    imagem_url: str = ""
    descricao: str = ""
    trecho: str = ""


def _ler_limitado(resp, limite: int) -> bytes:
    partes, total = [], 0
    for bloco in resp.iter_content(chunk_size=65536):
        partes.append(bloco)
        total += len(bloco)
        if total >= limite:
            break
    return b"".join(partes)[:limite]


def extrair_pagina(html: bytes | str, url_base: str) -> Pagina:
    sopa = BeautifulSoup(html, "html.parser")

    def meta(*nomes: str) -> str:
        for nome in nomes:
            tag = sopa.find("meta", attrs={"property": nome}) or sopa.find("meta", attrs={"name": nome})
            if tag and tag.get("content", "").strip():
                return tag["content"].strip()
        return ""

    imagem = meta("og:image", "og:image:url", "twitter:image")
    if imagem:
        imagem = urljoin(url_base, imagem)
    descricao = " ".join(meta("og:description", "description", "twitter:description").split())
    paragrafos = []
    for p in sopa.find_all("p"):
        texto = " ".join(p.get_text(" ", strip=True).split())
        if len(texto) >= 60:
            paragrafos.append(texto)
        if sum(len(x) for x in paragrafos) >= MAX_TRECHO:
            break
    trecho = " ".join(paragrafos)[:MAX_TRECHO]
    return Pagina(imagem_url=imagem, descricao=descricao, trecho=trecho)


def obter_pagina(url: str, robots: Robots, sessao: requests.Session | None = None) -> Pagina:
    """Baixa a matéria (se o robots.txt permitir) e extrai imagem e trecho. Nunca levanta erro."""
    http = sessao or requests
    if not robots.permitido(url):
        log.info("robots.txt não permite coletar %s", url)
        return Pagina()
    try:
        resp = http.get(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}, timeout=TIMEOUT, stream=True)
        if resp.status_code != 200:
            log.info("Página %s respondeu %s", url, resp.status_code)
            return Pagina()
        pagina = extrair_pagina(_ler_limitado(resp, MAX_HTML), resp.url or url)
    except Exception as exc:  # noqa: BLE001
        log.info("Falha ao ler %s: %s", url, exc)
        return Pagina()
    return pagina


def baixar_imagem(
    imagem_url: str, pasta: Path, nome: str, robots: Robots, sessao: requests.Session | None = None
) -> str | None:
    """Salva a imagem em `pasta` e devolve o nome do arquivo, ou None se não for possível."""
    if not imagem_url or not robots.permitido(imagem_url):
        return None
    http = sessao or requests
    try:
        resp = http.get(imagem_url, headers={"User-Agent": USER_AGENT, "Accept": "image/*"}, timeout=TIMEOUT, stream=True)
        if resp.status_code != 200:
            return None
        tipo = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if tipo not in TIPOS_IMAGEM:
            return None
        dados = _ler_limitado(resp, MAX_IMAGEM + 1)
    except Exception as exc:  # noqa: BLE001
        log.info("Falha ao baixar imagem %s: %s", imagem_url, exc)
        return None
    if not 500 < len(dados) <= MAX_IMAGEM:
        return None
    arquivo = f"{nome}.{TIPOS_IMAGEM[tipo]}"
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / arquivo).write_bytes(dados)
    return arquivo


# ---------------------------------------------------------------- por grupo

def enriquecer_artigos(artigos: list[Artigo], robots: Robots, sessao: requests.Session | None = None) -> None:
    """Preenche url, trecho e imagem_url de cada artigo (altera os objetos). Lê em paralelo."""

    def um(a: Artigo) -> None:
        a.url = resolver_url_google(a.link, sessao) or a.link
        if "news.google.com" in urlsplit(a.url).netloc:
            return  # não conseguimos chegar à matéria original
        pagina = obter_pagina(a.url, robots, sessao)
        texto = pagina.trecho
        if pagina.descricao and pagina.descricao not in texto:
            texto = f"{pagina.descricao} {texto}"
        a.trecho = texto.strip()[:MAX_TRECHO]
        a.imagem_url = pagina.imagem_url

    with ThreadPoolExecutor(max_workers=TRABALHADORES) as pool:
        list(pool.map(um, artigos))


def imagem_do_grupo(
    artigos: list[Artigo], pasta: Path, nome: str, robots: Robots, sessao: requests.Session | None = None
) -> str | None:
    """Tenta a imagem de cada fonte, na ordem; devolve o nome do arquivo salvo ou None."""
    for a in artigos:
        arquivo = baixar_imagem(a.imagem_url, pasta, nome, robots, sessao)
        if arquivo:
            return arquivo
    return None
