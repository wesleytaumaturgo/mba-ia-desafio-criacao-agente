"""Topologia dos agentes (D-09, D-14): o modelo conversa, o código decide (tools)."""

from collections.abc import Callable

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.tools import FunctionTool
from google.adk.tools.agent_tool import AgentTool

from aurora import regulamento, tools

APP_NOME = "aurora"

_VOLTAR = (
    "Se o assunto não for seu (regulamento, outra especialidade), transfira a "
    "conversa para o agente principal e não responda você mesmo."
)


def _tools(*nomes: str) -> list:
    return [t for t in tools.TOOLS if t.name in nomes]


def criar_app(modelo_factory: Callable[[], object]) -> App:
    """Monta o app; `modelo_factory()` devolve o modelo de cada agente."""
    reg = LlmAgent(
        name="regulamento",
        description="Responde dúvidas sobre o regulamento do condomínio.",
        model=modelo_factory(),
        instruction=(
            "Você responde dúvidas sobre o regulamento do Residencial Aurora. "
            "Chame consultar_regulamento com a pergunta e responda em português "
            "apenas com base no capítulo devolvido, citando o artigo."
        ),
        tools=[FunctionTool(regulamento.consultar_regulamento)],
    )
    reservas = LlmAgent(
        name="reservas",
        description="Reservas das áreas comuns: disponibilidade, reservar, cancelar, listar.",
        model=modelo_factory(),
        instruction=(
            "Você cuida das reservas das áreas comuns do morador desta conversa. "
            "Use as tools para consultar disponibilidade, reservar, cancelar e "
            "listar. Nunca peça nem aceite o número do apartamento: ele vem da "
            "sessão. Responda em português, sem inventar dados. " + _VOLTAR
        ),
        tools=_tools(
            "listar_minhas_reservas",
            "verificar_disponibilidade",
            "reservar_area",
            "cancelar_reserva",
        ),
    )
    visitantes = LlmAgent(
        name="visitantes",
        description="Autorização e consulta de visitantes do morador.",
        model=modelo_factory(),
        instruction=(
            "Você cuida dos visitantes do morador desta conversa. Use as tools "
            "para listar e autorizar visitantes. Nunca peça nem aceite o número "
            "do apartamento: ele vem da sessão. Responda em português. " + _VOLTAR
        ),
        tools=_tools("listar_meus_visitantes", "autorizar_visitante"),
    )
    principal = LlmAgent(
        name="principal",
        description="Atendimento do Residencial Aurora.",
        model=modelo_factory(),
        instruction=(
            "Você é o assistente do Residencial Aurora e atende o morador em "
            "português. Reservas de áreas comuns: transfira para o agente "
            "reservas. Visitantes: transfira para o agente visitantes. Dúvidas "
            "sobre regras do condomínio: use a ferramenta regulamento e repasse "
            "a resposta. Não invente informações."
        ),
        sub_agents=[reservas, visitantes],
        tools=[AgentTool(agent=reg)],
    )
    return App(name=APP_NOME, root_agent=principal)
