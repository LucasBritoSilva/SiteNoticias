import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import main
import site_builder
from gemini import GeminiClient, GeminiCotaErro, GeminiErro, GeminiIndisponivel, GeminiResumidor, RespostaInvalida
from coleta import Artigo
from resumos import Resumo
from site_builder import Item

AGORA = datetime(2026, 11, 11, 7, 0, tzinfo=timezone.utc)
RESUMO_LONGO = ("Frase simulada do resumo, unindo as informações das fontes do grupo com contexto e detalhes. " * 8).strip()
FIXTURES = Path(__file__).parent / "fixtures"


def item(i, imagem=None, titulo=None):
    return Item(
        slug=f"{i:02d}-titulo-{i}", nicho="economia",
        resumo=Resumo(titulo or f"Título <b>{i}</b> & cia", "Primeiro parágrafo.\n\nSegundo <script>x</script>.",
                      [{"nome": "G1 & Cia", "url": "https://g1.globo.com/a?x=1&y=2"},
                       {"nome": "Ruim", "url": "javascript:alert(1)"}]),
        imagem=imagem,
    )


@pytest.fixture
def docs(tmp_path):
    d = tmp_path / "docs"
    (d / "img").mkdir(parents=True)
    (d / "img" / "generica.svg").write_text("<svg/>")
    (d / "style.css").write_text("body{}")
    (d / "editar.html").write_text("<html></html>")
    return d


def test_slugify():
    assert site_builder.slugify("Inflação sobe 0,5%: o que MUDA?") == "inflacao-sobe-0-5-o-que-muda"
    assert site_builder.slugify("???") == "noticia"


def test_index_tem_cartoes_com_imagem_titulo_e_botao():
    html = site_builder.pagina_index([item(1, "a.jpg"), item(2)], ["economia"], AGORA)
    assert html.count('class="cartao"') == 2
    assert 'src="img/a.jpg"' in html and 'src="img/generica.svg"' in html     # fallback genérico
    assert 'href="resumos/01-titulo-1.html"' in html and "Ler resumo" in html
    assert "<b>1</b>" not in html and "&lt;b&gt;1&lt;/b&gt;" in html          # escapado
    assert 'name="viewport"' in html
    assert "11/11/2026 às 04:00 (horário de Brasília)" in html  # 07:00 UTC
    assert "editar.html" not in html                                          # sem link para editar nichos
    assert "atualizado todos os dias às 19:17 (horário de Brasília)" in html


def test_pagina_de_resumo_lista_fontes_e_escapa():
    html = site_builder.pagina_resumo(item(1), AGORA)
    assert html.count("<p>") >= 2 and "<script>" not in html
    assert 'href="https://g1.globo.com/a?x=1&amp;y=2"' in html
    assert 'rel="noopener noreferrer"' in html
    assert 'href="#"' in html and "javascript:" not in html                    # esquema perigoso barrado
    assert 'src="../img/generica.svg"' in html and 'href="../style.css"' in html


def test_publicar_troca_apenas_o_conteudo_gerado(docs, tmp_path):
    # versão anterior no ar
    (docs / "index.html").write_text("ANTIGO")
    (docs / "resumos").mkdir()
    (docs / "resumos" / "velho.html").write_text("velho")
    (docs / "img" / "velha.jpg").write_bytes(b"x")
    staging = tmp_path / ".staging"
    (staging / "img").mkdir(parents=True)
    (staging / "img" / "01-titulo-1.jpg").write_bytes(b"nova")
    site_builder.gerar_site([item(1, "01-titulo-1.jpg")], ["economia"], staging, AGORA)
    site_builder.publicar(staging, docs)
    assert "ANTIGO" not in (docs / "index.html").read_text(encoding="utf-8")
    assert not (docs / "resumos" / "velho.html").exists() and not (docs / "img" / "velha.jpg").exists()
    assert (docs / "resumos" / "01-titulo-1.html").exists() and (docs / "img" / "01-titulo-1.jpg").exists()
    # arquivos fixos preservados
    for f in ("style.css", "editar.html", "img/generica.svg"):
        assert (docs / f).exists()


def test_publicar_recusa_site_com_link_quebrado_e_nao_toca_em_docs(docs, tmp_path):
    (docs / "index.html").write_text("ANTIGO")
    staging = tmp_path / ".staging"
    site_builder.gerar_site([item(1, "nao-existe.jpg")], ["economia"], staging, AGORA)
    with pytest.raises(RuntimeError, match="Link quebrado"):
        site_builder.publicar(staging, docs)
    assert (docs / "index.html").read_text() == "ANTIGO"


