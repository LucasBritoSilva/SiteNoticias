import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import feedparser
import pytest

import coleta

AGORA = datetime(2026, 11, 11, 12, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / "fixtures"


def rfc822(dt):
    return dt.strftime("%a, %d %b %Y %H:%M:%S GMT")


def feed_xml(itens):
    corpo = "".join(
        f"<item><title>{t}</title><link>{l}</link><pubDate>{rfc822(d)}</pubDate>"
        f'<source url="https://{f.lower()}.com">{f}</source></item>'
        for t, l, d, f in itens
    )
    return feedparser.parse(f"<?xml version='1.0'?><rss version='2.0'><channel>{corpo}</channel></rss>")


def test_url_do_feed_codifica_termo():
    assert coleta.url_do_feed("inteligência artificial") == (
        "https://news.google.com/rss/search?q=intelig%C3%AAncia+artificial"
        "&hl=pt-BR&gl=BR&ceid=BR:pt-419"
    )


def test_carregar_nichos(tmp_path):
    p = tmp_path / "n.json"
    p.write_text(json.dumps({"nichos": [" ia ", "ia", "", "economia"]}), encoding="utf-8")
    assert coleta.carregar_nichos(p) == ["ia", "economia"]
    p.write_text("[]", encoding="utf-8")
    with pytest.raises(ValueError):
        coleta.carregar_nichos(p)


def test_normalizar_titulo():
    assert coleta.normalizar_titulo("  Inflação: o que MUDA?! ") == "inflacao o que muda"


def test_normalizar_link_remove_rastreio():
    a = coleta.normalizar_link("https://Site.com/a/b/?utm_source=x&id=1#topo")
    b = coleta.normalizar_link("https://site.com/a/b?id=1")
    assert a == b


def test_limpar_titulo_remove_sufixo_da_fonte():
    assert coleta.limpar_titulo("Notícia boa - G1", "G1") == "Notícia boa"
    assert coleta.limpar_titulo("Notícia - outra coisa", "G1") == "Notícia - outra coisa"


def test_filtra_ultimas_24_horas():
    feed = feed_xml([
        ("Recente - A", "https://x/1", AGORA - timedelta(hours=2), "A"),
        ("Limite - A", "https://x/2", AGORA - timedelta(hours=23, minutes=59), "A"),
        ("Antiga - A", "https://x/3", AGORA - timedelta(hours=25), "A"),
    ])
    arts = coleta.converter_feed(feed, "ia", AGORA)
    assert [a.titulo for a in arts] == ["Recente", "Limite"]
    assert arts[0].fonte == "A" and arts[0].nicho == "ia"


def test_deduplica_por_link_e_por_titulo():
    feed = feed_xml([
        ("Mesmo fato - A", "https://x/1", AGORA - timedelta(hours=1), "A"),
        ("Mesmo fato - A", "https://x/1?utm_source=z", AGORA - timedelta(hours=2), "A"),
        ("MESMO  fato!", "https://y/9", AGORA - timedelta(hours=3), "B"),
        ("Outro fato - C", "https://z/2", AGORA - timedelta(hours=4), "C"),
    ])
    unicos = coleta.deduplicar(coleta.converter_feed(feed, "ia", AGORA))
    assert [a.titulo for a in unicos] == ["Mesmo fato", "Outro fato"]


def test_coletar_ignora_feed_com_erro_mas_falha_se_todos_falharem():
    feed = feed_xml([("T - A", "https://x/1", AGORA - timedelta(hours=1), "A")])

    def buscar(termo):
        if termo == "ruim":
            raise ConnectionError("fora do ar")
        return feed

    assert len(coleta.coletar(["ruim", "bom"], AGORA, buscar)) == 1
    with pytest.raises(RuntimeError):
        coleta.coletar(["ruim"], AGORA, buscar)


def test_exemplo_real_salvo_e_consistente():
    dados = json.loads((FIXTURES / "exemplo_coleta.json").read_text(encoding="utf-8"))
    arts = [coleta.Artigo(**a) for a in dados["artigos"]]
    assert len(arts) >= 10
    assert all(a.link.startswith("https://news.google.com/") for a in arts)
    assert len(coleta.deduplicar(arts)) == len(arts)
