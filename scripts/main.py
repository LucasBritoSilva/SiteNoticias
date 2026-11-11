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

import coleta
import paginas
import resumos
import site_builder
from gemini import GeminiResumidor
from resumos import Resumo
from site_builder import Item

RAIZ = Path(__file__).resolve().parent.parent
TENTATIVAS = 3
ESPERA_ENTRE_TENTATIVAS = 15 * 60  # segundos

log = logging.getLogger("pipeline")


def executar(raiz: Path, resumidor: resumos.Resumidor, sessao=None, agora: datetime | None = None) -> int:
    """Roda o pipeline uma vez. Devolve a quantidade de notícias publicadas; levanta erro se falhar."""
    agora = agora or datetime.now(timezone.utc)
    docs = raiz / "docs"
    staging = raiz / ".staging"
    if staging.exists():
        shutil.rmtree(staging)

    nichos = coleta.carregar_nichos(raiz / "nichos.json")
    log.info("Nichos: %s", nichos)
    artigos = coleta.coletar(nichos, agora)
    if len(artigos) < 2:
        raise RuntimeError("Notícias insuficientes nas últimas 24 horas.")

    # Uma única chamada ao provedor: agrupa, escolhe e resume.
    grupos = resumidor.resumir_grupos(artigos, nichos, resumos.TOTAL_GRUPOS)
    if not grupos:
        raise RuntimeError("Nenhum grupo válido foi gerado.")

    robots = paginas.Robots(sessao)
    itens: list[Item] = []
    for posicao, grupo in enumerate(grupos, 1):
        paginas.enriquecer_artigos(grupo.artigos, robots, sessao)  # URL original e og:image
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
    log.info("Site publicado em %s com %d notícias.", docs, len(itens))
    return len(itens)


def com_tentativas(
    tarefa: Callable[[], object],
    tentativas: int = TENTATIVAS,
    espera: float = ESPERA_ENTRE_TENTATIVAS,
    dormir: Callable[[float], None] = time.sleep,
) -> bool:
    """Executa `tarefa` até `tentativas` vezes, esperando `espera` s entre elas."""
    for n in range(1, tentativas + 1):
        try:
            tarefa()
            return True
        except Exception:  # noqa: BLE001 - qualquer falha dispara nova tentativa
            log.exception("Tentativa %d/%d falhou", n, tentativas)
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
        log.error("Todas as tentativas falharam; docs/ não foi alterado.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
