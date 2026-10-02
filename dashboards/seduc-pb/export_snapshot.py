"""Export job: le o Postgres principal (somente leitura) e grava um
snapshot em SQLite para o dashboard SEDUC-PB consultar.
"""
from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from faixas import classificar
from ranking import calcular_ranking
from snapshot_db import create_schema, get_connection, substituir_redacoes

QUERY_REDACOES_APROVADAS = """
    SELECT
        ec.id AS id_redacao,
        es.student_id AS id_aluno,
        p.full_name AS nome_aluno,
        sc.id AS escola_id,
        sc.name AS escola_nome,
        cl.id AS turma_id,
        cl.name AS turma_nome,
        ec.final_scores AS final_scores,
        ec.created_at AS data_correcao
    FROM essay_corrections ec
    JOIN essay_submissions es ON es.id = ec.essay_submission_id
    JOIN students st ON st.id = es.student_id
    JOIN persons p ON p.id = st.person_id
    JOIN schools sc ON sc.id = es.school_id
    LEFT JOIN LATERAL (
        SELECT se.class_id
        FROM student_enrollments se
        WHERE se.student_id = es.student_id AND se.status = 'ACTIVE'
        ORDER BY se.enrolled_on DESC
        LIMIT 1
    ) matricula ON true
    LEFT JOIN classes cl ON cl.id = matricula.class_id
    WHERE ec.status = 'APPROVED'
    ORDER BY ec.created_at
"""


def buscar_redacoes_aprovadas(pg_conn: psycopg.Connection) -> list[dict]:
    with pg_conn.cursor(row_factory=dict_row) as cur:
        cur.execute(QUERY_REDACOES_APROVADAS)
        return cur.fetchall()


def montar_redacao_snapshot(linha: dict, posicao: int) -> dict:
    notas = linha["final_scores"]["per_competency"]
    nota_final = linha["final_scores"]["total"]
    return {
        "id_redacao": str(linha["id_redacao"]),
        "id_aluno": str(linha["id_aluno"]),
        "nome_aluno": linha["nome_aluno"],
        "escola_id": str(linha["escola_id"]),
        "escola_nome": linha["escola_nome"],
        "turma_id": str(linha["turma_id"]) if linha["turma_id"] else None,
        "turma_nome": linha["turma_nome"],
        "nota_final": nota_final,
        "c1": notas["C1"]["points"],
        "c2": notas["C2"]["points"],
        "c3": notas["C3"]["points"],
        "c4": notas["C4"]["points"],
        "c5": notas["C5"]["points"],
        "faixa_classificacao": classificar(nota_final),
        "posicao_geral": posicao,
        "data_correcao": linha["data_correcao"].isoformat(),
    }


def exportar(pg_conn: psycopg.Connection, sqlite_conn: sqlite3.Connection) -> int:
    linhas = buscar_redacoes_aprovadas(pg_conn)
    ranking = calcular_ranking(
        [(str(linha["id_redacao"]), linha["final_scores"]["total"]) for linha in linhas]
    )
    redacoes = [
        montar_redacao_snapshot(linha, ranking[str(linha["id_redacao"])]) for linha in linhas
    ]
    create_schema(sqlite_conn)
    substituir_redacoes(sqlite_conn, redacoes)
    return len(redacoes)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Exporta um snapshot do banco principal para o dashboard SEDUC-PB"
    )
    parser.add_argument(
        "--source-database-url",
        default=os.environ.get("SEDUC_DASHBOARD_SOURCE_DATABASE_URL"),
    )
    parser.add_argument(
        "--snapshot-path",
        default=str(Path(__file__).resolve().parent / "snapshot.db"),
    )
    args = parser.parse_args()
    if not args.source_database_url:
        raise SystemExit(
            "defina SEDUC_DASHBOARD_SOURCE_DATABASE_URL ou passe --source-database-url"
        )
    pg_conn = psycopg.connect(args.source_database_url)
    sqlite_conn = get_connection(Path(args.snapshot_path))
    total = exportar(pg_conn, sqlite_conn)
    print(f"{total} redacoes exportadas para {args.snapshot_path}")


if __name__ == "__main__":
    main()
