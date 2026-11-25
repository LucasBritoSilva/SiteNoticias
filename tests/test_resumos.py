"""Testes da chamada única ao Gemini, das regras dos grupos e do cliente REST.

NÃO há GEMINI_API_KEY neste ambiente de desenvolvimento: todas as respostas do Gemini aqui
são SIMULADAS (cliente falso e sessão HTTP falsa). Nenhuma chamada real à API foi testada.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import requests

import coleta
import gemini
import resumos
from coleta import Artigo
from gemini import GeminiClient, GeminiErro, RespostaInvalida

FIXTURES = Path(__file__).parent / "fixtures"
RESUMO_LONGO = ("Frase simulada do resumo, unindo as informações das fontes do grupo com contexto e detalhes. " * 8).strip()


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
        "resumo": resumo or RESUMO_LONGO,
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
    assert "[0] Veiculo0 | Fato 0" in p and "[1] Veiculo1 | Fato 1" in p
    assert '["ia", "economia"]' in p and "11 grupos" in p       # 9 + reserva de 2
    assert "SOMENTE o que está nos textos" in p and "nunca invente ids" in p


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


def test_json_invalido_falha_na_hora_sem_nova_chamada():
    arts = [art(i) for i in range(4)]
    bom = {"grupos": [item([0, 1], 8)]}
    r, cliente = resumidor("isto não é json", bom)
    with pytest.raises(RespostaInvalida, match="sem nova tentativa"):
        r.resumir_grupos(arts, ["ia"], 3)
    assert len(cliente.prompts) == 1          # erro não transitório: uma chamada só


def test_estrutura_invalida_falha_na_hora_sem_nova_chamada():
    r, cliente = resumidor({"grupos": "x"}, {"outra": 1})
    with pytest.raises(RespostaInvalida):
        r.resumir_grupos([art(0), art(1)], ["ia"], 3)
    assert len(cliente.prompts) == 1


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
    return GeminiClient(chave="chave-de-teste", sessao=sessao, intervalo_minimo=0, **kw)


def test_cliente_envia_chave_no_cabecalho_e_nao_na_url():
    s = SessaoFalsa(ok('{"a":1}'))
    assert cliente(s).gerar("oi") == '{"a":1}'
    url, headers, corpo = s.chamadas[0]
    assert "chave-de-teste" not in url and headers["x-goog-api-key"] == "chave-de-teste"
    assert corpo["generationConfig"]["responseMimeType"] == "application/json"
    assert "generateContent" in url


def test_cliente_resposta_sem_texto_e_invalida():
    s = SessaoFalsa(RespostaHTTP(200, {"promptFeedback": {"blockReason": "SAFETY"}}))
    with pytest.raises(RespostaInvalida):
        cliente(s).gerar("x")


def test_cliente_exige_chave(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(GeminiErro):
        GeminiClient()


# ------------------------------------------------------------ uma requisição só, qualquer que seja o erro

def corpo_429(quota_id=None, mensagem="You exceeded your current quota, please check your plan and billing details.",
              metrica="generativelanguage.googleapis.com/generate_content_free_tier_requests", valor=None):
    erro = {"code": 429, "message": mensagem, "status": "RESOURCE_EXHAUSTED"}
    if quota_id:
        violacao = {"quotaMetric": metrica, "quotaId": quota_id}
        if valor:
            violacao["quotaValue"] = valor
        erro["details"] = [{"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [violacao]},
                           {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "7s"}]
    return {"error": erro}


@pytest.mark.parametrize("status,classe", [
    (429, gemini.GeminiCotaErro),
    (500, gemini.GeminiIndisponivel), (502, gemini.GeminiIndisponivel),
    (503, gemini.GeminiIndisponivel), (504, gemini.GeminiIndisponivel),
    (400, GeminiErro), (401, GeminiErro), (403, GeminiErro), (404, GeminiErro),
])
def test_cada_erro_http_faz_uma_unica_requisicao_sem_esperar(monkeypatch, status, classe):
    esperas = []
    monkeypatch.setattr(gemini.time, "sleep", esperas.append)
    s = SessaoFalsa(RespostaHTTP(status, {"error": {"message": "x"}}), ok("{}"))
    c = cliente(s)
    with pytest.raises(classe, match=f"HTTP {status}") as e:
        c.gerar("x")
    assert type(e.value) is classe
    assert len(s.chamadas) == 1 and c.chamadas == 1 and esperas == []


@pytest.mark.parametrize("excecao,texto", [
    (requests.ReadTimeout("Read timed out."), "Timeout de 60s"),
    (requests.ConnectTimeout("sem conexão"), "Timeout de 60s"),
    (requests.ConnectionError("rede caiu"), "Falha de comunicação"),
])
def test_timeout_e_erro_de_rede_nao_sao_repetidos(excecao, texto):
    class Cai:
        n, timeouts = 0, []

        def post(self, url, headers=None, json=None, timeout=None):
            self.n += 1
            self.timeouts.append(timeout)
            raise excecao

    s = Cai()
    with pytest.raises(gemini.GeminiIndisponivel, match=texto):
        cliente(s).gerar("x")
    assert s.n == 1 and s.timeouts[-1] == gemini.TIMEOUT_PADRAO == 60


@pytest.mark.parametrize("status", [429, 503])
def test_requests_de_verdade_nao_repete_nem_com_retry_after(monkeypatch, status):
    """Sessão real do requests contra um servidor local: a biblioteca não pode repetir sozinha."""
    pedidos = []

    class Servidor(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            pedidos.append(self.path)
            self.send_response(status)
            self.send_header("Retry-After", "0")
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass

    servidor = HTTPServer(("127.0.0.1", 0), Servidor)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    try:
        monkeypatch.setattr(gemini, "API_URL", f"http://127.0.0.1:{servidor.server_port}/{{modelo}}")
        sessao = requests.Session()
        sessao.trust_env = False  # sem proxy do ambiente
        with pytest.raises(GeminiErro, match=f"HTTP {status}"):
            GeminiClient(chave="chave-de-teste", modelo="m", sessao=sessao).gerar("x")
    finally:
        servidor.shutdown()
        servidor.server_close()
    assert pedidos == ["/m"]


def test_chave_nao_aparece_nas_mensagens_de_erro():
    s = SessaoFalsa(RespostaHTTP(400, {"error": {"message": "chave chave-de-teste inválida"}}))
    with pytest.raises(GeminiErro) as e:
        cliente(s).gerar("x")
    assert "chave-de-teste" not in str(e.value) and "***" in str(e.value)
    assert GeminiClient(chave="  chave-com-quebra\n").chave == "chave-com-quebra"


# ------------------------------------------------------------ qual limite o 429 estourou

TOKENS = "generativelanguage.googleapis.com/generate_content_free_tier_input_token_count"


@pytest.mark.parametrize("corpo,esperado", [
    (corpo_429("GenerateRequestsPerMinutePerProjectPerModel-FreeTier"), "RPM (requisições por minuto)"),
    (corpo_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier", valor="20"), "RPD (requisições por dia)"),
    (corpo_429("GenerateContentInputTokensPerModelPerMinute-FreeTier", metrica=TOKENS),
     "TPM (tokens por minuto)"),
    (corpo_429("GenerateContentInputTokensPerModelPerDay-FreeTier", metrica=TOKENS), "TPD (tokens por dia)"),
    (corpo_429(mensagem="Too many requests per minute"), "RPM (requisições por minuto)"),
    (corpo_429(mensagem="Daily limit reached"), "limite por dia (a API não disse"),
    (corpo_429(), "não informada pela API"),                    # "quota" sozinho não vira cota diária
    ({}, "não informada pela API"),
])
def test_429_registra_a_metrica_sem_presumir_cota_diaria(corpo, esperado):
    metrica = gemini.metrica_do_429(RespostaHTTP(429, corpo))
    assert esperado in metrica
    if "dia" not in esperado:
        assert "por dia" not in metrica and "RPD" not in metrica


def test_429_com_varias_violacoes_lista_todas_com_o_limite():
    corpo = corpo_429("GenerateRequestsPerDayPerProjectPerModel-FreeTier", valor="20")
    corpo["error"]["details"][0]["violations"].append(
        {"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier", "quotaValue": "5"})
    with pytest.raises(gemini.GeminiCotaErro) as e:
        cliente(SessaoFalsa(RespostaHTTP(429, corpo))).gerar("x")
    texto = str(e.value)
    assert "RPD (requisições por dia)" in texto and "limite 20" in texto
    assert "RPM (requisições por minuto)" in texto and "limite 5]" in texto


def test_cota_e_um_tipo_de_erro_do_gemini():
    assert issubclass(gemini.GeminiCotaErro, GeminiErro)


def test_corpo_200_que_nao_e_json_e_resposta_invalida_sem_retentativa():
    class R:
        status_code, text = 200, "<html>"

        def json(self):
            raise ValueError("não é JSON")

    s = SessaoFalsa(R(), ok("{}"))
    with pytest.raises(RespostaInvalida):
        cliente(s).gerar("x")
    assert len(s.chamadas) == 1


# ------------------------------------------------------------ resumos elaborados (texto das matérias)

def test_prompt_envia_o_texto_das_materias_e_pede_resumo_elaborado():
    a, b = art(0), art(1)
    a.trecho = "Texto completo da primeira matéria com detalhes."
    r, cliente = resumidor({"grupos": [item([0, 1])]})
    r.resumir_grupos([a, b], ["ia"], total=3)
    p = cliente.prompts[0]
    assert "Texto: Texto completo da primeira matéria com detalhes." in p
    assert "indisponível" in p                                  # a segunda não teve o texto lido
    assert "3 a 5 parágrafos" in p and "evite frases vagas, genéricas" in p
    assert "divergência" in p and "Se faltar detalhe, escreva menos em vez de inventar" in p


def test_resumo_curto_e_recusado_quando_ha_texto_das_materias():
    a, b = art(0), art(1)
    a.trecho = b.trecho = "texto da matéria"
    curto = item([0, 1], resumo="Resumo de apenas duas frases curtas, genérico demais para publicar. " * 2)
    longo = item([0, 1], resumo=RESUMO_LONGO)
    r, _ = resumidor({"grupos": [curto]}, {"grupos": [curto]})
    with pytest.raises(RespostaInvalida):                       # curto nas duas tentativas
        r.resumir_grupos([a, b], ["ia"], 3)
    r2, _ = resumidor({"grupos": [curto, longo]})
    assert [g.resumo for g in r2.resumir_grupos([a, b], ["ia"], 3)] == [RESUMO_LONGO]


def test_so_com_manchetes_o_minimo_e_menor():
    curto = item([0, 1], resumo="Resumo curto, mas aceitável porque nenhuma matéria teve o texto lido.")
    r, _ = resumidor({"grupos": [curto]})
    assert len(r.resumir_grupos([art(0), art(1)], ["ia"], 3)) == 1


def art_t(i, titulo, fonte, nicho="ia", hora=10):
    return Artigo(titulo=titulo, link=f"https://news.google.com/rss/articles/{i}", fonte=fonte, fonte_url="",
                  publicado=f"2026-11-11T{hora:02d}:00:00+00:00", nicho=nicho)


def test_pool_prioriza_assuntos_com_varios_veiculos_e_limita_por_assunto():
    mec = [art_t(i, "MEC aprova regras para uso de inteligência artificial nas escolas", f"V{i}") for i in range(6)]
    assuntos = ["Petrobras anuncia dividendos extraordinários", "Santos contrata zagueiro uruguaio",
                "Cientistas descobrem novo exoplaneta habitável", "Prefeitura reabre ponte interditada",
                "Vacina contra dengue ganha aprovação federal"]
    soltas = [art_t(10 + i, t, f"W{i}", hora=11) for i, t in enumerate(assuntos)]
    pool = resumos.selecionar_pool(soltas + mec, tamanho=5)
    assert len(pool) == 5
    assert sum(1 for a in pool if a.titulo.startswith("MEC")) == resumos.MAX_POR_ASSUNTO_NO_POOL   # 3 de 6
    # tamanho maior que o disponível devolve tudo o que couber, sem repetir
    tudo = resumos.selecionar_pool(soltas + mec, tamanho=50)
    assert len(tudo) == len({a.link for a in tudo}) == 5 + resumos.MAX_POR_ASSUNTO_NO_POOL


def test_pool_alterna_entre_nichos():
    ta = ["Bolsa fecha estável", "Chuvas atingem Sul", "Eleição municipal começa", "Dólar recua semana",
          "Petróleo dispara Oriente", "Safra recorde soja", "Inflação desacelera outubro", "Greve metroviários termina"]
    tb = ["Corinthians vence clássico", "Final libertadores sorteada", "Tenista brasileiro avança", "Fórmula-1 estreia pista",
          "Seleção convoca goleiro", "Maratona reúne corredores", "Vôlei conquista ouro", "Natação bate recorde"]
    a = [art_t(i, t, f"A{i}", nicho="a") for i, t in enumerate(ta)]
    b = [art_t(20 + i, t, f"B{i}", nicho="b") for i, t in enumerate(tb)]
    pool = resumos.selecionar_pool(a + b, tamanho=6)
    assert sorted(x.nicho for x in pool) == ["a"] * 3 + ["b"] * 3


def test_pool_com_dados_reais_do_exemplo():
    dados = json.loads((FIXTURES / "exemplo_coleta.json").read_text(encoding="utf-8"))
    arts = [Artigo(**x) for x in dados["artigos"]]
    pool = resumos.selecionar_pool(arts, tamanho=12)
    assert len(pool) == 12
    assert sum("MEC" in x.titulo for x in pool) <= resumos.MAX_POR_ASSUNTO_NO_POOL + 1


# ------------------------------------------------------------ pelo menos uma notícia de cada nicho


def test_cada_nicho_tem_pelo_menos_um_grupo_mesmo_avulso():
    # 4 grupos de "ia" muito relevantes e uma notícia avulsa de "fosseis" pouco relevante
    arts = [art(i, nicho="ia") for i in range(8)] + [art(8, nicho="fosseis")]
    resposta = {"grupos": [item([0, 1], 9), item([2, 3], 8), item([4, 5], 7), item([6, 7], 6),
                           item([8], 1, nicho="Fósseis")]}
    r, cliente = resumidor(resposta)
    grupos = r.resumir_grupos(arts, ["ia", "fosseis"], total=3)
    assert [g.nicho for g in grupos] == ["ia", "ia", "fosseis"]   # o avulso de fosseis toma a última vaga
    assert "Obrigatório" in cliente.prompts[0] and '"fosseis"' in cliente.prompts[0]


def test_garantir_limita_os_nichos_obrigatorios():
    arts = [art(i, nicho="ia") for i in range(4)] + [art(4, nicho="fosseis")]
    resposta = {"grupos": [item([0, 1], 9), item([2, 3], 8), item([4], 1, nicho="fosseis")]}
    r, cliente = resumidor(resposta)
    grupos = r.resumir_grupos(arts, ["ia", "fosseis"], total=2, garantir=["ia"])
    assert [g.nicho for g in grupos] == ["ia", "ia"]
    assert '["ia"]' in cliente.prompts[0]


def test_escolher_com_cobertura_mantem_a_ordem():
    itens = ["a1", "a2", "a3", "b1", "c1"]
    escolhidos = resumos.escolher_com_cobertura(itens, 3, lambda s: s[0], ["a", "b", "c"])
    assert escolhidos == ["a1", "b1", "c1"]
    assert resumos.escolher_com_cobertura(itens, 2, lambda s: s[0], ["c"]) == ["a1", "c1"]
    assert resumos.escolher_com_cobertura(itens, 9, lambda s: s[0], ["z"]) == itens
