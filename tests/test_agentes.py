import importlib
import importlib.util
import json
from pathlib import Path

import pytest
from google.adk.runners import Runner
from google.adk.sessions.in_memory_session_service import InMemorySessionService
from google.adk.sessions.sqlite_session_service import SqliteSessionService

from aurora import dados, modelo, restaurar
from aurora.agentes import criar_app
from tests.roteiro import (
    LlmRoteirizado,
    chamada,
    criar_sessao,
    responder_confirmacao,
    rodar,
    texto,
)

RAIZ = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def banco(tmp_path, monkeypatch):
    monkeypatch.setenv("AURORA_VAR_DIR", str(tmp_path))
    restaurar.restaurar()


def _app(passos):
    modelo_unico = LlmRoteirizado(passos=passos)  # um roteiro para todos os agentes
    return criar_app(lambda: modelo_unico)


def _runner(app, servico=None):
    return Runner(app=app, session_service=servico or InMemorySessionService())


def _contar(where="1=1"):
    with dados.conectar() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM reservas WHERE {where}").fetchone()[0]


def _pedidos(eventos):
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


SALAO = [
    chamada("transfer_to_agent", agent_name="reservas"),
    chamada("reservar_area", area="Salão de festas", data="2030-04-01"),
]


# T2
def test_nenhuma_instrucao_contem_texto_do_regulamento():
    app = _app([])
    agentes = [app.root_agent, *app.root_agent.sub_agents]
    agentes += [
        t.agent for t in app.root_agent.tools if hasattr(t, "agent")
    ]
    assert {a.name for a in agentes} == {"principal", "reservas", "visitantes", "regulamento"}
    linhas = [
        l.strip()
        for l in (RAIZ / "dados" / "regulamento.md").read_text(encoding="utf-8").splitlines()
        if len(l.strip()) > 40
    ]
    for agente in agentes:
        for linha in linhas:
            assert linha not in agente.instruction, agente.name


def test_topologia_principal_sem_tools_de_dados_e_regulamento_so_via_agent_tool():
    app = _app([])
    principal = app.root_agent
    assert principal.name == "principal"
    assert [a.name for a in principal.sub_agents] == ["reservas", "visitantes"]
    assert [t.name for t in principal.tools] == ["regulamento"]
    reservas, visitantes = principal.sub_agents
    assert "reservar_area" in {t.name for t in reservas.tools}
    assert "autorizar_visitante" in {t.name for t in visitantes.tools}
    assert "consultar_regulamento" not in {t.name for t in reservas.tools + visitantes.tools}


# T7
def test_sem_disallow_transfer_no_pacote():
    for arquivo in (RAIZ / "aurora").rglob("*.py"):
        assert "disallow_transfer" not in arquivo.read_text(encoding="utf-8"), arquivo.name


