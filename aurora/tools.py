"""Tools do agente (D-06, D-07, D-08): o apartamento vem só do estado da sessão."""

from google.adk.tools import FunctionTool
from google.adk.tools.tool_context import ToolContext

from aurora import dados, dominio

_AREA_OU_DATA_INVALIDA = {"reservado": False, "motivo": "área ou data inválida"}


def _apartamento(tool_context: ToolContext) -> str:
    return tool_context.state["apartamento"]


def listar_minhas_reservas(tool_context: ToolContext) -> dict:
    """Lista as reservas ativas do morador desta conversa."""
    return {"reservas": dominio.reservas_do(_apartamento(tool_context))}


def verificar_disponibilidade(area: str, data: str, tool_context: ToolContext) -> dict:
    """Diz se a área está livre na data (AAAA-MM-DD)."""
    area_id = dominio.normalizar_area(area)
    data_norm = dominio.normalizar_data(data)
    if area_id is None or data_norm is None:
        return {"erro": "área ou data inválida"}
    return {
        "area": area_id,
        "data": data_norm,
        "livre": dominio.data_livre(area_id, data_norm),
    }


def precisa_confirmar(area: str, data: str, tool_context: ToolContext) -> bool:
    """Confirmação só se a área tem taxa, a data está livre e os argumentos são válidos."""
    area_id = dominio.normalizar_area(area)
    data_norm = dominio.normalizar_data(data)
    if area_id is None or data_norm is None:
        return False
    return dominio.taxa(area_id) > 0 and dominio.data_livre(area_id, data_norm)


def reservar_area(area: str, data: str, tool_context: ToolContext) -> dict:
    """Reserva a área na data (AAAA-MM-DD) para o morador desta conversa."""
    area_id = dominio.normalizar_area(area)
    data_norm = dominio.normalizar_data(data)
    if area_id is None or data_norm is None:
        return dict(_AREA_OU_DATA_INVALIDA)
    # a exclusividade é do índice único; IntegrityError vira resposta normal
    return dados.gravar_reserva(_apartamento(tool_context), area_id, data_norm)


def cancelar_reserva(area: str, data: str, tool_context: ToolContext) -> dict:
    """Cancela a reserva ativa do morador desta conversa na área e data."""
    area_id = dominio.normalizar_area(area)
    data_norm = dominio.normalizar_data(data)
    if area_id and data_norm and dados.cancelar_reserva(
        _apartamento(tool_context), area_id, data_norm
    ):
        return {"cancelado": True, "area": area_id, "data": data_norm}
    return {"cancelado": False, "motivo": "nenhuma reserva sua nessa área e data"}


def listar_meus_visitantes(tool_context: ToolContext) -> dict:
    """Lista os visitantes autorizados pelo morador desta conversa."""
    return {"visitantes": dominio.visitantes_do(_apartamento(tool_context))}


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict:
    """Autoriza a entrada de um visitante na data (AAAA-MM-DD)."""
    data_norm = dominio.normalizar_data(data)
    nome = str(nome).strip()
    if not nome or data_norm is None:
        return {"autorizado": False, "motivo": "nome ou data inválido"}
    dominio.registrar_visitante(_apartamento(tool_context), nome, data_norm)
    return {"autorizado": True, "nome": nome, "data": data_norm}


TOOLS = [
    FunctionTool(listar_minhas_reservas),
    FunctionTool(verificar_disponibilidade),
    FunctionTool(reservar_area, require_confirmation=precisa_confirmar),
    FunctionTool(cancelar_reserva),
    FunctionTool(listar_meus_visitantes),
    FunctionTool(autorizar_visitante, require_confirmation=True),
]
