import pytest

from snapshot_db import create_schema, get_connection, insert_redacoes, reset_redacoes


@pytest.fixture
def conn(tmp_path):
    return get_connection(tmp_path / "snapshot.db")


def test_create_schema_cria_as_tres_tabelas(conn):
    create_schema(conn)
    tabelas = {
        linha["name"]
        for linha in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {"redacoes", "sinteses", "usuarios"} <= tabelas


def test_create_schema_e_idempotente(conn):
    create_schema(conn)
    create_schema(conn)  # nao deve falhar ao rodar de novo


REDACAO_EXEMPLO = {
    "id_redacao": "r1",
    "id_aluno": "a1",
    "nome_aluno": "Aluno Um",
    "escola_id": "e1",
    "escola_nome": "Escola Um",
    "turma_id": "t1",
    "turma_nome": "Turma A",
    "nota_final": 800,
    "c1": 160, "c2": 160, "c3": 160, "c4": 160, "c5": 160,
    "faixa_classificacao": "Alto",
    "posicao_geral": 1,
    "data_correcao": "2026-09-30T10:00:00",
}


def test_insert_e_reset_redacoes(conn):
    create_schema(conn)
    insert_redacoes(conn, [REDACAO_EXEMPLO])
    total_apos_insert = conn.execute("SELECT count(*) FROM redacoes").fetchone()[0]
    assert total_apos_insert == 1

    reset_redacoes(conn)
    total_apos_reset = conn.execute("SELECT count(*) FROM redacoes").fetchone()[0]
    assert total_apos_reset == 0
