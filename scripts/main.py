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
from typing import Callable

import requests

import cache
import coleta
import paginas
import resumos
import site_builder
from gemini import GeminiCotaErro, GeminiErro, GeminiResumidor, RespostaInvalida
from resumos import Resumo
from site_builder import Item

RAIZ = Path(__file__).resolve().parent.parent
TENTATIVAS = 3
ESPERA_ENTRE_TENTATIVAS = 15 * 60  # segundos
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

    # 1) O que já foi resumido nas últimas 48 h não vai de novo ao Gemini.
    entradas = cache.carregar(arquivo_cache, agora)
    reaproveitadas, novos_artigos = cache.planejar(entradas, artigos, resumos.TOTAL_GRUPOS)
    faltam = resumos.TOTAL_GRUPOS - len(reaproveitadas)

    # 2) Só se faltarem grupos e houver notícias novas, uma única chamada ao provedor.
    novos: list[resumos.Grupo] = []
    chamadas = 0
    if faltam > 0 and len(novos_artigos) >= MIN_NOVOS_PARA_CHAMAR:
        chamadas = 1
        novos = resumidor.resumir_grupos(novos_artigos, nichos, faltam)
    log.info(
        "Cache: %d entradas válidas, %d reaproveitadas, %d notícias novas, %d grupos novos; "
        "chamadas ao Gemini nesta execução: %d",
        len(entradas), len(reaproveitadas), len(novos_artigos), len(novos), chamadas,
    )
    robots = paginas.Robots(sessao)
    for grupo in novos:
        paginas.enriquecer_artigos(grupo.artigos, robots, sessao)  # URL original e og:image
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
        return "COTA ESGOTADA"
    if isinstance(exc, RespostaInvalida):
        return "RESPOSTA INVÁLIDA"
    if isinstance(exc, (GeminiErro, requests.RequestException)):
        return "TEMPORÁRIO (rede/instabilidade/limite por minuto)"
    return "OUTRO"


def com_tentativas(
    tarefa: Callable[[], object],
    tentativas: int = TENTATIVAS,
    espera: float = ESPERA_ENTRE_TENTATIVAS,
    dormir: Callable[[float], None] = time.sleep,
) -> bool:
    """Executa `tarefa` até `tentativas` vezes, esperando `espera` s entre elas.

    Erro de cota não é repetido nem entra na contagem: a execução para na hora.
    """
    for n in range(1, tentativas + 1):
        try:
            tarefa()
            return True
        except GeminiCotaErro as exc:
            log.error("EXECUÇÃO INTERROMPIDA POR ERRO DE COTA (sem novas tentativas): %s", exc)
            return False
        except Exception as exc:  # noqa: BLE001 - qualquer outra falha dispara nova tentativa
            log.exception("Tentativa %d/%d falhou. Tipo de erro: %s", n, tentativas, tipo_do_erro(exc))
            if n < tentativas:
                log.info("Aguardando %.0f min para tentar de novo...", espera / 60)
                dormir(espera)
    return False


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    # A chave é lida só da variável de ambiente GEMINI_API_KEY. Sem ela não há o que tentar.
    try:
        resumidor = GeminiResumidor()
    except Exception as exc:  # noqa: BLE001
        log.error("%s", exc)
        return 1
    ok = com_tentativas(lambda: executar(RAIZ, resumidor))
    if not ok:
        log.error("Execução encerrada sem atualizar o site: docs/ e dados/ foram mantidos como estavam.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
