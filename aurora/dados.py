"""Leitura de dados/ e armazenamento SQLite do condomínio (D-02, D-04, D-05)."""

import json
import os
import secrets
import sqlite3
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTA_DADOS = RAIZ / "dados"

TENTATIVAS_CODIGO = 5
_ERRO_CODIGO = "UNIQUE constraint failed: reservas.codigo"
_ERRO_DATA = "UNIQUE constraint failed: reservas.area, reservas.data"

ESQUEMA = """
CREATE TABLE reservas (
    codigo TEXT PRIMARY KEY,
    apartamento TEXT NOT NULL,
    area TEXT NOT NULL,
    data TEXT NOT NULL,
    ativa INTEGER NOT NULL DEFAULT 1
);
CREATE UNIQUE INDEX ux_reserva_ativa ON reservas(area, data) WHERE ativa = 1;
CREATE TABLE visitantes (
    apartamento TEXT NOT NULL,
    nome TEXT NOT NULL,
    data TEXT NOT NULL
);
"""


def pasta_var() -> Path:
    """Pasta dos bancos, lida a cada chamada (testes apontam para tmp_path)."""
    pasta = Path(os.environ.get("AURORA_VAR_DIR", "var"))
    return pasta if pasta.is_absolute() else RAIZ / pasta


def caminho_condominio() -> Path:
    return pasta_var() / "condominio.db"


def caminho_sessoes() -> Path:
    return pasta_var() / "sessoes.db"


@contextmanager
def conectar():
    """Abre var/condominio.db, confirma ao sair e sempre fecha."""
    pasta_var().mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(caminho_condominio())
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _json(nome: str) -> list[dict]:
    return json.loads((PASTA_DADOS / nome).read_text(encoding="utf-8"))


def carregar_apartamentos() -> list[dict]:
    return _json("apartamentos.json")


def carregar_areas() -> list[dict]:
    return _json("areas.json")


def carregar_reservas_iniciais() -> list[dict]:
    return _json("reservas.json")


def carregar_visitantes_iniciais() -> list[dict]:
    return _json("visitantes.json")


def carregar_regulamento() -> str:
    return (PASTA_DADOS / "regulamento.md").read_text(encoding="utf-8")


def criar_esquema(conn: sqlite3.Connection) -> None:
    conn.executescript(ESQUEMA)


def carregar_iniciais(conn: sqlite3.Connection) -> None:
    conn.executemany(
        "INSERT INTO reservas(codigo, apartamento, area, data, ativa) "
        "VALUES (:codigo, :apartamento, :area, :data, 1)",
        carregar_reservas_iniciais(),
    )
    conn.executemany(
        "INSERT INTO visitantes(apartamento, nome, data) "
        "VALUES (:apartamento, :nome, :data)",
        carregar_visitantes_iniciais(),
    )


def gerar_codigo() -> str:
    return "RSV-" + secrets.token_hex(4).upper()


def gravar_reserva(
    apartamento: str,
    area: str,
    data: str,
    gerador: Callable[[], str] = gerar_codigo,
) -> dict:
    """INSERT atômico; a exclusividade é do índice único parcial (G5)."""
    with conectar() as conn:
        for _ in range(TENTATIVAS_CODIGO):
            codigo = gerador()
            try:
                conn.execute(
                    "INSERT INTO reservas(codigo, apartamento, area, data, ativa) "
                    "VALUES (?, ?, ?, ?, 1)",
                    (codigo, apartamento, area, data),
                )
            except sqlite3.IntegrityError as erro:
                mensagem = str(erro)
                if _ERRO_DATA in mensagem:
                    return {"reservado": False, "motivo": "data ocupada"}
                if _ERRO_CODIGO in mensagem:
                    continue
                raise
            return {"reservado": True, "codigo": codigo}
    raise RuntimeError("não foi possível gerar um código de reserva único")


def cancelar_reserva(apartamento: str, area: str, data: str) -> bool:
    """Marca ativa = 0 só na reserva ativa do apartamento; a linha fica."""
    with conectar() as conn:
        cur = conn.execute(
            "UPDATE reservas SET ativa = 0 "
            "WHERE apartamento = ? AND area = ? AND data = ? AND ativa = 1",
            (apartamento, area, data),
        )
        return cur.rowcount > 0
