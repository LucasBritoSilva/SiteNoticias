import json

import pytest
import requests

import paginas
from coleta import Artigo
from paginas import Robots

HTML = """<html><head>
<meta property="og:image" content="/fotos/capa.jpg">
<meta property="og:description" content="  Descrição   da matéria ">
</head><body><p>curto</p>
<p>Este é um parágrafo longo o bastante para entrar no trecho que será enviado ao resumo da notícia.</p>
</body></html>"""


class Resp:
    def __init__(self, status=200, corpo=b"", headers=None, url="", texto=None):
        self.status_code = status
        self.content = corpo if isinstance(corpo, bytes) else corpo.encode()
        self.text = texto if texto is not None else self.content.decode("utf-8", "ignore")
        self.headers = headers or {}
        self.url = url

    def iter_content(self, chunk_size=1):
        yield self.content

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(str(self.status_code))


class Sessao:
    """Sessão falsa: mapeia URL -> Resp (ou exceção)."""

    def __init__(self, mapa):
        self.mapa, self.pedidos = mapa, []

    def get(self, url, **kw):
        self.pedidos.append(url)
        r = self.mapa.get(url, Resp(404))
        if isinstance(r, Exception):
            raise r
        r.url = r.url or url
        return r

    post = get


def test_extrair_pagina():
    p = paginas.extrair_pagina(HTML, "https://exemplo.com/a/b")
    assert p.imagem_url == "https://exemplo.com/fotos/capa.jpg"
    assert p.descricao == "Descrição da matéria"
    assert p.trecho.startswith("Este é um parágrafo longo") and "curto" not in p.trecho


def test_robots_respeita_disallow_e_trata_erros():
    s = Sessao({
        "https://a.com/robots.txt": Resp(200, "User-agent: *\nDisallow: /privado/"),
        "https://b.com/robots.txt": Resp(403),
        "https://c.com/robots.txt": Resp(404),
        "https://d.com/robots.txt": requests.ConnectionError("x"),
        "https://e.com/robots.txt": Resp(200, f"User-agent: {paginas.AGENTE}\nDisallow: /"),
    })
    r = Robots(s)
    assert r.permitido("https://a.com/publico/x") is True
    assert r.permitido("https://a.com/privado/x") is False
    assert r.permitido("https://b.com/x") is False      # 403 no robots.txt
    assert r.permitido("https://c.com/x") is True       # sem robots.txt
    assert r.permitido("https://d.com/x") is False      # rede falhou: não arrisca
    assert r.permitido("https://e.com/x") is False      # bloqueia o nosso agente
    assert r.permitido("ftp://a.com/x") is False
    r.permitido("https://a.com/outro")
    assert s.pedidos.count("https://a.com/robots.txt") == 1  # cache


def test_obter_pagina_ok_bloqueada_e_com_erro():
    s = Sessao({
        "https://ok.com/robots.txt": Resp(404),
        "https://ok.com/m": Resp(200, HTML),
        "https://nao.com/robots.txt": Resp(200, "User-agent: *\nDisallow: /"),
        "https://nao.com/m": Resp(200, HTML),
        "https://proibido.com/robots.txt": Resp(404),
        "https://proibido.com/m": Resp(403),
        "https://cai.com/robots.txt": Resp(404),
        "https://cai.com/m": requests.Timeout("lento"),
    })
    robots = Robots(s)
    assert paginas.obter_pagina("https://ok.com/m", robots, s).imagem_url.endswith("capa.jpg")
    assert paginas.obter_pagina("https://nao.com/m", robots, s).imagem_url == ""
    assert "https://nao.com/m" not in s.pedidos          # nem tentou baixar
    assert paginas.obter_pagina("https://proibido.com/m", robots, s) == paginas.Pagina()
    assert paginas.obter_pagina("https://cai.com/m", robots, s) == paginas.Pagina()


def test_baixar_imagem(tmp_path):
    png = b"\x89PNG" + b"0" * 2000
    s = Sessao({
        "https://i.com/robots.txt": Resp(404),
        "https://i.com/boa.png": Resp(200, png, {"Content-Type": "image/png"}),
        "https://i.com/texto": Resp(200, b"x" * 2000, {"Content-Type": "text/html"}),
        "https://i.com/pixel.png": Resp(200, b"x" * 50, {"Content-Type": "image/png"}),
        "https://i.com/403.png": Resp(403),
    })
    r = Robots(s)
    assert paginas.baixar_imagem("https://i.com/boa.png", tmp_path, "g1", r, s) == "g1.png"
    assert (tmp_path / "g1.png").read_bytes() == png
    for ruim in ("https://i.com/texto", "https://i.com/pixel.png", "https://i.com/403.png", "", "https://i.com/nada"):
        assert paginas.baixar_imagem(ruim, tmp_path, "x", r, s) is None
    assert not (tmp_path / "x.png").exists()


def test_imagem_do_grupo_usa_a_primeira_fonte_que_funcionar(tmp_path):
    png = b"\x89PNG" + b"0" * 2000
    s = Sessao({
        "https://i.com/robots.txt": Resp(404),
        "https://i.com/403.png": Resp(403),
        "https://i.com/ok.png": Resp(200, png, {"Content-Type": "image/png"}),
    })
    a = [Artigo("t", "l", "A", "", "", "n", imagem_url="https://i.com/403.png"),
         Artigo("t", "l", "B", "", "", "n", imagem_url="https://i.com/ok.png")]
    assert paginas.imagem_do_grupo(a, tmp_path, "grupo1", Robots(s), s) == "grupo1.png"
    assert paginas.imagem_do_grupo(a[:1], tmp_path, "grupo2", Robots(s), s) is None


def test_resolver_url_google_com_resposta_simulada():
    pagina = 'x data-n-a-sg="SIG" data-n-a-ts="123" data-n-a-id="ID" y'
    interno = json.dumps(["garturlresp", "https://g1.globo.com/materia"])
    corpo = ")]}'\n\n" + json.dumps([["wrb.fr", "Fbv4je", interno, None]]) + "\n\n"
    s = Sessao({
        "https://news.google.com/rss/articles/ABC": Resp(200, pagina),
        paginas.BATCHEXECUTE: Resp(200, corpo),
    })
    assert paginas.resolver_url_google("https://news.google.com/rss/articles/ABC", s) == "https://g1.globo.com/materia"
    assert paginas.resolver_url_google("https://news.google.com/rss/articles/ZZZ", s) == ""


def test_enriquecer_usa_link_do_google_quando_nao_resolve():
    a = Artigo("t", "https://news.google.com/rss/articles/ZZZ", "A", "", "", "n")
    paginas.enriquecer_artigos([a], Robots(Sessao({})), Sessao({}))
    assert a.url == a.link and a.imagem_url == ""


def test_enriquecer_junta_descricao_e_texto_e_le_varias_em_paralelo(monkeypatch):
    monkeypatch.setattr(paginas, "resolver_url_google", lambda link, sessao=None: "https://ok.com/" + link[-1])
    mapa = {"https://ok.com/robots.txt": Resp(404)}
    for n in "abcdef":
        mapa[f"https://ok.com/{n}"] = Resp(200, HTML)
    s = Sessao(mapa)
    arts = [Artigo("t", f"https://news.google.com/rss/articles/{n}", "A", "", "", "n") for n in "abcdef"]
    paginas.enriquecer_artigos(arts, Robots(s), s)
    for a in arts:
        assert a.url.startswith("https://ok.com/")
        assert a.trecho.startswith("Descrição da matéria Este é um parágrafo longo")
        assert a.imagem_url == "https://ok.com/fotos/capa.jpg"
