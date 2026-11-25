"""Ponto de entrada do pipeline: python scripts/main.py

Fluxo: nichos -> RSS -> deduplicação -> UMA chamada ao Gemini (grupos + top 9 + resumos)
-> URLs originais e imagens -> site em pasta temporária -> troca de docs/ somente se tudo deu certo.
"""
from __future__ import annotations

import logging
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

import cache
import coleta
import paginas
import resumos
import site_builder
from gemini import GeminiCotaErro, GeminiErro, GeminiIndisponivel, GeminiResumidor, RespostaInvalida
from resumos import Resumo
from site_builder import Item

RAIZ = Path(__file__).resolve().parent.parent
# Este script faz UMA tentativa e sai com 0 (publicou) ou 1 (falhou). A nova tentativa, uma só e
# 1 hora depois, é decidida apenas pelo workflow (.github/workflows/atualizar-site.yml).
MIN_NOVOS_PARA_CHAMAR = 2  # com menos de 2 notícias novas não vale gastar uma chamada

log = logging.getLogger("pipeline")


def executar(raiz: Path, resumidor: resumos.Resumidor, sessao=None, agora: datetime | None = None) -> int:
    """Roda o pipeline uma vez. Devolve a quantidade de notícias publicadas; levanta erro se falhar."""
    agora = agora or datetime.now(timezone.utc)
    docs = raiz / "docs"
    staging = raiz / ".staging"
    arquivo_cache = raiz / "dados" / "resumos_gerados.json"
    if staging.exists():
        shutil.rmtree(staging)

    nichos = coleta.carregar_nichos(raiz / "nichos.json")
    log.info("Nichos: %s", nichos)
    artigos = coleta.coletar(nichos, agora)
    if len(artigos) < 2:
        raise RuntimeError("Notícias insuficientes nas últimas 24 horas.")

    # 1) O que já foi resumido nas últimas 24 h não vai de novo ao Gemini.
    entradas = cache.carregar(arquivo_cache, agora)
    reaproveitadas, novos_artigos = cache.planejar(entradas, artigos, resumos.TOTAL_GRUPOS)
    faltam = resumos.TOTAL_GRUPOS - len(reaproveitadas)

    # 2) Só se faltarem grupos e houver notícias novas, uma única chamada ao provedor.
    #    Antes dela, lê o texto de um conjunto limitado de matérias para o resumo poder ser elaborado.
    robots = paginas.Robots(sessao)
    novos: list[resumos.Grupo] = []
    chamadas = 0
    if faltam > 0 and len(novos_artigos) >= MIN_NOVOS_PARA_CHAMAR:
        pool = resumos.selecionar_pool(novos_artigos, min(resumos.TAMANHO_POOL, max(12, faltam * 4)))
        paginas.enriquecer_artigos(pool, robots, sessao)  # URL original, texto e og:image
        com_texto = [a for a in pool if a.trecho]
        if len(com_texto) < MIN_NOVOS_PARA_CHAMAR:
            log.warning("Só %d matérias tiveram o texto lido; usando as manchetes.", len(com_texto))
            com_texto = pool
        log.info("Texto lido de %d das %d matérias escolhidas", sum(1 for a in pool if a.trecho), len(pool))
        chamadas = 1
        # Pelo menos uma notícia de cada nicho: o Gemini precisa cobrir os que o cache não cobre.
        cobertos = {resumos.chave_nicho(e.nicho) for e in reaproveitadas}
        garantir = [n for n in nichos if resumos.chave_nicho(n) not in cobertos]
        novos = resumidor.resumir_grupos(com_texto, nichos, faltam, garantir)
    log.info(
        "Cache: %d entradas válidas, %d reaproveitadas, %d notícias novas, %d grupos novos; "
        "chamadas ao Gemini nesta execução: %d",
        len(entradas), len(reaproveitadas), len(novos_artigos), len(novos), chamadas,
    )
    grupos = [cache.grupo_de_entrada(e) for e in reaproveitadas] + novos
    if not grupos:
        raise RuntimeError("Nenhum grupo para publicar.")
    grupos.sort(key=lambda g: (len(g.artigos) < 2, -g.relevancia))  # 2+ fontes primeiro, por relevância

    itens: list[Item] = []
    for posicao, grupo in enumerate(grupos, 1):
        resumo = Resumo(
            titulo=grupo.titulo,
            resumo=grupo.resumo,
            fontes=[{"nome": a.fonte, "url": a.url or a.link} for a in grupo.artigos],
        )
        slug = f"{posicao:02d}-{site_builder.slugify(resumo.titulo)}"
        imagem = paginas.imagem_do_grupo(grupo.artigos, staging / "img", slug, robots, sessao)
        log.info("[%02d] %s (%d fontes, imagem: %s)", posicao, resumo.titulo, len(grupo.artigos), imagem or "genérica")
        itens.append(Item(slug=slug, nicho=grupo.nicho, resumo=resumo, imagem=imagem))

    site_builder.gerar_site(itens, nichos, staging, agora)
    site_builder.publicar(staging, docs)
    shutil.rmtree(staging, ignore_errors=True)
    # O cache só é atualizado depois que o site foi publicado.
    cache.salvar(arquivo_cache, entradas + [cache.entrada_de_grupo(g, agora) for g in novos], agora)
    log.info("Site publicado em %s com %d notícias.", docs, len(itens))
    return len(itens)


