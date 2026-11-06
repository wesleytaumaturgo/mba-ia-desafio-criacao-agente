import re

import pytest

from aurora import regulamento


def _titulos(texto: str) -> list[str]:
    return re.findall(r"^## Capítulo .*$", texto, flags=re.MULTILINE)


def test_piscina_no_domingo_devolve_so_o_capitulo_iv():
    resposta = regulamento.consultar_regulamento(
        "Até que horas a piscina funciona aos domingos?"
    )

    assert resposta["capitulo"] == "Capítulo IV: Piscina"
    assert "das 9h às 20h" in resposta["texto"]
    assert _titulos(resposta["texto"]) == ["## Capítulo IV: Piscina"]


@pytest.mark.parametrize(
    "pergunta, capitulo",
    [
        ("Posso fazer a mudança no sábado à tarde?", "Capítulo IX: Mudanças"),
        ("Posso ter animais no apartamento?", "Capítulo VIII: Animais de estimação"),
        ("Quantas vagas de garagem cada unidade tem?", "Capítulo XI: Garagem e veículos"),
    ],
)
def test_pergunta_devolve_o_capitulo_certo_e_so_ele(pergunta, capitulo):
    resposta = regulamento.consultar_regulamento(pergunta)

    assert resposta["capitulo"] == capitulo
    assert _titulos(resposta["texto"]) == [f"## {capitulo}"]