# ------------------------------------------------------------ pipeline completo (tudo simulado)

class GeminiSimulado:
    """Cliente simulado do Gemini: uma resposta única com os grupos pedidos. Conta as chamadas."""

    def __init__(self, ids_por_grupo, quebrar=False, prefixo="do grupo"):
        self.ids, self.quebrar, self.prefixo, self.prompts = ids_por_grupo, quebrar, prefixo, []

    def gerar(self, prompt):
        self.prompts.append(prompt)
        if self.quebrar:
            return "nada de json"
        return json.dumps({"grupos": [
            {"nicho": "inteligência artificial", "relevancia": max(0, 10 - n), "ids": ids,
             "titulo": f"Resumo simulado {self.prefixo} {n}",
             "resumo": RESUMO_LONGO}
            for n, ids in enumerate(self.ids)
        ]})


def pipeline(raiz, ids, **kw):
    cliente = GeminiSimulado(ids, **kw)
    return main.executar(raiz, GeminiResumidor(cliente), agora=AGORA), cliente


@pytest.fixture
def raiz(tmp_path, monkeypatch):
    (tmp_path / "docs" / "img").mkdir(parents=True)
    (tmp_path / "docs" / "img" / "generica.svg").write_text("<svg/>")
    (tmp_path / "docs" / "style.css").write_text("x")
    (tmp_path / "docs" / "editar.html").write_text("x")
    (tmp_path / "docs" / "index.html").write_text("VERSAO ANTERIOR")
    (tmp_path / "nichos.json").write_text(json.dumps({"nichos": ["inteligência artificial"]}), encoding="utf-8")
    dados = json.loads((FIXTURES / "exemplo_coleta.json").read_text(encoding="utf-8"))
    arts = [Artigo(**a) for a in dados["artigos"]]
    monkeypatch.setattr(main.coleta, "coletar", lambda nichos, agora=None: arts)

    def enriquecer(artigos, robots, sessao=None):
        for a in artigos:
            a.url = f"https://exemplo.com/{abs(hash(a.link)) % 10**8}"
            a.trecho = "trecho"
    monkeypatch.setattr(main.paginas, "enriquecer_artigos", enriquecer)
    monkeypatch.setattr(main.paginas, "imagem_do_grupo", lambda *a, **k: None)
    return tmp_path


def test_pipeline_completo_gera_site_com_9_cartoes_com_uma_unica_chamada(raiz):
    ids = [[i, i + 1] for i in range(0, 24, 2)]  # 12 grupos de 2 fontes: mais que o limite, então corta em 9
    n, cliente = pipeline(raiz, ids)
    assert n == 9
    assert len(cliente.prompts) == 1                      # agrupar + resumir tudo em uma chamada
    index = (raiz / "docs" / "index.html").read_text(encoding="utf-8")
    assert index.count('class="cartao"') == 9
    assert len(list((raiz / "docs" / "resumos").glob("*.html"))) == 9
    assert not (raiz / ".staging").exists()
    site_builder.verificar_site(raiz / "docs", raiz / "docs")
    # todo link externo de fonte nas páginas de resumo é uma URL coletada
    for p in (raiz / "docs" / "resumos").glob("*.html"):
        assert re.findall(r'href="(https://exemplo[.]com/[0-9]+)"', p.read_text(encoding="utf-8"))


def test_pipeline_completa_com_noticias_avulsas_quando_faltam_grupos(raiz):
    ids = [[0, 1]] + [[i] for i in range(2, 25)]          # 1 grupo + avulsas (o código evita veículo repetido)
    n, cliente = pipeline(raiz, ids)
    assert n == 9 and len(cliente.prompts) == 1
    index = (raiz / "docs" / "index.html").read_text(encoding="utf-8")
    assert "Resumo simulado do grupo 0" in index         # o grupo de 2 fontes vem primeiro


def test_falha_nao_sobrescreve_docs(raiz):
    with pytest.raises(RespostaInvalida):
        pipeline(raiz, [[0, 1]], quebrar=True)
    assert (raiz / "docs" / "index.html").read_text() == "VERSAO ANTERIOR"


# ------------------------------------------------------------ uma tentativa por execução do script

