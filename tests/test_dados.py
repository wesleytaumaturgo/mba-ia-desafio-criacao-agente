import sqlite3

import pytest

from aurora import dados, restaurar

STARTER = {"RSV-1377", "RSV-4821", "RSV-2950"}


@pytest.fixture(autouse=True)
def var_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("AURORA_VAR_DIR", str(tmp_path))
    return tmp_path


def _contar(tabela, where="1=1"):
    with dados.conectar() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {tabela} WHERE {where}").fetchone()[0]


def test_restauracao_carrega_reservas_e_visitantes():
    restaurar.restaurar()
    assert _contar("reservas") == 3
    assert _contar("visitantes") == 2
    with dados.conectar() as conn:
        codigos = {r[0] for r in conn.execute("SELECT codigo FROM reservas")}
    assert codigos == STARTER


def test_restauracao_apaga_bancos_anteriores():
    restaurar.restaurar()
    dados.gravar_reserva("102", "quadra", "2031-01-01")
    (dados.pasta_var() / "sessoes.db").write_bytes(b"")
    restaurar.restaurar()
    assert _contar("reservas") == 3
    assert (dados.pasta_var() / "sessoes.db").exists() is False


def test_segundo_insert_ativo_na_mesma_area_e_data_viola_indice():
    restaurar.restaurar()
    with dados.conectar() as conn:
        with pytest.raises(sqlite3.IntegrityError) as erro:
            conn.execute(
                "INSERT INTO reservas(codigo, apartamento, area, data, ativa) "
                "VALUES ('RSV-ZZZZZZZZ', '102', 'quadra', '2030-03-09', 1)"
            )
    assert "reservas.area, reservas.data" in str(erro.value)


def test_gravar_reserva_data_ocupada_vira_resposta_normal():
    restaurar.restaurar()
    r = dados.gravar_reserva("102", "quadra", "2030-03-09")
    assert r == {"reservado": False, "motivo": "data ocupada"}
    assert _contar("reservas") == 3


def test_gravar_reserva_livre_devolve_codigo_rsv():
    restaurar.restaurar()
    r = dados.gravar_reserva("102", "quadra", "2031-05-05")
    assert r["reservado"] is True
    assert r["codigo"].startswith("RSV-") and len(r["codigo"]) == 12
    assert r["codigo"] == r["codigo"].upper()
    assert _contar("reservas") == 4


def test_colisao_de_codigo_gera_nova_tentativa_e_grava():
    restaurar.restaurar()
    codigos = iter(["RSV-1377", "RSV-AAAAAAAA"])
    r = dados.gravar_reserva("102", "quadra", "2031-05-05", gerador=lambda: next(codigos))
    assert r == {"reservado": True, "codigo": "RSV-AAAAAAAA"}
    assert _contar("reservas") == 4


def test_colisao_de_codigo_esgota_cinco_tentativas():
    restaurar.restaurar()
    with pytest.raises(RuntimeError):
        dados.gravar_reserva("102", "quadra", "2031-05-05", gerador=lambda: "RSV-1377")
    assert _contar("reservas") == 3


def test_cancelada_libera_a_data_e_o_codigo_nao_volta():
    restaurar.restaurar()
    assert dados.cancelar_reserva("101", "quadra", "2030-03-09") is True
    assert _contar("reservas", "codigo = 'RSV-1377' AND ativa = 0") == 1
    r = dados.gravar_reserva("102", "quadra", "2030-03-09")
    assert r["reservado"] is True
    assert r["codigo"] != "RSV-1377"
    assert _contar("reservas") == 4


def test_mil_codigos_sem_repeticao_e_fora_do_starter():
    codigos = [dados.gerar_codigo() for _ in range(1000)]
    assert len(set(codigos)) == 1000
    assert not (set(codigos) & STARTER)


def test_leitura_de_dados_somente_leitura():
    assert len(dados.carregar_apartamentos()) == 6
    assert {a["id"] for a in dados.carregar_areas()} == {
        "salao-de-festas",
        "churrasqueira",
        "quadra",
    }
    assert dados.carregar_regulamento().startswith("#")


def test_var_dir_padrao_relativo_a_raiz(monkeypatch):
    monkeypatch.delenv("AURORA_VAR_DIR")
    assert dados.pasta_var() == dados.RAIZ / "var"
