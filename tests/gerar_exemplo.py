"""Gera um site de demonstração com dados SIMULADOS (sem Gemini, sem rede) para teste visual.

Uso: python tests/gerar_exemplo.py <pasta_de_saida> [pasta_com_imagens]
"""
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ / "scripts"))

import site_builder  # noqa: E402
from resumos import Resumo  # noqa: E402
from site_builder import Item  # noqa: E402

TITULOS = [
    "MEC define regras para o uso de inteligência artificial em escolas e universidades do país",
    "Banco Central mantém a taxa básica de juros e sinaliza cautela nos próximos meses",
    "Nova versão de modelo de linguagem promete reduzir custo de uso para empresas",
    "Dólar fecha em queda após dados de inflação nos Estados Unidos",
    "Startups brasileiras de tecnologia captam rodada recorde no terceiro trimestre",
    "Governo anuncia programa de incentivo à produção de semicondutores",
    "Título curto",
    "Mercado de trabalho em tecnologia volta a crescer, segundo levantamento",
    "Empresas adotam agentes automatizados no atendimento ao cliente",
    "Reforma tributária: o que muda para o setor de serviços digitais",
]


def main(saida: Path, imagens: Path | None):
    if saida.exists():
        shutil.rmtree(saida)
    shutil.copytree(RAIZ / "docs", saida, ignore=shutil.ignore_patterns("index.html", "resumos"))
    itens = []
    for i, titulo in enumerate(TITULOS, 1):
        img = None
        if imagens and (imagens / f"t{i - 1}.jpg").exists():
            shutil.copy(imagens / f"t{i - 1}.jpg", saida / "img" / f"{i:02d}.jpg")
            img = f"{i:02d}.jpg"
        itens.append(Item(
            slug=f"{i:02d}-{site_builder.slugify(titulo)}", nicho=["inteligência artificial", "economia", "tecnologia"][i % 3],
            resumo=Resumo(
                titulo,
                "Primeiro parágrafo do resumo simulado, que une as informações das fontes de forma clara e objetiva.\n\n"
                "Segundo parágrafo com mais contexto sobre o assunto, apenas para testar a quebra de linhas e a leitura em telas pequenas.",
                [{"nome": "G1", "url": "https://g1.globo.com/exemplo-de-url-bem-longa/para-testar-a-quebra-de-linha/materia-123456.ghtml"},
                 {"nome": "Folha de S.Paulo", "url": "https://www1.folha.uol.com.br/exemplo"}]),
            imagem=img,
        ))
    site_builder.gerar_site(itens, ["inteligência artificial", "tecnologia", "economia"], saida,
                            datetime(2026, 11, 11, 7, 0, tzinfo=timezone.utc))
    site_builder.verificar_site(saida, saida)
    print("Site de demonstração em", saida)


if __name__ == "__main__":
    main(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else None)
