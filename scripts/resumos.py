"""Estruturas e regras dos grupos de notícias.

Nada aqui depende de um provedor de texto específico: o pipeline conversa com qualquer
objeto que cumpra o protocolo `Resumidor` (hoje, `gemini.GeminiResumidor`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from coleta import Artigo

MAX_ARTIGOS_NO_PROMPT = 150
MAX_FONTES_POR_GRUPO = 3
MIN_FONTES_POR_GRUPO = 2
TOTAL_GRUPOS = 9


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