def tipo_do_erro(exc: BaseException) -> str:
    """Rótulo usado no log para deixar claro o que parou a execução."""
    if isinstance(exc, GeminiCotaErro):
        return "LIMITE DE USO EXCEDIDO (HTTP 429)"
    if isinstance(exc, GeminiIndisponivel):
        return "GEMINI INDISPONÍVEL (HTTP 5xx, rede ou timeout)"
    if isinstance(exc, RespostaInvalida):
        return "RESPOSTA INVÁLIDA"
    if isinstance(exc, GeminiErro):
        return "ERRO DO GEMINI (configuração ou requisição recusada)"
    if isinstance(exc, requests.RequestException):
        return "REDE"
    return "OUTRO"


def configurar_log() -> None:
    """Todos os horários do log em UTC, com o fuso escrito em cada linha."""
    formato = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S UTC")
    formato.converter = time.gmtime
    saida = logging.StreamHandler()
    saida.setFormatter(formato)
    logging.basicConfig(level=logging.INFO, handlers=[saida])


def registrar_falha(exc: BaseException, chamadas: object) -> None:
    log.error(
        "RESUMO DA TENTATIVA: FALHOU | requisições ao Gemini: %s | motivo: %s (%s) | nada a publicar.",
        chamadas, tipo_do_erro(exc), exc,
    )


def main() -> int:
    """Faz uma tentativa de atualização. Devolve 0 se o site foi gerado e 1 em qualquer falha."""
    configurar_log()
    log.info("Início da geração.")
    # A chave é lida só da variável de ambiente GEMINI_API_KEY.
    try:
        resumidor = GeminiResumidor()
    except Exception as exc:  # noqa: BLE001
        registrar_falha(exc, 0)
        return 1
    cliente = getattr(resumidor, "cliente", None)
    log.info("Modelo Gemini: %s", getattr(cliente, "modelo", "?"))
    try:
        executar(RAIZ, resumidor)
    except Exception as exc:  # noqa: BLE001 - qualquer falha encerra a tentativa
        if not isinstance(exc, (GeminiErro, RespostaInvalida)):
            log.exception("Erro inesperado durante a geração.")
        registrar_falha(exc, getattr(cliente, "chamadas", "?"))
        return 1
    log.info("RESUMO DA TENTATIVA: OK | requisições ao Gemini: %s", getattr(cliente, "chamadas", "?"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
