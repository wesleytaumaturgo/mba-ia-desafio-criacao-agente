"""Regulamento por capítulo (D-13, G4): o código escolhe o capítulo, não o modelo."""

import math
import re
import unicodedata
from functools import lru_cache

from aurora import dados

_TITULO = re.compile(r"^## (Capítulo .+)$", re.MULTILINE)
_PALAVRA = re.compile(r"[a-z0-9]+")
_TAMANHO_RAIZ = 5
_PESO_TITULO = 10


def _sem_acento(texto: str) -> str:
    decomposto = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in decomposto if not unicodedata.combining(c))


def _raizes(texto: str) -> list[str]:
    """Palavras de 4+ letras reduzidas ao prefixo (mudança ~ mudanças)."""
    return [p[:_TAMANHO_RAIZ] for p in _PALAVRA.findall(_sem_acento(texto)) if len(p) >= 4]


@lru_cache(maxsize=1)
def capitulos() -> tuple[dict, ...]:
    """Corta o regulamento por `## Capítulo`; cada item tem título, texto e raízes."""
    texto = dados.carregar_regulamento()
    marcas = list(_TITULO.finditer(texto))
    fim = [m.start() for m in marcas[1:]] + [len(texto)]
    itens = []
    for marca, limite in zip(marcas, fim):
        titulo = marca.group(1)
        corpo = texto[marca.start():limite].strip()
        itens.append(
            {
                "titulo": titulo,
                "texto": corpo,
                "raizes_titulo": _raizes(titulo),
                "raizes_corpo": _raizes(corpo),
            }
        )
    return tuple(itens)


def _escolher(pergunta: str) -> dict:
    todos = capitulos()
    melhor, melhor_nota = todos[0], (-1.0, -1.0)
    for cap in todos:
        nota = nota_titulo = 0.0
        for raiz in set(_raizes(pergunta)):
            presentes = sum(1 for c in todos if raiz in c["raizes_corpo"])
            if not presentes:
                continue
            peso = math.log(1 + len(todos) / presentes)
            no_titulo = cap["raizes_titulo"].count(raiz)
            no_corpo = cap["raizes_corpo"].count(raiz)
            nota_titulo += peso * no_titulo
            nota += peso * (_PESO_TITULO * min(no_titulo, 1) + math.log1p(no_corpo))
        # empate: o de título mais aderente
        if (nota, nota_titulo) > melhor_nota:
            melhor, melhor_nota = cap, (nota, nota_titulo)
    return melhor


def consultar_regulamento(pergunta: str) -> dict:
    """Devolve um único capítulo do regulamento para a pergunta."""
    cap = _escolher(pergunta)
    return {"capitulo": cap["titulo"], "texto": cap["texto"]}
