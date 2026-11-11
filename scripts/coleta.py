"""Coleta de notícias via RSS do Google Notícias e remoção de duplicadas."""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote_plus, urlsplit, urlunsplit

import feedparser
import requests

log = logging.getLogger(__name__)

RSS_URL = "https://news.google.com/rss/search?q={q}&hl=pt-BR&gl=BR&ceid=BR:pt-419"
USER_AGENT = "Mozilla/5.0 (compatible; SiteNoticias/1.0)"
JANELA_HORAS = 24


@dataclass
class Artigo:
    titulo: str
    link: str
    fonte: str
    fonte_url: str
    publicado: str  # ISO 8601 em UTC
    nicho: str

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------- nichos

def carregar_nichos(caminho: Path) -> list[str]:
    """Lê nichos.json. Aceita {"nichos": [...]} ou uma lista simples."""
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    termos = dados.get("nichos") if isinstance(dados, dict) else dados
    if not isinstance(termos, list):
        raise ValueError("nichos.json deve conter uma lista de termos em 'nichos'.")
    limpos: list[str] = []
    for t in termos:
        if isinstance(t, str) and t.strip() and t.strip() not in limpos:
            limpos.append(t.strip())
    if not limpos:
        raise ValueError("nichos.json não tem nenhum termo válido.")
    return limpos


def url_do_feed(termo: str) -> str:
    return RSS_URL.format(q=quote_plus(termo))


# ---------------------------------------------------------------- normalização

def normalizar_titulo(titulo: str) -> str:
    """Minúsculas, sem acentos, sem pontuação e com espaços colapsados."""
    t = unicodedata.normalize("NFKD", titulo.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return t.strip()


def normalizar_link(link: str) -> str:
    """Remove fragmento, parâmetros de rastreio (utm_*) e barra final."""
    partes = urlsplit(link.strip())
    consulta = "&".join(
        p for p in partes.query.split("&") if p and not p.lower().startswith(("utm_", "oc="))
    )
    caminho = partes.path.rstrip("/")
    return urlunsplit((partes.scheme.lower(), partes.netloc.lower(), caminho, consulta, ""))


def limpar_titulo(titulo: str, fonte: str) -> str:
    """O Google Notícias anexa ' - Veículo' ao título; remove o sufixo."""
    titulo = " ".join(titulo.split())
    sufixo = f" - {fonte}"
    if fonte and titulo.endswith(sufixo):
        titulo = titulo[: -len(sufixo)].rstrip()
    return titulo


# ---------------------------------------------------------------- coleta

def _data_da_entrada(entrada) -> datetime | None:
    pp = entrada.get("published_parsed") or entrada.get("updated_parsed")
    if not pp:
        return None
    return datetime(*pp[:6], tzinfo=timezone.utc)


def converter_feed(feed, nicho: str, agora: datetime, horas: int = JANELA_HORAS) -> list[Artigo]:
    """Converte um feed já lido em artigos das últimas `horas` horas."""
    limite = agora - timedelta(hours=horas)
    artigos: list[Artigo] = []
    for e in feed.entries:
        data = _data_da_entrada(e)
        link = e.get("link")
        titulo = e.get("title")
        if not (data and link and titulo):
            continue
        if data < limite or data > agora + timedelta(minutes=5):
            continue
        origem = e.get("source") or {}
        fonte = (origem.get("title") or "").strip() or "Fonte desconhecida"
        artigos.append(
            Artigo(
                titulo=limpar_titulo(titulo, fonte),
                link=link,
                fonte=fonte,
                fonte_url=origem.get("href", ""),
                publicado=data.isoformat(),
                nicho=nicho,
            )
        )
    return artigos


def buscar_feed(termo: str, sessao: requests.Session | None = None, timeout: int = 30):
    http = sessao or requests
    resp = http.get(url_do_feed(termo), headers={"User-Agent": USER_AGENT}, timeout=timeout)
    resp.raise_for_status()
    return feedparser.parse(resp.content)


def deduplicar(artigos: list[Artigo]) -> list[Artigo]:
    """Remove artigos repetidos por link ou por título normalizado (mantém o mais recente)."""
    vistos_link: set[str] = set()
    vistos_titulo: set[str] = set()
    unicos: list[Artigo] = []
    for a in sorted(artigos, key=lambda x: x.publicado, reverse=True):
        chave_link = normalizar_link(a.link)
        chave_titulo = normalizar_titulo(a.titulo)
        if chave_link in vistos_link or (chave_titulo and chave_titulo in vistos_titulo):
            continue
        vistos_link.add(chave_link)
        vistos_titulo.add(chave_titulo)
        unicos.append(a)
    return unicos


def coletar(nichos: list[str], agora: datetime | None = None, buscar=buscar_feed) -> list[Artigo]:
    """Busca todos os feeds, filtra as últimas 24 h e remove duplicadas.

    Um feed com erro é registrado e ignorado; se todos falharem, levanta RuntimeError.
    """
    agora = agora or datetime.now(timezone.utc)
    todos: list[Artigo] = []
    falhas = 0
    for termo in nichos:
        try:
            feed = buscar(termo)
        except Exception as exc:  # noqa: BLE001 - qualquer erro de rede/parse conta como falha
            falhas += 1
            log.warning("Falha ao buscar o feed '%s': %s", termo, exc)
            continue
        novos = converter_feed(feed, termo, agora)
        log.info("Feed '%s': %d notícias nas últimas %dh", termo, len(novos), JANELA_HORAS)
        todos.extend(novos)
    if falhas == len(nichos):
        raise RuntimeError("Nenhum feed RSS pôde ser lido.")
    unicos = deduplicar(todos)
    log.info("Total: %d coletadas, %d após remover duplicadas", len(todos), len(unicos))
    return unicos
