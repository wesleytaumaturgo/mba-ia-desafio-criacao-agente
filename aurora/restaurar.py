"""Restauração (D-03): uv run python -m aurora.restaurar."""

from aurora import dados


def restaurar() -> None:
    for caminho in (dados.caminho_condominio(), dados.caminho_sessoes()):
        caminho.unlink(missing_ok=True)
        for sufixo in ("-wal", "-shm", "-journal"):
            caminho.with_name(caminho.name + sufixo).unlink(missing_ok=True)
    with dados.conectar() as conn:
        dados.criar_esquema(conn)
        dados.carregar_iniciais(conn)


if __name__ == "__main__":
    restaurar()
    print("restaurado: reservas e visitantes de dados/, sessões apagadas")
