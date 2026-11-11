"""Cache dos resumos já gerados (dados/resumos_gerados.json).

Serve para não pagar de novo por uma notícia que já foi resumida: as fontes de um grupo
resumido nas últimas 24 horas não são reenviadas ao Gemini, e o resumo guardado é reaproveitado
enquanto alguma das fontes ainda estiver no feed das últimas 24 horas.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from coleta import Artigo, normalizar_link
from resumos import Grupo

log = logging.getLogger(__name__)

VALIDADE_HORAS = 24
VERSAO = 1
# Só por segurança: o arquivo é commitado, então nada que pareça chave pode ir parar nele.
PADROES_DE_SEGREDO = [
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
]


@dataclass
class Entrada:
    id: str
    gerado_em: str  # ISO 8601 em UTC
    nicho: str
    relevancia: float
    titulo: str
    resumo: str
    # [{"nome", "url" (original), "link" (do RSS, usado como chave), "imagem_url"}]
    fontes: list[dict] = field(default_factory=list)

    def links(self) -> set[str]:
        return {normalizar_link(f["link"]) for f in self.fontes}


def id_do_grupo(links: list[str]) -> str:
    """Identificador estável: não depende da ordem das fontes."""
    base = "\n".join(sorted(normalizar_link(l) for l in links))
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:12]


def entrada_de_grupo(grupo: Grupo, agora: datetime) -> Entrada:
    return Entrada(
        id=id_do_grupo([a.link for a in grupo.artigos]),
        gerado_em=agora.isoformat(),
        nicho=grupo.nicho,
        relevancia=grupo.relevancia,
        titulo=grupo.titulo,
        resumo=grupo.resumo,
        fontes=[
            {"nome": a.fonte, "url": a.url or a.link, "link": a.link, "imagem_url": a.imagem_url}
            for a in grupo.artigos
        ],
    )


def grupo_de_entrada(e: Entrada) -> Grupo:
    """Recria o grupo a partir do cache (sem chamar o Gemini nem resolver links de novo)."""
    artigos = [
        Artigo(titulo="", link=f["link"], fonte=f["nome"], fonte_url="", publicado=e.gerado_em,
               nicho=e.nicho, url=f["url"], imagem_url=f.get("imagem_url", ""))
        for f in e.fontes
    ]
    return Grupo(nicho=e.nicho, relevancia=e.relevancia, artigos=artigos, titulo=e.titulo, resumo=e.resumo)


def _valida(e: Entrada, agora: datetime) -> bool:
    try:
        idade = agora - datetime.fromisoformat(e.gerado_em)
    except ValueError:
        return False
    return -timedelta(minutes=5) <= idade <= timedelta(hours=VALIDADE_HORAS)  # tolera relógio adiantado


def carregar(caminho: Path, agora: datetime) -> list[Entrada]:
    """Lê o cache e descarta entradas vencidas ou malformadas. Arquivo ausente/ilegível = cache vazio."""
    try:
        dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
        brutas = dados["grupos"]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    entradas: list[Entrada] = []
    for b in brutas if isinstance(brutas, list) else []:
        try:
            e = Entrada(**b)
            if e.fontes and all("link" in f and "nome" in f and "url" in f for f in e.fontes) and _valida(e, agora):
                entradas.append(e)
        except (TypeError, ValueError):
            continue
    return entradas


def salvar(caminho: Path, entradas: list[Entrada], agora: datetime) -> None:
    """Grava o cache (sem entradas vencidas ou repetidas) de forma atômica."""
    vistos: dict[str, Entrada] = {}
    for e in entradas:
        if _valida(e, agora) and e.id not in vistos:
            vistos[e.id] = e
    texto = json.dumps(
        {"versao": VERSAO, "grupos": [asdict(e) for e in vistos.values()]}, ensure_ascii=False, indent=2
    ) + "\n"
    for padrao in PADROES_DE_SEGREDO:
        if padrao.search(texto):
            raise ValueError("O cache contém algo que parece uma chave/token; não será gravado.")
    caminho = Path(caminho)
    caminho.parent.mkdir(parents=True, exist_ok=True)
    tmp = caminho.with_suffix(".tmp")
    tmp.write_text(texto, encoding="utf-8")
    tmp.replace(caminho)


def planejar(entradas: list[Entrada], artigos: list[Artigo], total: int) -> tuple[list[Entrada], list[Artigo]]:
    """Decide o que reaproveitar e o que ainda precisa ir ao Gemini.

    Devolve (reaproveitadas, novos_artigos):
    - reaproveitadas: até `total` entradas do cache que ainda têm alguma fonte no feed atual
      (as mais relevantes primeiro);
    - novos_artigos: artigos do feed cujas fontes NÃO aparecem em nenhum resumo recente.
    """
    no_feed = {normalizar_link(a.link) for a in artigos}
    ja_resumidos = set().union(*(e.links() for e in entradas)) if entradas else set()
    vivas = [e for e in entradas if e.links() & no_feed]
    vivas.sort(key=lambda e: (len(e.fontes) < 2, -e.relevancia))
    novos = [a for a in artigos if normalizar_link(a.link) not in ja_resumidos]
    return vivas[:total], novos
