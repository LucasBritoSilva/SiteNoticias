"""Testes do cliente Gemini e dos resumos.

NÃO há GEMINI_API_KEY neste ambiente de desenvolvimento: todas as respostas do Gemini aqui
são SIMULADAS (cliente falso e sessão HTTP falsa). Nenhuma chamada real à API foi testada.
"""
import json
from pathlib import Path

import pytest

import coleta
import gemini
import resumos
from coleta import Artigo
from gemini import GeminiClient, GeminiErro, RespostaInvalida

FIXTURES = Path(__file__).parent / "fixtures"


def art(i, fonte=None, nicho="ia", hora=10):
    return Artigo(
        titulo=f"Fato {i}", link=f"https://news.google.com/rss/articles/{i}", fonte=fonte or f"Veiculo{i}",
        fonte_url="", publicado=f"2026-11-11T{hora:02d}:00:00+00:00", nicho=nicho,
        url=f"https://site{i}.com/materia-{i}",
    )


class Falso:
    """Cliente simulado: devolve as respostas na ordem (texto ou exceção)."""

    def __init__(self, *respostas):
        self.respostas = list(respostas)
        self.prompts = []

    def gerar(self, prompt):
        self.prompts.append(prompt)
        r = self.respostas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r if isinstance(r, str) else json.dumps(r)


RESUMO_OK = {
    "titulo": "MEC define regras para uso de inteligência artificial nas escolas",
    "resumo": "O Ministério da Educação publicou diretrizes para o uso de inteligência artificial em escolas.",
    "fontes": [{"nome": "Veiculo1", "url": "https://site1.com/materia-1"},
               {"nome": "Veiculo2", "url": "https://site2.com/materia-2"}],
}


# ------------------------------------------------------------ agrupamento

def test_agrupar_aplica_regras_de_fontes():
    arts = [art(i) for i in range(6)] + [art(6, fonte="Veiculo0")]
    resposta = {"grupos": [
        {"tema": "A", "nicho": "ia", "relevancia": 9, "ids": [0, 1, 2, 3]},   # 4 ids -> corta em 3
        {"tema": "B", "nicho": "ia", "relevancia": 7, "ids": [2, 4]},         # 2 já usado -> sobra 1 -> descarta
        {"tema": "C", "nicho": "ia", "relevancia": 5, "ids": [4, 5]},
        {"tema": "D", "nicho": "ia", "relevancia": 4, "ids": [6, 0]},         # mesmo veículo e id usado
        {"tema": "E", "nicho": "ia", "relevancia": 3, "ids": [99, 1]},        # id inexistente -> ignorado
    ]}
    grupos = resumos.agrupar(Falso(resposta), arts, ["ia"])
    assert [g.tema for g in grupos] == ["A", "C"]
    assert len(grupos[0].artigos) == 3
    assert all(2 <= len(g.artigos) <= 3 for g in grupos)


def test_agrupamento_invalido_tenta_de_novo_uma_vez():
    arts = [art(i) for i in range(4)]
    bom = {"grupos": [{"tema": "A", "nicho": "ia", "relevancia": 8, "ids": [0, 1]}]}
    cliente = Falso("isto não é json", bom)
    assert len(resumos.agrupar(cliente, arts, ["ia"])) == 1
    assert len(cliente.prompts) == 2


def test_agrupamento_invalido_duas_vezes_levanta_erro():
    with pytest.raises(RespostaInvalida):
        resumos.agrupar(Falso({"grupos": "x"}, {"outra": 1}), [art(0), art(1)], ["ia"])


def test_limitar_artigos_alterna_nichos():
    arts = [art(i, nicho="a", hora=10) for i in range(5)] + [art(i + 10, nicho="b", hora=9) for i in range(5)]
    escolhidos = resumos.limitar_artigos(arts, limite=4)
    assert {a.nicho for a in escolhidos} == {"a", "b"}


def test_escolher_top_prioriza_relevancia_e_completa_com_avulsas():
    g1 = resumos.Grupo("x", "ia", 3, [art(0), art(1)])
    g2 = resumos.Grupo("y", "ia", 9, [art(2), art(3)])
    avulsas = [art(4, hora=8), art(5, hora=12), *g1.artigos, *g2.artigos]
    top = resumos.escolher_top([g1, g2], avulsas, total=3)
    assert [g.tema for g in top][:2] == ["y", "x"]
    assert len(top) == 3 and len(top[2].artigos) == 1 and top[2].artigos[0].titulo == "Fato 5"
    assert len(resumos.escolher_top([g1, g2], avulsas, total=1)) == 1


