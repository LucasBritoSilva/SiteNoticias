"""Testes da chamada única ao Gemini, das regras dos grupos e do cliente REST.

NÃO há GEMINI_API_KEY neste ambiente de desenvolvimento: todas as respostas do Gemini aqui
são SIMULADAS (cliente falso e sessão HTTP falsa). Nenhuma chamada real à API foi testada.
"""
import json
from pathlib import Path

import pytest
import requests

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


def item(ids, rel=5, nicho="ia", titulo=None, resumo=None):
    n = ids[0]
    return {
        "ids": ids, "nicho": nicho, "relevancia": rel,
        "titulo": titulo or f"Título explicativo do grupo {n}",
        "resumo": resumo or "Resumo que une as informações das manchetes do grupo, longo o bastante para validar.",
    }


def resumidor(*respostas):
    cliente = Falso(*respostas)
    return gemini.GeminiResumidor(cliente), cliente


# ------------------------------------------------------------ chamada única

def test_uma_unica_chamada_agrupa_escolhe_e_resume():
    arts = [art(i) for i in range(8)]
    r, cliente = resumidor({"grupos": [item([0, 1], 9), item([2, 3], 7), item([4, 5], 5)]})
    grupos = r.resumir_grupos(arts, ["ia"], total=3)
    assert len(cliente.prompts) == 1
    assert [g.titulo for g in grupos] == [
        "Título explicativo do grupo 0", "Título explicativo do grupo 2", "Título explicativo do grupo 4"]
    assert all(g.resumo and 2 <= len(g.artigos) <= 3 for g in grupos)


def test_prompt_traz_manchetes_pedido_de_resumo_e_regra_de_nao_inventar():
    arts = [art(0), art(1)]
    r, cliente = resumidor({"grupos": [item([0, 1])]})
    r.resumir_grupos(arts, ["ia", "economia"], total=9)
    p = cliente.prompts[0]
    assert "0 | Veiculo0 | Fato 0" in p and "1 | Veiculo1 | Fato 1" in p
    assert '["ia", "economia"]' in p and "11 grupos" in p       # 9 + reserva de 2
    assert "SOMENTE o que as manchetes" in p and "nunca invente ids" in p


def test_regras_dos_grupos_na_resposta_unica():
    arts = [art(i) for i in range(6)] + [art(6, fonte="Veiculo0")]
    resposta = {"grupos": [
        item([0, 1, 2, 3], 9),        # 4 ids -> corta em 3
        item([2, 4], 7),              # 2 já usado -> sobra 1 -> descarta
        item([4, 5], 5),
        item([6, 0], 4),              # mesmo veículo e id usado -> descarta
        item([99, 1], 3),             # id inexistente -> grupo inválido
    ]}
    r, _ = resumidor(resposta)
    grupos = r.resumir_grupos(arts, ["ia"], total=9)
    assert [len(g.artigos) for g in grupos] == [3, 2]
    assert [a.titulo for a in grupos[1].artigos] == ["Fato 4", "Fato 5"]


def test_avulsas_so_completam_quando_faltam_grupos_e_sem_repetir_veiculo():
    arts = [art(i) for i in range(8)] + [art(8, fonte="Veiculo5")]
    resposta = {"grupos": [item([0, 1], 3), item([2], 9), item([3], 8), item([5], 7), item([8], 6)]}
    r, _ = resumidor(resposta)
    grupos = r.resumir_grupos(arts, ["ia"], total=4)
    # o grupo de 2 fontes vem primeiro, mesmo com relevância menor; depois avulsas por relevância
    assert [len(g.artigos) for g in grupos] == [2, 1, 1, 1]
    assert [g.artigos[0].titulo for g in grupos[1:]] == ["Fato 2", "Fato 3", "Fato 5"]  # Fato 8 repete o veículo 5
    # com grupos suficientes, avulsas nem entram
    r2, _ = resumidor({"grupos": [item([0, 1], 3), item([2, 3], 2), item([4], 9)]})
    assert [len(g.artigos) for g in r2.resumir_grupos(arts, ["ia"], total=2)] == [2, 2]


def test_nada_e_inventado_ids_precisam_existir():
    arts = [art(0), art(1)]
    r, _ = resumidor({"grupos": [item([0, 1])]}, {"grupos": [item([0, 7])]}, {"grupos": [item([0, 7])]})
    assert len(r.resumir_grupos(arts, ["ia"], 3)) == 1
    with pytest.raises(RespostaInvalida):
        r.resumir_grupos(arts, ["ia"], 3)


def test_resposta_invalida_tenta_de_novo_uma_vez():
    arts = [art(i) for i in range(4)]
    bom = {"grupos": [item([0, 1], 8)]}
    r, cliente = resumidor("isto não é json", bom)
    assert len(r.resumir_grupos(arts, ["ia"], 3)) == 1
    assert len(cliente.prompts) == 2


