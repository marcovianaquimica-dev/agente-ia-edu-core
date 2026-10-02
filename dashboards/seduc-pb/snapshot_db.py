"""Esquema e operacoes basicas do snapshot SQLite do dashboard SEDUC-PB."""
from __future__ import annotations

import sqlite3
from pathlib import Path


def get_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def create_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS redacoes (
            id_redacao TEXT PRIMARY KEY,
            id_aluno TEXT NOT NULL,
            nome_aluno TEXT NOT NULL,
            escola_id TEXT NOT NULL,
            escola_nome TEXT NOT NULL,
            turma_id TEXT,
            turma_nome TEXT,
            nota_final INTEGER NOT NULL,
            c1 INTEGER NOT NULL,
            c2 INTEGER NOT NULL,
            c3 INTEGER NOT NULL,
            c4 INTEGER NOT NULL,
            c5 INTEGER NOT NULL,
            faixa_classificacao TEXT NOT NULL,
            posicao_geral INTEGER NOT NULL,
            data_correcao TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sinteses (
            corte TEXT PRIMARY KEY,
            texto TEXT NOT NULL,
            hash_dados TEXT NOT NULL,
            gerado_em TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS usuarios (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            login TEXT UNIQUE NOT NULL,
            senha_hash TEXT NOT NULL
        )
        """
    )
    conn.commit()


CAMPOS_REDACAO = [
    "id_redacao", "id_aluno", "nome_aluno", "escola_id", "escola_nome",
    "turma_id", "turma_nome", "nota_final", "c1", "c2", "c3", "c4", "c5",
    "faixa_classificacao", "posicao_geral", "data_correcao",
]


def reset_redacoes(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM redacoes")
    conn.commit()


def insert_redacoes(conn: sqlite3.Connection, redacoes: list[dict]) -> None:
    colunas = ", ".join(CAMPOS_REDACAO)
    marcadores = ", ".join(f":{campo}" for campo in CAMPOS_REDACAO)
    conn.executemany(
        f"INSERT INTO redacoes ({colunas}) VALUES ({marcadores})",
        redacoes,
    )
    conn.commit()
