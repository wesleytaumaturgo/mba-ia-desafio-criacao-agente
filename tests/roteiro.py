"""LLM roteirizado e Runner de teste (D-14): nenhuma chamada de rede."""

from collections.abc import AsyncGenerator

from google.adk.agents import LlmAgent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_request import LlmRequest
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import InMemoryRunner
from google.genai import types

APP = "aurora-teste"


def chamada(tool: str, /, **args) -> LlmResponse:
    """Passo do roteiro: o modelo pede a tool `tool`."""
    parte = types.Part(function_call=types.FunctionCall(name=tool, args=args))
    return LlmResponse(content=types.Content(role="model", parts=[parte]))


def texto(conteudo: str) -> LlmResponse:
    """Passo do roteiro: o modelo responde em texto."""
    return LlmResponse(
        content=types.Content(role="model", parts=[types.Part(text=conteudo)])
    )


class LlmRoteirizado(BaseLlm):
    """Devolve os passos em ordem; esgotado o roteiro, responde "fim"."""

    model: str = "roteirizado"
    passos: list[LlmResponse]
    proximo: int = 0

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        if self.proximo < len(self.passos):
            passo = self.passos[self.proximo]
            self.proximo += 1
        else:
            passo = texto("fim")
        yield passo


def criar_runner(tools: list, passos: list[LlmResponse]) -> InMemoryRunner:
    agente = LlmAgent(
        name="aurora_teste",
        model=LlmRoteirizado(passos=passos),
        instruction="Atenda o morador.",
        tools=tools,
    )
    return InMemoryRunner(agent=agente, app_name=APP)


async def criar_sessao(runner: InMemoryRunner, apartamento: str):
    return await runner.session_service.create_session(
        app_name=runner.app_name, user_id="aurora", state={"apartamento": apartamento}
    )


async def rodar(runner: InMemoryRunner, sessao, mensagem: str = "oi") -> list:
    """Executa uma mensagem do usuário e devolve todos os eventos."""
    nova = types.Content(role="user", parts=[types.Part(text=mensagem)])
    return await _executar(runner, sessao, nova)


async def responder_confirmacao(
    runner: InMemoryRunner, sessao, pedido_id: str, confirmado: bool
) -> list:
    """Responde um `adk_request_confirmation` pelo id, como o cliente da API."""
    resposta = types.Part(
        function_response=types.FunctionResponse(
            id=pedido_id,
            name="adk_request_confirmation",
            response={"confirmed": confirmado},
        )
    )
    return await _executar(runner, sessao, types.Content(role="user", parts=[resposta]))


async def _executar(runner, sessao, conteudo: types.Content) -> list:
    return [
        evento
        async for evento in runner.run_async(
            user_id="aurora", session_id=sessao.id, new_message=conteudo
        )
    ]
