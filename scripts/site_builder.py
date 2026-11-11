"""Geração do site estático e publicação segura em docs/.

Arquivos fixos (versionados à mão): docs/style.css, docs/editar.html, docs/img/generica.svg.
Arquivos gerados a cada execução: docs/index.html, docs/resumos/*.html e docs/img/<grupo>.<ext>.
"""
from __future__ import annotations

import html
import re
import shutil
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from resumos import Resumo

CUIABA = timezone(timedelta(hours=-4), "Cuiabá")  # UTC-4 o ano todo, sem horário de verão
TITULO_SITE = "Notícias por nicho"
IMAGEM_GENERICA = "generica.svg"
GERADOS = ("index.html", "resumos")  # além de img/* (exceto a genérica)


@dataclass
class Item:
    slug: str
    nicho: str
    resumo: Resumo
    imagem: str | None  # nome do arquivo em img/, ou None para usar a genérica


# ---------------------------------------------------------------- utilitários

def slugify(texto: str, limite: int = 50) -> str:
    t = unicodedata.normalize("NFKD", texto.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:limite].strip("-") or "noticia"


def esc(texto: str) -> str:
    return html.escape(str(texto), quote=True)


def url_segura(url: str) -> str:
    """Só deixa passar http(s); qualquer outra coisa vira '#'."""
    return esc(url) if urlsplit(url).scheme in ("http", "https") else "#"


def data_legivel(quando: datetime) -> str:
    return quando.astimezone(CUIABA).strftime("%d/%m/%Y às %H:%M") + " (horário de Cuiabá)"


def _pagina(titulo: str, corpo: str, prefixo: str, descricao: str) -> str:
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(titulo)}</title>
<meta name="description" content="{esc(descricao)}">
<link rel="stylesheet" href="{prefixo}style.css">
</head>
<body>
{corpo}
</body>
</html>
"""


def _caminho_imagem(item: Item, prefixo: str) -> str:
    return f"{prefixo}img/{esc(item.imagem or IMAGEM_GENERICA)}"


# ---------------------------------------------------------------- páginas

def pagina_index(itens: list[Item], nichos: list[str], atualizado: datetime) -> str:
    cartoes = []
    for it in itens:
        cartoes.append(
            f"""<article class="cartao">
  <img src="{_caminho_imagem(it, '')}" alt="" loading="lazy" width="640" height="360">
  <div class="cartao-corpo">
    <p class="nicho">{esc(it.nicho)}</p>
    <h2>{esc(it.resumo.titulo)}</h2>
    <a class="botao" href="resumos/{esc(it.slug)}.html">Ler resumo</a>
  </div>
</article>"""
        )
    lista_nichos = ", ".join(esc(n) for n in nichos)
    vazio = "" if itens else '<p class="aviso">Nenhuma notícia disponível no momento.</p>'
    corpo = f"""<header class="topo">
  <h1>{esc(TITULO_SITE)}</h1>
  <p>Atualizado em {esc(data_legivel(atualizado))}</p>
  <p class="nichos">Nichos: {lista_nichos}</p>
</header>
<main class="grade">
{vazio}{chr(10).join(cartoes)}
</main>
<footer class="rodape">
  <p>Resumos gerados automaticamente a partir de notícias do Google Notícias. Confira sempre as fontes originais.</p>
  <p><a href="editar.html">Editar nichos</a></p>
</footer>"""
    return _pagina(TITULO_SITE, corpo, "", f"Notícias dos nichos: {', '.join(nichos)}")


def pagina_resumo(item: Item, atualizado: datetime) -> str:
    paragrafos = "\n".join(
        f"<p>{esc(p.strip())}</p>" for p in re.split(r"\n\s*\n|\n", item.resumo.resumo) if p.strip()
    )
    fontes = "\n".join(
        f'<li><a href="{url_segura(f["url"])}" target="_blank" rel="noopener noreferrer">{esc(f["nome"])}</a></li>'
        for f in item.resumo.fontes
    )
    corpo = f"""<header class="topo topo-interno">
  <a class="voltar" href="../index.html">&larr; Voltar às notícias</a>
