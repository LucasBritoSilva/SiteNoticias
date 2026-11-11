"""Cache de resumos: reaproveitamento, validade de 48 h e conteúdo seguro. Gemini sempre SIMULADO."""
import json
from datetime import timedelta

import pytest

import cache
import main
from coleta import Artigo
from gemini import GeminiResumidor
from resumos import Grupo
from test_site import AGORA, FIXTURES, GeminiSimulado, raiz  # noqa: F401  (raiz é uma fixture)

PARES = [[i, i + 1] for i in range(0, 18, 2)]  # 9 grupos de 2 fontes


def executar(raiz, ids, agora=AGORA, **kw):
    cliente = GeminiSimulado(ids, **kw)
    return main.executar(raiz, GeminiResumidor(cliente), agora=agora), cliente


def arquivo(raiz):
    return raiz / "dados" / "resumos_gerados.json"


def artigos_do_exemplo():
    dados = json.loads((FIXTURES / "exemplo_coleta.json").read_text(encoding="utf-8"))
    return [Artigo(**a) for a in dados["artigos"]]


# ------------------------------------------------------------ pipeline com cache

def test_primeira_execucao_chama_o_gemini_uma_vez_e_grava_o_cache(raiz):
    n, cliente = executar(raiz, PARES)
    assert n == 9 and len(cliente.prompts) == 1
    dados = json.loads(arquivo(raiz).read_text(encoding="utf-8"))
    assert len(dados["grupos"]) == 9
    g = dados["grupos"][0]
    assert {"id", "gerado_em", "titulo", "resumo", "fontes"} <= set(g)
    assert all(set(f) >= {"nome", "url", "link"} for f in g["fontes"])


def test_segunda_execucao_com_tudo_em_cache_nao_chama_o_gemini(raiz):
    executar(raiz, PARES)
    n, cliente = executar(raiz, PARES, agora=AGORA + timedelta(hours=12))
    assert n == 9
    assert cliente.prompts == []                          # zero chamadas
    depois = (raiz / "docs" / "index.html").read_text(encoding="utf-8")
    assert depois.count('class="cartao"') == 9
    for i in range(9):
        assert f"Resumo simulado do grupo {i}" in depois  # mesmos resumos, reaproveitados


def test_so_os_grupos_novos_vao_ao_gemini(raiz):
    executar(raiz, PARES)
    # simula que só 5 dos 9 resumos continuam no cache
    mantidas = cache.carregar(arquivo(raiz), AGORA)[:5]
    cache.salvar(arquivo(raiz), mantidas, AGORA)
    ids_novos = [[i, i + 1] for i in range(0, 8, 2)]      # índices dentro da lista de candidatos novos
    n, cliente = executar(raiz, ids_novos, agora=AGORA + timedelta(hours=1), prefixo="novo")
    assert len(cliente.prompts) == 1
    prompt = cliente.prompts[0]
    assert "6 grupos" in prompt                           # 9 vagas - 5 reaproveitadas = 4, + 2 de reserva
    # as manchetes já resumidas não foram reenviadas
    arts = {a.link: a for a in artigos_do_exemplo()}
    for e in mantidas:
        for f in e.fontes:
            assert arts[f["link"]].titulo not in prompt
    index = (raiz / "docs" / "index.html").read_text(encoding="utf-8")
    assert n == 9 and index.count("Resumo simulado novo") == 4
    assert len(cache.carregar(arquivo(raiz), AGORA + timedelta(hours=1))) == 9   # 5 antigos + 4 novos


def test_entradas_com_mais_de_48h_nao_sao_reaproveitadas(raiz):
    executar(raiz, PARES)
    n, cliente = executar(raiz, PARES, agora=AGORA + timedelta(hours=49), prefixo="refeito")
    assert len(cliente.prompts) == 1 and n == 9
    assert "Resumo simulado refeito" in (raiz / "docs" / "index.html").read_text(encoding="utf-8")


def test_cache_corrompido_equivale_a_cache_vazio(raiz):
    arquivo(raiz).parent.mkdir(parents=True)
    arquivo(raiz).write_text("{isto não é json", encoding="utf-8")
    n, cliente = executar(raiz, PARES)
    assert n == 9 and len(cliente.prompts) == 1
    assert json.loads(arquivo(raiz).read_text(encoding="utf-8"))["grupos"]