def test_resposta_invalida_duas_vezes_levanta_erro():
    r, cliente = resumidor({"grupos": "x"}, {"outra": 1})
    with pytest.raises(RespostaInvalida):
        r.resumir_grupos([art(0), art(1)], ["ia"], 3)
    assert len(cliente.prompts) == 2          # nunca mais que 2 chamadas


@pytest.mark.parametrize("campo,valor", [
    ("titulo", ""), ("titulo", 5), ("resumo", "curto"), ("resumo", None), ("ids", []), ("ids", "x"), ("ids", [True]),
])
def test_grupo_com_campo_invalido_e_descartado(campo, valor):
    ruim = dict(item([0, 1]), **{campo: valor})
    r, _ = resumidor({"grupos": [ruim, item([2, 3])]})
    grupos = r.resumir_grupos([art(i) for i in range(4)], ["ia"], 3)
    assert [g.artigos[0].titulo for g in grupos] == ["Fato 2"]


def test_aceita_cerca_markdown_e_limpa_espacos():
    texto = "```json" + chr(10) + json.dumps({"grupos": [item([0, 1], titulo="  Título   com   espaços  ok ")]}) + chr(10) + "```"
    r, _ = resumidor(texto)
    assert r.resumir_grupos([art(0), art(1)], ["ia"], 3)[0].titulo == "Título com espaços ok"


def test_resumidor_cumpre_o_contrato_do_pipeline():
    # o pipeline só precisa do método resumir_grupos; outro provedor pode implementá-lo
    assert callable(getattr(gemini.GeminiResumidor, "resumir_grupos"))


def test_limitar_artigos_alterna_nichos():
    arts = [art(i, nicho="a", hora=10) for i in range(5)] + [art(i + 10, nicho="b", hora=9) for i in range(5)]
    assert {a.nicho for a in resumos.limitar_artigos(arts, limite=4)} == {"a", "b"}


def test_com_dados_reais_do_exemplo_coletado():
    dados = json.loads((FIXTURES / "exemplo_coleta.json").read_text(encoding="utf-8"))
    arts = [Artigo(**a) for a in dados["artigos"]]
    r, _ = resumidor({"grupos": [item([0, 1], 9, nicho="inteligência artificial")]})
    grupos = r.resumir_grupos(arts, ["inteligência artificial"], 9)
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


# ------------------------------------------------------------ erros de cota x erros temporários

def corpo_429(quota_id=None, mensagem="You exceeded your current quota, please check your plan and billing details."):
    erro = {"code": 429, "message": mensagem, "status": "RESOURCE_EXHAUSTED"}
    if quota_id:
        erro["details"] = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure",
                            "violations": [{"quotaMetric": "generativelanguage.googleapis.com/generate_content_requests",
                                            "quotaId": quota_id}]}]
    return {"error": erro}


def test_429_de_cota_diaria_nao_e_repetido():
    s = SessaoFalsa(RespostaHTTP(429, corpo_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier")), ok("{}"))
    with pytest.raises(gemini.GeminiCotaErro):
        cliente(s).gerar("x")
    assert len(s.chamadas) == 1                       # parou na primeira, sem retentativa


@pytest.mark.parametrize("corpo", [
    corpo_429(mensagem="Daily limit reached"),
    corpo_429(mensagem="Billing is not enabled for this project"),
    corpo_429(mensagem="You exceeded your current quota"),
    corpo_429("GenerateContentInputTokensPerModelPerDay-FreeTier"),
])
def test_429_com_mensagem_de_cota_nao_e_repetido(corpo):
    s = SessaoFalsa(RespostaHTTP(429, corpo), ok("{}"))
    with pytest.raises(gemini.GeminiCotaErro):
        cliente(s).gerar("x")
    assert len(s.chamadas) == 1


def test_cota_e_um_tipo_de_erro_do_gemini():
    assert issubclass(gemini.GeminiCotaErro, GeminiErro)


def test_429_por_minuto_e_repetido_com_espera_curta():
    s = SessaoFalsa(RespostaHTTP(429, corpo_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier")),
                    RespostaHTTP(429, corpo_429(mensagem="Rate limit: too many requests per minute")), ok('{"a":1}'))
    assert cliente(s).gerar("x") == '{"a":1}'
    assert len(s.chamadas) == 3


def test_429_generico_sem_pista_de_cota_e_tratado_como_temporario():
    s = SessaoFalsa(RespostaHTTP(429, {"error": {"message": "Too many requests"}}), ok("{}"))
    assert cliente(s).gerar("x") == "{}"
    assert len(s.chamadas) == 2


def test_503_e_erro_de_rede_continuam_sendo_repetidos():
    class Cai:
        def __init__(self):
            self.n = 0

        def post(self, *a, **k):
            self.n += 1
            if self.n == 1:
                raise requests.ConnectionError("rede caiu")
            if self.n == 2:
                return RespostaHTTP(503, {})
            return ok("{}")

    s = Cai()
    assert cliente(s).gerar("x") == "{}" and s.n == 3
