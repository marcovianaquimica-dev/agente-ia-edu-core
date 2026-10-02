from export_snapshot import exportar
from snapshot_db import get_connection


def test_exportar_grava_somente_correcoes_aprovadas(tmp_path, pg_com_dados_sinteticos):
    pg_conn, ids = pg_com_dados_sinteticos
    sqlite_conn = get_connection(tmp_path / "snapshot.db")

    total = exportar(pg_conn, sqlite_conn)

    assert total == 2
    linhas = sqlite_conn.execute(
        "SELECT nome_aluno, turma_nome, faixa_classificacao, posicao_geral "
        "FROM redacoes ORDER BY nome_aluno"
    ).fetchall()
    assert [dict(linha) for linha in linhas] == [
        {"nome_aluno": "Aluno Tres", "turma_nome": None, "faixa_classificacao": "Alto", "posicao_geral": 1},
        {"nome_aluno": "Aluno Um", "turma_nome": "3o Ano A", "faixa_classificacao": "Alto", "posicao_geral": 1},
    ]


def test_exportar_e_idempotente(tmp_path, pg_com_dados_sinteticos):
    pg_conn, ids = pg_com_dados_sinteticos
    sqlite_conn = get_connection(tmp_path / "snapshot.db")

    exportar(pg_conn, sqlite_conn)
    total_segunda_vez = exportar(pg_conn, sqlite_conn)

    assert total_segunda_vez == 2
    total_linhas = sqlite_conn.execute("SELECT count(*) FROM redacoes").fetchone()[0]
    assert total_linhas == 2