def test_falha_nao_altera_o_cache(raiz):
    executar(raiz, PARES)
    cheio = arquivo(raiz).read_text(encoding="utf-8")
    cache.salvar(arquivo(raiz), [], AGORA)               # esvazia para forçar nova chamada
    vazio = arquivo(raiz).read_text(encoding="utf-8")
    with pytest.raises(Exception):
        executar(raiz, PARES, quebrar=True)
    assert arquivo(raiz).read_text(encoding="utf-8") == vazio != cheio


# ------------------------------------------------------------ unidades

def grupo_exemplo(i, j, rel=5):
    arts = artigos_do_exemplo()
    return Grupo("ia", rel, [arts[i], arts[j]], f"Título {i}", "Resumo " * 12)


def test_id_do_grupo_nao_depende_da_ordem():
    a, b = artigos_do_exemplo()[:2]
    assert cache.id_do_grupo([a.link, b.link]) == cache.id_do_grupo([b.link, a.link])
    assert cache.id_do_grupo([a.link]) != cache.id_do_grupo([a.link, b.link])


def test_ida_e_volta_do_grupo_preserva_urls_e_imagens():
    g = grupo_exemplo(0, 1)
    g.artigos[0].url, g.artigos[0].imagem_url = "https://orig.com/a", "https://orig.com/a.jpg"
    g.artigos[1].url = "https://orig2.com/b"
    volta = cache.grupo_de_entrada(cache.entrada_de_grupo(g, AGORA))
    assert [a.url for a in volta.artigos] == ["https://orig.com/a", "https://orig2.com/b"]
    assert [a.imagem_url for a in volta.artigos] == ["https://orig.com/a.jpg", ""]
    assert (volta.titulo, volta.resumo, volta.nicho) == (g.titulo, g.resumo, g.nicho)


def test_planejar_separa_reaproveitadas_e_novos():
    arts = artigos_do_exemplo()
    e1 = cache.entrada_de_grupo(grupo_exemplo(0, 1, rel=9), AGORA)
    e2 = cache.entrada_de_grupo(grupo_exemplo(2, 3, rel=4), AGORA)
    fora = Artigo("x", "https://news.google.com/rss/articles/FORA", "Z", "", "", "ia", url="u")
    e3 = cache.entrada_de_grupo(Grupo("ia", 10, [fora, fora], "T", "R"), AGORA)   # nenhuma fonte no feed
    reaproveitadas, novos = cache.planejar([e2, e3, e1], arts, total=9)
    assert [e.id for e in reaproveitadas] == [e1.id, e2.id]       # por relevância; e3 fora do feed
    assert {a.link for a in novos}.isdisjoint({a.link for a in arts[:4]})
    assert len(novos) == len(arts) - 4
    assert len(cache.planejar([e1, e2], arts, total=1)[0]) == 1


def test_validade_de_48h_na_leitura(tmp_path):
    caminho = tmp_path / "c.json"
    velho = cache.entrada_de_grupo(grupo_exemplo(0, 1), AGORA - timedelta(hours=49))
    novo = cache.entrada_de_grupo(grupo_exemplo(2, 3), AGORA - timedelta(hours=47))
    cache.salvar(caminho, [velho, novo], AGORA - timedelta(hours=47))
    assert [e.id for e in cache.carregar(caminho, AGORA)] == [novo.id]
    assert cache.carregar(tmp_path / "nao-existe.json", AGORA) == []


def test_cache_nao_guarda_segredos(tmp_path, raiz):
    # 1) o que é gravado numa execução normal não contém nada parecido com chave
    executar(raiz, PARES)
    texto = arquivo(raiz).read_text(encoding="utf-8")
    for padrao in cache.PADROES_DE_SEGREDO:
        assert not padrao.search(texto)
    assert set(json.loads(texto)) == {"versao", "grupos"}
    # 2) e uma entrada suspeita é recusada
    suspeita = cache.entrada_de_grupo(grupo_exemplo(0, 1), AGORA)
    suspeita.resumo = "AIza" + "x" * 35
    with pytest.raises(ValueError):
        cache.salvar(tmp_path / "c.json", [suspeita], AGORA)
    assert not (tmp_path / "c.json").exists()
