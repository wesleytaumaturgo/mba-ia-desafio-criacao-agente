"""Regras do condomínio no código: normalização de área e consultas (D-07, D-12)."""

import unicodedata
from datetime import date

from aurora import dados


def _simplificar(texto: str) -> str:
    """Minúsculas, sem acento e com espaços/hífens unificados."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return "-".join(sem_acento.lower().replace("-", " ").split())


def normalizar_area(texto: str) -> str | None:
    """Devolve o id da área a partir do id ou do nome; None se não existe."""
    chave = _simplificar(str(texto))
    for area in dados.carregar_areas():
        if chave in (_simplificar(area["id"]), _simplificar(area["nome"])):
            return area["id"]
    return None


def normalizar_data(texto: str) -> str | None:
    """Aceita só AAAA-MM-DD de calendário válido."""
    texto = str(texto).strip()
    if len(texto) != 10:
        return None
    try:
        return date.fromisoformat(texto).isoformat()
    except ValueError:
        return None


def taxa(area_id: str) -> float:
    for area in dados.carregar_areas():
        if area["id"] == area_id:
            return float(area["taxa"])
    return 0.0


def data_livre(area_id: str, data: str) -> bool:
    with dados.conectar() as conn:
        linha = conn.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND ativa = 1",
            (area_id, data),
        ).fetchone()
    return linha is None


def reservas_do(apartamento: str) -> list[dict]:
    """Reservas ativas só do apartamento informado."""
    with dados.conectar() as conn:
        linhas = conn.execute(
            "SELECT codigo, area, data FROM reservas "
            "WHERE apartamento = ? AND ativa = 1 ORDER BY data",
            (apartamento,),
        ).fetchall()
    return [{"codigo": c, "area": a, "data": d} for c, a, d in linhas]


def visitantes_do(apartamento: str) -> list[dict]:
    with dados.conectar() as conn:
        linhas = conn.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data",
            (apartamento,),
        ).fetchall()
    return [{"nome": n, "data": d} for n, d in linhas]


def registrar_visitante(apartamento: str, nome: str, data: str) -> None:
    with dados.conectar() as conn:
        conn.execute(
            "INSERT INTO visitantes(apartamento, nome, data) VALUES (?, ?, ?)",
            (apartamento, nome, data),
        )
