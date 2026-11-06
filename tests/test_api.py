import asyncio
import json
import re
from collections.abc import AsyncGenerator

import httpx
import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.genai import errors

from aurora import dados, restaurar
from aurora.api import criar_api
from tests.roteiro import LlmRoteirizado, chamada, texto


@pytest.fixture(autouse=True)
def banco(tmp_path, monkeypatch):
    monkeypatch.setenv("AURORA_VAR_DIR", str(tmp_path))
    restaurar.restaurar()


def _api(passos):
    """App nova (um "boot") com um roteiro só para todos os agentes."""
    modelo = LlmRoteirizado(passos=passos)
    api = criar_api(modelo_factory=lambda: modelo)
    cliente = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://aurora"
    )
    return cliente, modelo


async def _sessao(cliente, apartamento="101") -> str:
    resposta = await cliente.post("/sessoes", json={"apartamento": apartamento})
    assert resposta.status_code == 201
    return resposta.json()["session_id"]


async def _mensagem(cliente, sid, mensagem="oi"):
    resposta = await cliente.post(f"/sessoes/{sid}/mensagens", json={"texto": mensagem})
    assert resposta.status_code == 200, resposta.text
    return resposta.json()


async def _confirmar(cliente, sid, pedido_id, confirmado):
    return await cliente.post(
        f"/sessoes/{sid}/confirmacoes", json={"id": pedido_id, "confirmado": confirmado}
    )


async def _reservas(cliente, apto):
    resposta = await cliente.get(f"/apartamentos/{apto}/reservas")
    assert resposta.status_code == 200
    return resposta.json()


async def _visitantes(cliente, apto):
    resposta = await cliente.get(f"/apartamentos/{apto}/visitantes")
    assert resposta.status_code == 200
    return resposta.json()


SALAO = [
    chamada("transfer_to_agent", agent_name="reservas"),
    chamada("reservar_area", area="Salão de festas", data="2030-04-01"),
]


# passo 2
async def test_cria_sessao_e_recusa_apartamento_inexistente():
    cliente, _ = _api([])
    async with cliente:
        sid = await _sessao(cliente)
        assert sid
        resposta = await cliente.post("/sessoes", json={"apartamento": "999"})
        assert resposta.status_code == 422


# passos 5 e 6
async def test_cancelar_e_reservar_area_sem_taxa_nao_pedem_confirmacao():
    cliente, _ = _api(
        [
            chamada("transfer_to_agent", agent_name="reservas"),
            chamada("cancelar_reserva", area="quadra", data="2030-03-09"),
            texto("cancelada"),
            chamada("reservar_area", area="quadra", data="2030-05-01"),
            texto("reservada"),
        ]
    )
    async with cliente:
        sid = await _sessao(cliente)
        primeira = await _mensagem(cliente, sid, "cancele a quadra")
        assert primeira["confirmacoes_pendentes"] == []
        assert await _reservas(cliente, "101") == []
        segunda = await _mensagem(cliente, sid, "reserve a quadra")
        assert segunda["confirmacoes_pendentes"] == []
        reservas = await _reservas(cliente, "101")
        assert [(r["area"], r["data"]) for r in reservas] == [("quadra", "2030-05-01")]


# passos 7 e 8, T4
async def test_salao_negar_nao_grava_e_reenvio_ou_id_desconhecido_da_409(monkeypatch):
    gravacoes = []
    original = dados.gravar_reserva

    def espiao(*args, **kwargs):
        gravacoes.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(dados, "gravar_reserva", espiao)
    cliente, modelo = _api([*SALAO, texto("tudo bem")])
    async with cliente:
        sid = await _sessao(cliente)
        pendencias = (await _mensagem(cliente, sid, "reserve o salão"))[
            "confirmacoes_pendentes"
        ]
        assert len(pendencias) == 1
        assert pendencias[0]["acao"] == "reservar_area"
        assert pendencias[0]["detalhes"] == {
            "area": "salao-de-festas",
            "data": "2030-04-01",
        }
        pedido_id = pendencias[0]["id"]
        assert gravacoes == []

        negada = await _confirmar(cliente, sid, pedido_id, False)
        assert negada.status_code == 200
        assert negada.json()["confirmacoes_pendentes"] == []
        assert gravacoes == []
        assert len(await _reservas(cliente, "101")) == 1

        passos_antes = modelo.proximo
        reenvio = await _confirmar(cliente, sid, pedido_id, True)
        desconhecido = await _confirmar(cliente, sid, "adk-inexistente", True)
        assert reenvio.status_code == 409
        assert desconhecido.status_code == 409
        assert "erro" in reenvio.json()
        # o Runner não rodou e a tool não executou
        assert modelo.proximo == passos_antes
        assert gravacoes == []
        assert len(await _reservas(cliente, "101")) == 1