# T8
def test_importar_sem_ambiente_nao_levanta(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("AURORA_MODELO", raising=False)
    nomes = ["aurora.modelo", "aurora.agentes"]
    if importlib.util.find_spec("aurora.api"):
        nomes.append("aurora.api")
    for nome in nomes:
        importlib.reload(importlib.import_module(nome))


# fábrica padrão (D-15, D-16)
@pytest.fixture
def sem_dotenv(monkeypatch):
    monkeypatch.setattr(modelo, "load_dotenv", lambda: None)


def test_modelo_usa_padrao_sem_variavel(monkeypatch, sem_dotenv):
    monkeypatch.setenv("GOOGLE_API_KEY", "chave-falsa-de-teste")
    monkeypatch.delenv("AURORA_MODELO", raising=False)
    m = modelo.criar_modelo()
    assert m.model == modelo.MODELO_PADRAO == "gemini-3.5-flash-lite"
    assert m.retry_options.attempts == 5
    assert m.retry_options.http_status_codes == [429, 500, 503]


def test_modelo_usa_valor_da_variavel_e_ignora_vazia(monkeypatch, sem_dotenv):
    monkeypatch.setenv("GOOGLE_API_KEY", "chave-falsa-de-teste")
    monkeypatch.setenv("AURORA_MODELO", "gemini-outro")
    assert modelo.criar_modelo().model == "gemini-outro"
    monkeypatch.setenv("AURORA_MODELO", "")
    assert modelo.criar_modelo().model == modelo.MODELO_PADRAO


def test_modelo_sai_com_mensagem_sem_chave(monkeypatch, sem_dotenv, capsys):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    with pytest.raises(SystemExit) as saida:
        modelo.criar_modelo()
    assert saida.value.code != 0
    assert "GOOGLE_API_KEY" in capsys.readouterr().err


def test_modelo_carrega_o_dotenv_antes_de_ler(monkeypatch):
    chamadas = []
    monkeypatch.setattr(modelo, "load_dotenv", lambda: chamadas.append(1))
    monkeypatch.setenv("GOOGLE_API_KEY", "chave-falsa-de-teste")
    modelo.criar_modelo()
    assert chamadas == [1]


# confirmação do salão (D-08)
async def test_salao_pede_confirmacao_e_nao_grava():
    runner = _runner(_app(SALAO))
    sessao = await criar_sessao(runner, "101")
    eventos = await rodar(runner, sessao, "reserve o salão")
    pedidos = _pedidos(eventos)
    assert len(pedidos) == 1
    assert pedidos[0].args["originalFunctionCall"]["name"] == "reservar_area"
    assert _contar() == 3


async def test_negar_nao_grava():
    runner = _runner(_app([*SALAO, texto("tudo bem")]))
    sessao = await criar_sessao(runner, "101")
    pedido = _pedidos(await rodar(runner, sessao))[0]
    await responder_confirmacao(runner, sessao, pedido.id, False)
    assert _contar() == 3


async def test_aprovar_grava_uma_reserva():
    runner = _runner(_app([*SALAO, texto("reservado")]))
    sessao = await criar_sessao(runner, "101")
    pedido = _pedidos(await rodar(runner, sessao))[0]
    eventos = await responder_confirmacao(runner, sessao, pedido.id, True)
    assert _contar() == 4
    assert _contar("apartamento = '101' AND area = 'salao-de-festas' AND data = '2030-04-01'") == 1
    assert _respostas(eventos, "reservar_area")[0]["reservado"] is True


async def test_data_ocupada_depois_do_pedido_e_negar_nao_grava_nem_vaza():
    runner = _runner(_app([*SALAO, texto("ocupado")]))
    sessao = await criar_sessao(runner, "101")
    pedido = _pedidos(await rodar(runner, sessao))[0]
    # outro apartamento ocupa a data entre o pedido e a resposta
    assert dados.gravar_reserva("201", "salao-de-festas", "2030-04-01")["reservado"] is True
    antes = _contar()
    eventos = await responder_confirmacao(runner, sessao, pedido.id, False)
    assert _contar() == antes
    retorno = json.dumps(_respostas(eventos, "reservar_area"))
    assert "RSV-" not in retorno
    assert "201" not in retorno
    assert _contar("apartamento = '101' AND data = '2030-04-01'") == 0


# T3
async def test_retomada_depois_de_reinicio_grava(tmp_path):
    arquivo = str(tmp_path / "sessoes-teste.db")
    runner = _runner(_app(SALAO), SqliteSessionService(arquivo))
    sessao = await criar_sessao(runner, "101")
    pedido = _pedidos(await rodar(runner, sessao))[0]
    assert _contar() == 3

    # "reinício": app, serviço e Runner novos sobre o mesmo arquivo
    novo = _runner(_app([texto("reservado")]), SqliteSessionService(arquivo))
    await responder_confirmacao(novo, sessao, pedido.id, True)
    assert _contar() == 4
    assert _contar("apartamento = '101' AND area = 'salao-de-festas'") == 1


# D-09 (v1-R7)
async def test_segunda_mensagem_chega_ao_especialista_e_ele_transfere_ao_principal():
    runner = _runner(
        _app(
            [
                chamada("transfer_to_agent", agent_name="reservas"),
                texto("posso ajudar com reservas"),
                chamada("transfer_to_agent", agent_name="principal"),
                texto("o principal responde"),
            ]
        )
    )
    sessao = await criar_sessao(runner, "101")
    primeira = await rodar(runner, sessao, "quero reservar")
    assert primeira[-1].author == "reservas"

    segunda = await rodar(runner, sessao, "e o regulamento da piscina?")
    transferencias = [
        (e.author, c.args["agent_name"])
        for e in segunda
        for c in e.get_function_calls()
        if c.name == "transfer_to_agent"
    ]
    assert transferencias == [("reservas", "principal")]
    assert segunda[-1].author == "principal"
