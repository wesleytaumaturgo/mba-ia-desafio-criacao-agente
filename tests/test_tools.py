import json
import sqlite3

import pytest

from aurora import dados, dominio, restaurar, tools
from tests.roteiro import chamada, criar_runner, criar_sessao, rodar, texto


class Estado:
    """Contexto mínimo: as tools só leem `state`."""

    def __init__(self, apartamento):
        self.state = {"apartamento": apartamento}


@pytest.fixture(autouse=True)
def banco(tmp_path, monkeypatch):
    monkeypatch.setenv("AURORA_VAR_DIR", str(tmp_path))
    restaurar.restaurar()


def _contar(where="1=1"):
    with dados.conectar() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM reservas WHERE {where}").fetchone()[0]


def _pedidos_de_confirmacao(eventos):
    return [
        c
        for e in eventos
        for c in e.get_function_calls()
        if c.name == "adk_request_confirmation"
    ]


def _respostas(eventos, nome):
    return [
        r.response
        for e in eventos
        for r in e.get_function_responses()
        if r.name == nome
    ]


def _propriedades(tool):
    declaracao = tool._get_declaration().model_dump(exclude_none=True)
    esquema = declaracao.get("parameters") or declaracao.get("parameters_json_schema") or {}
    return set(esquema.get("properties", {}))


# T1
def test_nenhuma_tool_tem_parametro_de_apartamento():
    assert len(tools.TOOLS) == 6
    for tool in tools.TOOLS:
        proibidos = {"apartamento", "apto"} & _propriedades(tool)
        assert not proibidos, tool.name


def test_normaliza_area_por_id_nome_sem_acento_e_caixa():
    assert dominio.normalizar_area("Salão de festas") == "salao-de-festas"
    assert dominio.normalizar_area("  SALAO DE FESTAS ") == "salao-de-festas"
    assert dominio.normalizar_area("quadra") == "quadra"
    assert dominio.normalizar_area("Quadra poliesportiva") == "quadra"
    assert dominio.normalizar_area("piscina") is None


def test_normaliza_data_rejeita_formato_invalido():
    assert dominio.normalizar_data("2030-04-01") == "2030-04-01"
    assert dominio.normalizar_data("2030-02-31") is None
    assert dominio.normalizar_data("01/04/2030") is None


def test_disponibilidade_ocupada_nao_vaza_codigo_nem_apartamento():
    r = tools.verificar_disponibilidade("salão de festas", "2030-03-16", Estado("101"))
    assert r == {"area": "salao-de-festas", "data": "2030-03-16", "livre": False}
    r = tools.verificar_disponibilidade("quadra", "2030-04-01", Estado("101"))
    assert r == {"area": "quadra", "data": "2030-04-01", "livre": True}


def test_listagens_so_do_apartamento_da_sessao():
    reservas = tools.listar_minhas_reservas(Estado("101"))
    assert json.dumps(reservas).count("RSV-") == 1
    assert "RSV-1377" in json.dumps(reservas)
    assert "RSV-4821" not in json.dumps(reservas)
    visitantes = json.dumps(tools.listar_meus_visitantes(Estado("302")))
    assert "Marina Duarte" in visitantes
    assert "Paulo Nogueira" not in json.dumps(tools.listar_meus_visitantes(Estado("101")))


def test_cancelar_de_outro_apartamento_nao_cancela_nem_vaza():
    r = tools.cancelar_reserva("salao-de-festas", "2030-03-16", Estado("101"))
    assert r == {
        "cancelado": False,
        "motivo": "nenhuma reserva sua nessa área e data",
    }
    assert "RSV" not in json.dumps(r)
    assert _contar("codigo = 'RSV-4821' AND ativa = 1") == 1


def test_cancelar_a_propria_reserva():
    r = tools.cancelar_reserva("quadra", "2030-03-09", Estado("101"))
    assert r["cancelado"] is True
    assert _contar("codigo = 'RSV-1377' AND ativa = 0") == 1


def test_integrity_error_vira_reservado_false(monkeypatch):
    # a verificação prévia diz "livre", mas o índice recusa na gravação (corrida)
    monkeypatch.setattr(dominio, "data_livre", lambda area, data: True)
    r = tools.reservar_area("salao-de-festas", "2030-03-16", Estado("101"))
    assert r == {"reservado": False, "motivo": "data ocupada"}
    assert _contar() == 3