class SessaoComErro:
    """Sessão HTTP falsa: o Gemini sempre responde com o status dado. Nenhuma cota real é usada."""

    def __init__(self, status, corpo=None):
        self.status, self.chamadas = status, 0
        self.corpo = corpo or {"error": {"code": status, "message": "falha simulada"}}

    def post(self, *a, **k):
        self.chamadas += 1
        sessao = self

        class R:
            status_code, text = sessao.status, json.dumps(sessao.corpo)

            def json(self):
                return sessao.corpo
        return R()


def rodar_main(raiz, monkeypatch, sessao):
    cliente = GeminiClient(chave="chave-de-teste", sessao=sessao, intervalo_minimo=0)
    executar_real = main.executar
    monkeypatch.setattr(main, "RAIZ", raiz)
    monkeypatch.setattr(main, "GeminiResumidor", lambda: GeminiResumidor(cliente))
    monkeypatch.setattr(main, "executar", lambda r, res: executar_real(r, res, agora=AGORA))
    monkeypatch.setattr(main, "configurar_log", lambda: None)
    return main.main()


@pytest.mark.parametrize("status,rotulo", [
    (429, "LIMITE DE USO EXCEDIDO"), (503, "GEMINI INDISPONÍVEL"), (500, "GEMINI INDISPONÍVEL"),
    (403, "ERRO DO GEMINI"),
])
def test_main_faz_uma_requisicao_nao_espera_e_sai_com_1(raiz, monkeypatch, caplog, status, rotulo):
    esperas = []
    monkeypatch.setattr(main.time, "sleep", esperas.append)
    sessao = SessaoComErro(status)
    with caplog.at_level("INFO"):
        assert rodar_main(raiz, monkeypatch, sessao) == 1
    assert sessao.chamadas == 1 and esperas == []
    assert (raiz / "docs" / "index.html").read_text() == "VERSAO ANTERIOR"   # docs/ mantido
    assert not (raiz / "dados").exists()                                     # cache não foi criado
    final = [r.getMessage() for r in caplog.records if "RESUMO DA TENTATIVA" in r.getMessage()][-1]
    assert "requisições ao Gemini: 1" in final and rotulo in final and f"HTTP {status}" in final
    assert "chave-de-teste" not in caplog.text


def test_main_registra_erro_inesperado_e_sai_com_1(raiz, monkeypatch, caplog):
    def sem_noticias(nichos, agora=None):
        return []
    monkeypatch.setattr(main.coleta, "coletar", sem_noticias)
    sessao = SessaoComErro(503)
    with caplog.at_level("INFO"):
        assert rodar_main(raiz, monkeypatch, sessao) == 1
    assert sessao.chamadas == 0 and "Notícias insuficientes" in caplog.text


def test_main_sem_chave_sai_com_1(monkeypatch, caplog):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr(main, "configurar_log", lambda: None)
    with caplog.at_level("INFO"):
        assert main.main() == 1
    assert "requisições ao Gemini: 0" in caplog.text and "GEMINI_API_KEY" in caplog.text


# ------------------------------------------------------------ texto lido antes da chamada única

def test_texto_das_materias_e_lido_antes_da_chamada_unica_e_vai_no_prompt(raiz):
    n, cliente = pipeline(raiz, [[i, i + 1] for i in range(0, 18, 2)])
    assert n == 9 and len(cliente.prompts) == 1
    assert "Texto: trecho" in cliente.prompts[0]                 # o texto lido foi enviado junto


def test_materias_sem_texto_ficam_de_fora_quando_ha_outras_com_texto(raiz, monkeypatch):
    def enriquecer(artigos, robots, sessao=None):
        for i, a in enumerate(artigos):
            a.url = f"https://exemplo.com/{i}"
            a.trecho = "texto lido" if i % 2 == 0 else ""
    monkeypatch.setattr(main.paginas, "enriquecer_artigos", enriquecer)
    _, cliente = pipeline(raiz, [[0, 1], [2, 3], [4, 5]])
    p = cliente.prompts[0]
    assert "indisponível" not in p and p.count("Texto: texto lido") >= 2


def test_sem_nenhum_texto_usa_as_manchetes_e_ainda_publica(raiz, monkeypatch):
    def enriquecer(artigos, robots, sessao=None):
        for i, a in enumerate(artigos):
            a.url = f"https://exemplo.com/{i}"
    monkeypatch.setattr(main.paginas, "enriquecer_artigos", enriquecer)
    n, cliente = pipeline(raiz, [[0, 1], [2, 3]])
    assert n == 2 and len(cliente.prompts) == 1
    assert "indisponível" in cliente.prompts[0]