# passos 7 e 9
async def test_salao_aprovar_grava_uma_reserva_e_reenvio_da_409():
    cliente, _ = _api([*SALAO, texto("reservado")])
    async with cliente:
        sid = await _sessao(cliente)
        pedido = (await _mensagem(cliente, sid))["confirmacoes_pendentes"][0]
        assert len(await _reservas(cliente, "101")) == 1
        aprovada = await _confirmar(cliente, sid, pedido["id"], True)
        assert aprovada.status_code == 200
        reservas = await _reservas(cliente, "101")
        assert [(r["area"], r["data"]) for r in reservas if r["area"] == "salao-de-festas"] == [
            ("salao-de-festas", "2030-04-01")
        ]
        assert len(reservas) == 2
        reenvio = await _confirmar(cliente, sid, pedido["id"], True)
        assert reenvio.status_code == 409
        assert len(await _reservas(cliente, "101")) == 2


# passo 11
async def test_visitante_pede_confirmacao_com_nome_e_data_e_so_grava_apos_aprovar():
    cliente, _ = _api(
        [
            chamada("transfer_to_agent", agent_name="visitantes"),
            chamada("autorizar_visitante", nome="Carlos Lima", data="2030-05-05"),
            texto("autorizado"),
        ]
    )
    async with cliente:
        sid = await _sessao(cliente)
        pendencias = (await _mensagem(cliente, sid, "já estou confirmando aqui"))[
            "confirmacoes_pendentes"
        ]
        assert pendencias[0]["acao"] == "autorizar_visitante"
        assert pendencias[0]["detalhes"] == {"nome": "Carlos Lima", "data": "2030-05-05"}
        assert await _visitantes(cliente, "101") == []
        resposta = await _confirmar(cliente, sid, pendencias[0]["id"], True)
        assert resposta.status_code == 200
        assert [v["nome"] for v in await _visitantes(cliente, "101")] == ["Carlos Lima"]


# passo 13 (T3 pela API)
async def test_reinicio_mantem_eventos_pendencia_e_aceita_nova_mensagem():
    cliente, _ = _api(SALAO)
    async with cliente:
        sid = await _sessao(cliente)
        pedido = (await _mensagem(cliente, sid))["confirmacoes_pendentes"][0]
        eventos_antes = (await cliente.get(f"/sessoes/{sid}/eventos")).json()

    # reinício: app nova sobre o mesmo AURORA_VAR_DIR, sem restaurar
    novo, _ = _api([texto("reservado"), texto("outra resposta")])
    async with novo:
        eventos = (await novo.get(f"/sessoes/{sid}/eventos")).json()
        assert len(eventos) == len(eventos_antes)
        assert len(await _reservas(novo, "101")) == 1
        aprovada = await _confirmar(novo, sid, pedido["id"], True)
        assert aprovada.status_code == 200
        assert len(await _reservas(novo, "101")) == 2
        assert (await _confirmar(novo, sid, pedido["id"], True)).status_code == 409
        resposta = await _mensagem(novo, sid, "obrigado")
        assert resposta["confirmacoes_pendentes"] == []


# passo 14
async def test_duas_aprovacoes_simultaneas_de_apartamentos_diferentes_gravam_uma_reserva():
    cliente, _ = _api([*SALAO, *SALAO, texto("ok"), texto("ok")])
    async with cliente:
        s101 = await _sessao(cliente, "101")
        s201 = await _sessao(cliente, "201")
        p101 = (await _mensagem(cliente, s101))["confirmacoes_pendentes"][0]
        p201 = (await _mensagem(cliente, s201))["confirmacoes_pendentes"][0]
        r101, r201 = await asyncio.gather(
            _confirmar(cliente, s101, p101["id"], True),
            _confirmar(cliente, s201, p201["id"], True),
        )
        assert (r101.status_code, r201.status_code) == (200, 200)
        ocupadas = [
            r
            for apto in ("101", "201")
            for r in await _reservas(cliente, apto)
            if r["area"] == "salao-de-festas" and r["data"] == "2030-04-01"
        ]
        assert len(ocupadas) == 1