</header>
<main class="resumo">
  <p class="nicho">{esc(item.nicho)}</p>
  <h1>{esc(item.resumo.titulo)}</h1>
  <p class="data">{esc(data_legivel(atualizado))}</p>
  <img src="{_caminho_imagem(item, '../')}" alt="" width="640" height="360">
  <div class="texto">
{paragrafos}
  </div>
  <section class="fontes">
    <h2>Fontes</h2>
    <ul>
{fontes}
    </ul>
  </section>
</main>
<footer class="rodape">
  <p>Resumo gerado automaticamente a partir das fontes acima. Confira sempre as matérias originais.</p>
</footer>"""
    return _pagina(item.resumo.titulo, corpo, "../", item.resumo.resumo[:150])


def gerar_site(itens: list[Item], nichos: list[str], destino: Path, atualizado: datetime) -> None:
    """Escreve index.html e resumos/*.html em `destino` (pasta de trabalho, não em docs/)."""
    (destino / "resumos").mkdir(parents=True, exist_ok=True)
    (destino / "index.html").write_text(pagina_index(itens, nichos, atualizado), encoding="utf-8")
    for it in itens:
        (destino / "resumos" / f"{it.slug}.html").write_text(pagina_resumo(it, atualizado), encoding="utf-8")


# ---------------------------------------------------------------- publicação

def verificar_site(staging: Path, docs: Path) -> None:
    """Confere o site gerado: arquivos existem e os links internos apontam para algo real."""
    index = staging / "index.html"
    if not index.exists():
        raise RuntimeError("index.html não foi gerado")
    paginas = [index, *sorted((staging / "resumos").glob("*.html"))]
    if len(paginas) < 2:
        raise RuntimeError("Nenhuma página de resumo foi gerada")
    for pagina in paginas:
        texto = pagina.read_text(encoding="utf-8")
        for alvo in re.findall(r'(?:href|src)="([^"#:]+)"', texto):
            if alvo.startswith(("http", "//")):
                continue
            rel = (pagina.parent / alvo).resolve()
            existe = rel.exists() or (docs / rel.relative_to(staging.resolve())).exists()
            if not existe:
                raise RuntimeError(f"Link quebrado em {pagina.name}: {alvo}")


def publicar(staging: Path, docs: Path) -> None:
    """Troca o conteúdo gerado de docs/ pelo de `staging`, restaurando o anterior se algo falhar."""
    docs.mkdir(parents=True, exist_ok=True)
    verificar_site(staging, docs)
    backup = staging.parent / (staging.name + "-anterior")
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir(parents=True)

    antigos = [docs / n for n in GERADOS if (docs / n).exists()]
    img_docs = docs / "img"
    if img_docs.exists():
        antigos += [p for p in img_docs.iterdir() if p.name != IMAGEM_GENERICA]
    movidos: list[tuple[Path, Path]] = []
    try:
        for p in antigos:
            destino = backup / p.relative_to(docs)
            destino.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(p), str(destino))
            movidos.append((p, destino))
        img_docs.mkdir(exist_ok=True)
        for nome in GERADOS:
            origem = staging / nome
            if origem.exists():
                shutil.move(str(origem), str(docs / nome))
        if (staging / "img").exists():
            for p in (staging / "img").iterdir():
                shutil.move(str(p), str(img_docs / p.name))
    except Exception:
        for nome in GERADOS:  # desfaz o que foi colocado e devolve o que estava no ar
            alvo = docs / nome
            if alvo.is_dir():
                shutil.rmtree(alvo, ignore_errors=True)
            elif alvo.exists():
                alvo.unlink()
        for p in img_docs.iterdir() if img_docs.exists() else []:
            if p.name != IMAGEM_GENERICA:
                p.unlink()
        for original, guardado in movidos:
            original.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(guardado), str(original))
        raise
    finally:
        shutil.rmtree(backup, ignore_errors=True)
