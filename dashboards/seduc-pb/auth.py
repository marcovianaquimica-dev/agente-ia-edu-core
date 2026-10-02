"""Autenticacao simples do dashboard SEDUC-PB: usuario/senha, sem escopo por perfil."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import bcrypt

from snapshot_db import create_schema, get_connection


def criar_usuario(conn: sqlite3.Connection, login: str, senha: str) -> None:
    senha_hash = bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    conn.execute(
        "INSERT INTO usuarios (login, senha_hash) VALUES (?, ?)",
        (login, senha_hash),
    )
    conn.commit()


def verificar_login(conn: sqlite3.Connection, login: str, senha: str) -> bool:
    linha = conn.execute(
        "SELECT senha_hash FROM usuarios WHERE login = ?", (login,)
    ).fetchone()
    if linha is None:
        return False
    return bcrypt.checkpw(senha.encode("utf-8"), linha["senha_hash"].encode("utf-8"))


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Cria um usuario no snapshot do dashboard SEDUC-PB"
    )
    parser.add_argument("--snapshot-path", required=True)
    parser.add_argument("--login", required=True)
    parser.add_argument("--senha", required=True)
    args = parser.parse_args()

    conexao = get_connection(Path(args.snapshot_path))
    create_schema(conexao)
    criar_usuario(conexao, args.login, args.senha)
    print(f"usuario '{args.login}' criado em {args.snapshot_path}")


if __name__ == "__main__":
    main()