# ------------------------------------------------------------ resumo

def grupo2():
    return resumos.Grupo("tema", "ia", 8, [art(1), art(2)])


def test_resumo_valido():
    r = resumos.resumir(Falso(RESUMO_OK), grupo2())
    assert r.titulo.startswith("MEC define")
    assert [f["url"] for f in r.fontes] == ["https://site1.com/materia-1", "https://site2.com/materia-2"]


def test_resumo_rejeita_url_inventada_e_tenta_de_novo():
    ruim = dict(RESUMO_OK, fontes=[{"nome": "X", "url": "https://inventado.com/a"}])
    cliente = Falso(ruim, RESUMO_OK)
    assert resumos.resumir(cliente, grupo2()).titulo
    assert len(cliente.prompts) == 2


@pytest.mark.parametrize("campo,valor", [
    ("titulo", ""), ("titulo", 5), ("resumo", "curto"), ("resumo", None), ("fontes", []), ("fontes", "x"),
])
def test_resumo_rejeita_campos_invalidos(campo, valor):
    ruim = dict(RESUMO_OK, **{campo: valor})
    with pytest.raises(RespostaInvalida):
        resumos.resumir(Falso(ruim, ruim), grupo2())


def test_resumo_aceita_cerca_markdown():
    texto = "```json\n" + json.dumps(RESUMO_OK) + "\n```"
    assert resumos.resumir(Falso(texto), grupo2()).resumo


def test_prompt_inclui_trecho_das_fontes():
    g = grupo2()
    g.artigos[0].trecho = "Texto de apoio da matéria"
    assert "Texto de apoio da matéria" in resumos.prompt_resumo(g)


def test_com_dados_reais_do_exemplo_coletado():
    dados = json.loads((FIXTURES / "exemplo_coleta.json").read_text(encoding="utf-8"))
    arts = [Artigo(**a) for a in dados["artigos"]]
    resposta = {"grupos": [{"tema": "t", "nicho": "inteligência artificial", "relevancia": 9, "ids": [0, 1]}]}
    grupos = resumos.agrupar(Falso(resposta), arts, ["inteligência artificial"])
    assert len(grupos) == 1 and len(grupos[0].artigos) == 2


# ------------------------------------------------------------ cliente REST

class RespostaHTTP:
    def __init__(self, status, corpo):
        self.status_code, self._c = status, corpo
        self.text = json.dumps(corpo)

    def json(self):
        return self._c


class SessaoFalsa:
    def __init__(self, *respostas):
        self.respostas, self.chamadas = list(respostas), []

    def post(self, url, headers=None, json=None, timeout=None):
        self.chamadas.append((url, headers, json))
        return self.respostas.pop(0)


def ok(texto):
    return RespostaHTTP(200, {"candidates": [{"content": {"parts": [{"text": texto}]}}]})


def cliente(sessao, **kw):
    return GeminiClient(chave="chave-de-teste", sessao=sessao, intervalo_minimo=0, esperas=(0, 0), **kw)


def test_cliente_envia_chave_no_cabecalho_e_nao_na_url():
    s = SessaoFalsa(ok('{"a":1}'))
    assert cliente(s).gerar("oi") == '{"a":1}'
    url, headers, corpo = s.chamadas[0]
    assert "chave-de-teste" not in url and headers["x-goog-api-key"] == "chave-de-teste"
    assert corpo["generationConfig"]["responseMimeType"] == "application/json"
    assert "generateContent" in url


def test_cliente_repete_em_erro_transitorio_mas_nao_em_erro_de_chave():
    s = SessaoFalsa(RespostaHTTP(503, {}), ok("{}"))
    assert cliente(s).gerar("x") == "{}"
    with pytest.raises(GeminiErro):
        cliente(SessaoFalsa(RespostaHTTP(403, {"error": "chave"}))).gerar("x")
    with pytest.raises(GeminiErro):
        cliente(SessaoFalsa(*[RespostaHTTP(429, {})] * 3)).gerar("x")


def test_cliente_resposta_sem_texto_e_invalida():
    s = SessaoFalsa(RespostaHTTP(200, {"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(RespostaInvalida):
        cliente(s).gerar("x")


def test_cliente_exige_chave(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(GeminiErro):
        GeminiClient()