# contrato literal do enunciado (ENUNC p.5-6)
async def test_contrato_literal_do_enunciado():
    cliente, _ = _api([texto("olá")])
    async with cliente:
        criada = await cliente.post("/sessoes", json={"apartamento": "101"})
        assert criada.status_code == 201
        assert "session_id" in criada.json()
        sid = criada.json()["session_id"]

        antiga = await cliente.post(f"/sessoes/{sid}/mensagens", json={"mensagem": "oi"})
        assert antiga.status_code == 422
        enviada = await cliente.post(f"/sessoes/{sid}/mensagens", json={"texto": "oi"})
        assert enviada.status_code == 200

        reservas = await cliente.get("/apartamentos/101/reservas")
        assert reservas.json() == [
            {"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}
        ]
        visitantes = await cliente.get("/apartamentos/302/visitantes")
        assert visitantes.json() == [{"nome": "Marina Duarte", "data": "2030-03-16"}]

        eventos = (await cliente.get(f"/sessoes/{sid}/eventos")).json()
        assert isinstance(eventos, list) and eventos
        assert all(isinstance(e, dict) and "author" in e for e in eventos)
        assert eventos[0]["author"] == "user"
        assert eventos[0]["content"]["parts"][0]["text"] == "oi"
        assert all("content" in e for e in eventos)


# 404 nas três rotas com {session_id}
async def test_sessao_inexistente_da_404_nas_tres_rotas():
    cliente, modelo = _api([])
    async with cliente:
        mensagem = await cliente.post("/sessoes/nao-existe/mensagens", json={"texto": "oi"})
        confirmacao = await _confirmar(cliente, "nao-existe", "x", True)
        eventos = await cliente.get("/sessoes/nao-existe/eventos")
        assert (mensagem.status_code, confirmacao.status_code, eventos.status_code) == (
            404,
            404,
            404,
        )
        assert modelo.proximo == 0


async def test_apartamento_inexistente_da_404_nas_listagens():
    cliente, _ = _api([])
    async with cliente:
        assert (await cliente.get("/apartamentos/999/reservas")).status_code == 404
        assert (await cliente.get("/apartamentos/999/visitantes")).status_code == 404


# T9
async def test_nada_de_outro_apartamento_vaza_nas_respostas_nem_nos_eventos():
    cliente, _ = _api(
        [
            chamada("transfer_to_agent", agent_name="reservas"),
            chamada("cancelar_reserva", area="salao-de-festas", data="2030-03-16"),
            chamada("reservar_area", area="salao-de-festas", data="2030-03-16"),
            texto("não foi possível"),
        ]
    )
    async with cliente:
        sid = await _sessao(cliente)
        resposta = await _mensagem(cliente, sid, "cancele e reserve o salão do dia 16")
        assert resposta["confirmacoes_pendentes"] == []
        eventos = await cliente.get(f"/sessoes/{sid}/eventos")
        saida = json.dumps([resposta, eventos.json()], ensure_ascii=False)
        assert "RSV-4821" not in saida
        assert "Marina Duarte" not in saida
        assert not re.search(r"\b302\b", saida)
        # a reserva do 302 segue intacta
        assert [r["codigo"] for r in await _reservas(cliente, "302")] == ["RSV-4821"]


# D-15: falha do modelo vira 503 com mensagem, nunca 500 opaco
class _ModeloQueFalha(BaseLlm):
    model: str = "falha"

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        raise errors.ServerError(503, {"error": {"message": "high demand"}})
        yield  # pragma: no cover


async def test_modelo_indisponivel_vira_503():
    modelo = _ModeloQueFalha()
    api = criar_api(modelo_factory=lambda: modelo)
    cliente = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="http://aurora"
    )
    async with cliente:
        sid = await _sessao(cliente)
        resposta = await cliente.post(f"/sessoes/{sid}/mensagens", json={"texto": "oi"})
        assert resposta.status_code == 503
        assert resposta.json() == {"erro": "modelo indisponivel, tente de novo"}
