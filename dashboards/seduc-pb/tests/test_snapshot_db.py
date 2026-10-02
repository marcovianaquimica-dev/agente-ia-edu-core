import sqlite3

import pytest

from snapshot_db import (
    create_schema,
    get_connection,
    insert_redacoes,
    reset_redacoes,
    substituir_redacoes,
)


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


def test_substituir_redacoes_grava_num_unico_lote(conn):
    create_schema(conn)
    insert_redacoes(conn, [REDACAO_EXEMPLO])

    outra_redacao = {**REDACAO_EXEMPLO, "id_redacao": "r2", "nome_aluno": "Aluno Dois"}
    substituir_redacoes(conn, [outra_redacao])

    linhas = conn.execute("SELECT id_redacao FROM redacoes").fetchall()
    assert [linha["id_redacao"] for linha in linhas] == ["r2"]


def test_substituir_redacoes_e_atomico_quando_a_insercao_falha(conn):
    # Se o export job falhasse no meio da insercao sem atomicidade, o DELETE
    # anterior ja teria sido commitado isoladamente - o snapshot ficaria
    # mostrando zero redacoes ate o proximo export rodar com sucesso. Este
    # teste injeta uma falha real (chave primaria duplicada no mesmo lote)
    # para provar que o DELETE e revertido junto com a insercao que falhou.
    create_schema(conn)
    insert_redacoes(conn, [REDACAO_EXEMPLO])

    redacoes_com_duplicata = [
        {**REDACAO_EXEMPLO, "id_redacao": "r2"},
        {**REDACAO_EXEMPLO, "id_redacao": "r2"},  # mesma PK -> IntegrityError
    ]

    with pytest.raises(sqlite3.IntegrityError):
        substituir_redacoes(conn, redacoes_com_duplicata)

    # a transacao inteira (DELETE + INSERT) foi revertida: a redacao
    # original continua la, o snapshot nunca ficou vazio.
    linhas = conn.execute("SELECT id_redacao FROM redacoes").fetchall()
    assert [linha["id_redacao"] for linha in linhas] == ["r1"]
