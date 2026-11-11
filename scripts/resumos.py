"""Estruturas e regras dos grupos de notícias.

Nada aqui depende de um provedor de texto específico: o pipeline conversa com qualquer
objeto que cumpra o protocolo `Resumidor` (hoje, `gemini.GeminiResumidor`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from coleta import Artigo, normalizar_titulo

MAX_ARTIGOS_NO_PROMPT = 150
MAX_FONTES_POR_GRUPO = 3
MIN_FONTES_POR_GRUPO = 2
TOTAL_GRUPOS = 9
TAMANHO_POOL = 36  # máximo de matérias cujo texto é lido antes da chamada
LIMIAR_SIMILARIDADE = 0.3
MAX_POR_ASSUNTO_NO_POOL = 3


@dataclass
class Grupo:
    nicho: str
    relevancia: float
    artigos: list[Artigo] = field(default_factory=list)
    titulo: str = ""
    resumo: str = ""


@dataclass
class Resumo:
    titulo: str
    resumo: str
    fontes: list[dict]  # [{"nome": ..., "url": ...}]


class Resumidor(Protocol):
    """Contrato entre o pipeline e o provedor de texto.

    Recebe as notícias coletadas e devolve, em UMA chamada, até `total` grupos já resumidos
    (com `titulo` e `resumo` preenchidos), seguindo as regras de `montar_grupos`.
    Para trocar de provedor basta implementar este método.
    """

    def resumir_grupos(self, artigos: list[Artigo], nichos: list[str], total: int) -> list[Grupo]: ...


def limitar_artigos(artigos: list[Artigo], limite: int = MAX_ARTIGOS_NO_PROMPT) -> list[Artigo]:
    """Limita o prompt alternando entre os nichos (mais recentes de cada um primeiro)."""
    por_nicho: dict[str, list[Artigo]] = {}
    for a in sorted(artigos, key=lambda x: x.publicado, reverse=True):
        por_nicho.setdefault(a.nicho, []).append(a)
    escolhidos: list[Artigo] = []
    filas = list(por_nicho.values())
    while filas and len(escolhidos) < limite:
        for fila in list(filas):
            escolhidos.append(fila.pop(0))
            if not fila:
                filas.remove(fila)
            if len(escolhidos) >= limite:
                break
    return escolhidos


def _palavras(titulo: str) -> set[str]:
    return {p for p in normalizar_titulo(titulo).split() if len(p) >= 4}


def _parecidos(a: set[str], b: set[str]) -> bool:
    return bool(a | b) and len(a & b) / len(a | b) >= LIMIAR_SIMILARIDADE


def selecionar_pool(artigos: list[Artigo], tamanho: int = TAMANHO_POOL) -> list[Artigo]:
    """Escolhe quais matérias terão o texto lido e enviado ao Gemini.

    Ler o texto de todas seria lento, então priorizamos as que têm títulos parecidos com os de
    outros veículos (provável mesmo fato, ou seja, bons grupos), alternando entre os nichos e
    limitando a poucas matérias por assunto. Não decide nada sobre os grupos: o Gemini continua
    sendo quem agrupa.
    """
    palavras = [_palavras(a.titulo) for a in artigos]
    pontos = [
        sum(1 for j, b in enumerate(artigos)
            if j != i and b.fonte != a.fonte and _parecidos(palavras[i], palavras[j]))
        for i, a in enumerate(artigos)
    ]
    por_nicho: dict[str, list[int]] = {}
    for i in sorted(range(len(artigos)), key=lambda i: artigos[i].publicado, reverse=True):
        por_nicho.setdefault(artigos[i].nicho, []).append(i)
    filas = [sorted(f, key=lambda i: -pontos[i]) for f in por_nicho.values()]  # estável: recentes primeiro

    escolhidos: list[int] = []
    while filas and len(escolhidos) < tamanho:
        for fila in list(filas):
            while fila:
                i = fila.pop(0)
                repetidas = sum(1 for k in escolhidos if _parecidos(palavras[i], palavras[k]))
                if repetidas < MAX_POR_ASSUNTO_NO_POOL:
                    escolhidos.append(i)
                    break
            if not fila:
                filas.remove(fila)
            if len(escolhidos) >= tamanho:
                break
    return [artigos[i] for i in escolhidos]


def montar_grupos(brutos: list[dict], candidatos: list[Artigo], total: int = TOTAL_GRUPOS) -> list[Grupo]:
    """Aplica as regras sobre os grupos propostos pelo provedor e devolve até `total`.

    Cada item de `brutos`: {"ids": [...], "nicho", "relevancia", "titulo", "resumo"}.
    - cada notícia entra em um só grupo; veículos repetidos no mesmo grupo são ignorados;
    - grupos de 2 a 3 fontes vêm primeiro, por relevância;
    - se faltarem, completa com notícias avulsas (grupo de uma fonte só, a que o provedor
      devolveu com um único id), de veículos diferentes entre si. Nada é inventado: tudo
      vem de ids que existem em `candidatos`.
    """
    por_relevancia = sorted(brutos, key=lambda g: -g["relevancia"])
    usados: set[int] = set()
    grupos: list[Grupo] = []

    def novo(g: dict, indices: list[int]) -> Grupo:
        usados.update(indices)
        artigos = [candidatos[i] for i in indices]
        return Grupo(g["nicho"] or artigos[0].nicho, g["relevancia"], artigos, g["titulo"], g["resumo"])

    for g in por_relevancia:  # 1) grupos com 2 a 3 fontes
        if len(g["ids"]) < MIN_FONTES_POR_GRUPO:
            continue
        indices: list[int] = []
        veiculos: set[str] = set()
        for i in g["ids"]:
            fonte = candidatos[i].fonte.lower()
            if i in usados or i in indices or fonte in veiculos:
                continue
            indices.append(i)
            veiculos.add(fonte)
            if len(indices) == MAX_FONTES_POR_GRUPO:
                break
        if len(indices) >= MIN_FONTES_POR_GRUPO:  # se sobrou 1, o texto não vale mais: descarta
            grupos.append(novo(g, indices))

    veiculos_avulsos: set[str] = set()
    for g in por_relevancia:  # 2) avulsas, só para completar
        if len(grupos) >= total:
            break
        if len(g["ids"]) != 1:
            continue
        i = g["ids"][0]
        fonte = candidatos[i].fonte.lower()
        if i in usados or fonte in veiculos_avulsos:
            continue
        veiculos_avulsos.add(fonte)
        grupos.append(novo(g, [i]))

    return grupos[:total]