def test_reservar_area_invalida_nao_grava():
    r = tools.reservar_area("piscina", "2030-04-01", Estado("101"))
    assert r["reservado"] is False
    r = tools.reservar_area("quadra", "amanhã", Estado("101"))
    assert r["reservado"] is False
    assert _contar() == 3


def test_precisa_confirmar_so_com_taxa_data_livre_e_argumentos_validos():
    e = Estado("101")
    assert tools.precisa_confirmar("salão de festas", "2030-04-01", e) is True
    assert tools.precisa_confirmar("quadra", "2030-04-01", e) is False
    assert tools.precisa_confirmar("salao-de-festas", "2030-03-16", e) is False
    assert tools.precisa_confirmar("piscina", "2030-04-01", e) is False
    assert tools.precisa_confirmar("salao-de-festas", "ontem", e) is False


async def test_runner_salao_livre_gera_pedido_de_confirmacao_e_nao_grava():
    runner = criar_runner(
        tools.TOOLS, [chamada("reservar_area", area="Salão de festas", data="2030-04-01")]
    )
    sessao = await criar_sessao(runner, "101")
    eventos = await rodar(runner, sessao, "reserve o salão")
    pedidos = _pedidos_de_confirmacao(eventos)
    assert len(pedidos) == 1
    original = pedidos[0].args["originalFunctionCall"]
    assert original["name"] == "reservar_area"
    assert _contar() == 3


async def test_runner_quadra_executa_sem_confirmacao():
    runner = criar_runner(
        tools.TOOLS,
        [chamada("reservar_area", area="quadra", data="2030-04-01"), texto("feito")],
    )
    sessao = await criar_sessao(runner, "101")
    eventos = await rodar(runner, sessao)
    assert _pedidos_de_confirmacao(eventos) == []
    resposta = _respostas(eventos, "reservar_area")[0]
    assert resposta["reservado"] is True
    assert resposta["codigo"].startswith("RSV-")
    assert _contar("apartamento = '101' AND area = 'quadra' AND data = '2030-04-01'") == 1


async def test_runner_salao_ocupado_executa_e_recusa_sem_confirmacao():
    runner = criar_runner(
        tools.TOOLS,
        [chamada("reservar_area", area="salao-de-festas", data="2030-03-16"), texto("ocupado")],
    )
    sessao = await criar_sessao(runner, "101")
    eventos = await rodar(runner, sessao)
    assert _pedidos_de_confirmacao(eventos) == []
    resposta = _respostas(eventos, "reservar_area")[0]
    assert resposta == {"reservado": False, "motivo": "data ocupada"}
    assert "RSV-4821" not in json.dumps(resposta)


async def test_runner_autorizar_visitante_pede_confirmacao_sempre():
    runner = criar_runner(
        tools.TOOLS, [chamada("autorizar_visitante", nome="Ana Lima", data="2030-04-01")]
    )
    sessao = await criar_sessao(runner, "101")
    eventos = await rodar(runner, sessao)
    assert len(_pedidos_de_confirmacao(eventos)) == 1
    with dados.conectar() as conn:
        assert conn.execute("SELECT COUNT(*) FROM visitantes").fetchone()[0] == 2


def test_autorizar_visitante_grava_para_o_apartamento_da_sessao():
    r = tools.autorizar_visitante("Ana Lima", "2030-04-01", Estado("101"))
    assert r == {"autorizado": True, "nome": "Ana Lima", "data": "2030-04-01"}
    assert "Ana Lima" in json.dumps(tools.listar_meus_visitantes(Estado("101")))
    assert "Ana Lima" not in json.dumps(tools.listar_meus_visitantes(Estado("102")))


def test_sqlite_integrity_error_de_outra_natureza_nao_e_engolido(monkeypatch):
    def quebra(*args, **kwargs):
        raise sqlite3.IntegrityError("NOT NULL constraint failed: reservas.area")

    monkeypatch.setattr(dados, "gravar_reserva", quebra)
    with pytest.raises(sqlite3.IntegrityError):
        tools.reservar_area("quadra", "2030-04-01", Estado("101"))
