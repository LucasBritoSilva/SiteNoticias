import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import main
import site_builder
from gemini import GeminiClient, GeminiCotaErro, GeminiErro, GeminiResumidor, RespostaInvalida
from coleta import Artigo
from resumos import Resumo
from site_builder import Item

AGORA = datetime(2026, 11, 11, 7, 0, tzinfo=timezone.utc)
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
    assert "03:00 (horário de Cuiabá)" in html                                 # 07:00 UTC


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
            {"nicho": "ia", "relevancia": max(0, 10 - n), "ids": ids,
             "titulo": f"Resumo simulado {self.prefixo} {n}",
             "resumo": "Texto simulado do resumo, longo o bastante para passar na validação do pipeline."}
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
    (tmp_path / "nichos.json").write_text(json.dumps({"nichos": ["ia"]}), encoding="utf-8")
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


def test_com_tentativas_repete_ate_3_vezes_com_espera():
    esperas, chamadas = [], []

    def tarefa():
        chamadas.append(1)
        raise RuntimeError("falhou")

    assert main.com_tentativas(tarefa, dormir=esperas.append) is False
    assert len(chamadas) == 3 and esperas == [900, 900]    # 15 min entre as tentativas, nenhuma após a última


def test_com_tentativas_para_no_primeiro_sucesso():
    estado = {"n": 0}

    def tarefa():
        estado["n"] += 1
        if estado["n"] < 2:
            raise RuntimeError("x")

    esperas = []
    assert main.com_tentativas(tarefa, dormir=esperas.append) is True
    assert estado["n"] == 2 and esperas == [900]


# ------------------------------------------------------------ erro de cota x temporário na execução

def test_cota_para_a_execucao_sem_retentativa_e_sem_esperar(caplog):
    esperas, chamadas = [], []

    def tarefa():
        chamadas.append(1)
        raise GeminiCotaErro("HTTP 429: quota exceeded")

    with caplog.at_level("ERROR"):
        assert main.com_tentativas(tarefa, dormir=esperas.append) is False
    assert len(chamadas) == 1 and esperas == []
    assert "ERRO DE COTA" in caplog.text and "sem novas tentativas" in caplog.text


def test_erro_temporario_continua_com_3_tentativas_e_o_log_diz_o_tipo(caplog):
    esperas = []

    def tarefa():
        raise GeminiErro("Gemini indisponível após 3 tentativas (HTTP 503)")

    with caplog.at_level("ERROR"):
        assert main.com_tentativas(tarefa, dormir=esperas.append) is False
    assert esperas == [900, 900]
    assert "TEMPORÁRIO" in caplog.text and "COTA" not in caplog.text


def test_temporario_seguido_de_cota_para_na_cota():
    estado = {"n": 0}
    esperas = []

    def tarefa():
        estado["n"] += 1
        raise GeminiErro("503") if estado["n"] == 1 else GeminiCotaErro("429 cota")

    assert main.com_tentativas(tarefa, dormir=esperas.append) is False
    assert estado["n"] == 2 and esperas == [900]


def test_cota_no_pipeline_real_mantem_docs_e_cache(raiz):
    class Sessao429:
        chamadas = 0

        def post(self, *a, **k):
            Sessao429.chamadas += 1
            corpo = {"error": {"message": "You exceeded your current quota", "details": [
                {"violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}]}}

            class R:
                status_code, text = 429, json.dumps(corpo)

                def json(self):
                    return corpo
            return R()

    cliente = GeminiClient(chave="chave-de-teste", sessao=Sessao429(), intervalo_minimo=0, esperas=(0, 0))
    resumidor = GeminiResumidor(cliente)
    esperas = []
    ok = main.com_tentativas(lambda: main.executar(raiz, resumidor, agora=AGORA), dormir=esperas.append)
    assert ok is False and esperas == []
    assert Sessao429.chamadas == 1                                        # nenhuma retentativa
    assert (raiz / "docs" / "index.html").read_text() == "VERSAO ANTERIOR"  # docs/ mantido
    assert not (raiz / "dados").exists()                                  # cache não foi criado
