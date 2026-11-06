"""API FastAPI (D-06, D-10, D-11, D-12): o apartamento vem da sessão, a pendência dos eventos."""

import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.genai import errors, types
from pydantic import BaseModel

from aurora import agentes, dados, dominio, modelo

USER_ID = "aurora"
_PEDIDO = "adk_request_confirmation"


class NovaSessao(BaseModel):
    apartamento: str | int


class NovaMensagem(BaseModel):
    texto: str


class Confirmacao(BaseModel):
    id: str
    confirmado: bool


def _erro(status: int, mensagem: str) -> JSONResponse:
    return JSONResponse({"erro": mensagem}, status_code=status)


def _detalhes(acao: str, args: dict) -> dict:
    if acao == "reservar_area":
        area = args.get("area")
        data = args.get("data")
        return {
            "area": dominio.normalizar_area(area) or area,
            "data": dominio.normalizar_data(data) or data,
        }
    if acao == "autorizar_visitante":
        data = args.get("data")
        return {
            "nome": str(args.get("nome", "")).strip(),
            "data": dominio.normalizar_data(data) or data,
        }
    return dict(args)


def pendencias(eventos) -> list[dict]:
    """D-10: pedidos de confirmação menos respostas, só pelos eventos da sessão."""
    respondidos = {
        r.id
        for e in eventos
        for r in e.get_function_responses()
        if r.name == _PEDIDO
    }
    saida = []
    for e in eventos:
        for c in e.get_function_calls():
            if c.name != _PEDIDO or c.id in respondidos:
                continue
            original = (c.args or {}).get("originalFunctionCall") or {}
            acao = original.get("name", "")
            saida.append(
                {
                    "id": c.id,
                    "acao": acao,
                    "detalhes": _detalhes(acao, original.get("args") or {}),
                }
            )
    return saida


def _resposta_final(eventos) -> str:
    for e in reversed(eventos):
        if e.author == "user" or not e.content or not e.content.parts:
            continue
        texto = "".join(p.text for p in e.content.parts if p.text and not p.thought)
        if texto:
            return texto
    return ""


class _Servico:
    """Monta o Runner no lifespan; sem lifespan (ASGITransport), na primeira requisição."""

    def __init__(self, criar_app: Callable, modelo_factory: Callable):
        self._criar_app = criar_app
        self._modelo_factory = modelo_factory
        self._runner: Runner | None = None
        self._travas: dict[str, asyncio.Lock] = {}

    @property
    def runner(self) -> Runner:
        if self._runner is None:
            dados.pasta_var().mkdir(parents=True, exist_ok=True)
            self._runner = Runner(
                app=self._criar_app(self._modelo_factory),
                session_service=SqliteSessionService(str(dados.caminho_sessoes())),
            )
        return self._runner

    def trava(self, session_id: str) -> asyncio.Lock:
        return self._travas.setdefault(session_id, asyncio.Lock())

    async def criar_sessao(self, apartamento: str):
        return await self.runner.session_service.create_session(
            app_name=agentes.APP_NOME, user_id=USER_ID, state={"apartamento": apartamento}
        )

    async def sessao(self, session_id: str):
        return await self.runner.session_service.get_session(
            app_name=agentes.APP_NOME, user_id=USER_ID, session_id=session_id
        )

    async def executar(self, session_id: str, conteudo: types.Content) -> list:
        return [
            e
            async for e in self.runner.run_async(
                user_id=USER_ID, session_id=session_id, new_message=conteudo
            )
        ]


def criar_api(
    criar_app: Callable = agentes.criar_app,
    modelo_factory: Callable = modelo.criar_modelo,
) -> FastAPI:
    """`criar_app` e `modelo_factory` entram por dependência (D-14)."""
    servico = _Servico(criar_app, modelo_factory)

    @asynccontextmanager
    async def ciclo_de_vida(_api: FastAPI):
        # D-15: sem GOOGLE_API_KEY a fábrica falha aqui e a API sai no boot
        servico.runner
        yield

    api = FastAPI(title="Residencial Aurora", lifespan=ciclo_de_vida)

    async def _responder(session_id: str, conteudo: types.Content, pedido_id=None):
        async with servico.trava(session_id):
            sessao = await servico.sessao(session_id)
            if sessao is None:
                return _erro(404, "sessao nao encontrada")
            if pedido_id is not None and pedido_id not in {
                p["id"] for p in pendencias(sessao.events)
            }:
                return _erro(409, "confirmacao inexistente ou ja respondida")
            try:
                eventos = await servico.executar(session_id, conteudo)
            except errors.APIError:
                return _erro(503, "modelo indisponivel, tente de novo")
            atual = await servico.sessao(session_id)
            return {
                "resposta": _resposta_final(eventos),
                "confirmacoes_pendentes": pendencias(atual.events),
            }

    @api.post("/sessoes", status_code=201)
    async def criar_sessao(corpo: NovaSessao):
        apartamento = str(corpo.apartamento).strip()
        if apartamento not in {a["numero"] for a in dados.carregar_apartamentos()}:
            return _erro(422, "apartamento inexistente")
        sessao = await servico.criar_sessao(apartamento)
        return {"session_id": sessao.id}

    @api.post("/sessoes/{session_id}/mensagens")
    async def enviar_mensagem(session_id: str, corpo: NovaMensagem):
        conteudo = types.Content(role="user", parts=[types.Part(text=corpo.texto)])
        return await _responder(session_id, conteudo)

    @api.post("/sessoes/{session_id}/confirmacoes")
    async def confirmar(session_id: str, corpo: Confirmacao):
        resposta = types.Part(
            function_response=types.FunctionResponse(
                id=corpo.id, name=_PEDIDO, response={"confirmed": corpo.confirmado}
            )
        )
        conteudo = types.Content(role="user", parts=[resposta])
        return await _responder(session_id, conteudo, pedido_id=corpo.id)

    @api.get("/sessoes/{session_id}/eventos")
    async def eventos(session_id: str):
        sessao = await servico.sessao(session_id)
        if sessao is None:
            return _erro(404, "sessao nao encontrada")
        return [e.model_dump(mode="json", exclude_none=True) for e in sessao.events]

    @api.get("/apartamentos/{numero}/reservas")
    async def reservas(numero: str):
        if not _existe(numero):
            return _erro(404, "apartamento inexistente")
        return dominio.reservas_do(numero)

    @api.get("/apartamentos/{numero}/visitantes")
    async def visitantes(numero: str):
        if not _existe(numero):
            return _erro(404, "apartamento inexistente")
        return dominio.visitantes_do(numero)

    return api


def _existe(numero: str) -> bool:
    return numero in {a["numero"] for a in dados.carregar_apartamentos()}


app = criar_api()
