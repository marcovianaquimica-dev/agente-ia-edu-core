import sqlite3

import pytest

from auth import criar_usuario, verificar_login
from snapshot_db import create_schema, get_connection


@pytest.fixture
def conn(tmp_path):
    conexao = get_connection(tmp_path / "snapshot.db")
    create_schema(conexao)
    return conexao


def test_login_correto_retorna_true(conn):
    criar_usuario(conn, "gestor", "senha-correta")
    assert verificar_login(conn, "gestor", "senha-correta") is True


def test_login_com_senha_errada_retorna_false(conn):
    criar_usuario(conn, "gestor", "senha-correta")
    assert verificar_login(conn, "gestor", "senha-errada") is False


def test_login_inexistente_retorna_false(conn):
    assert verificar_login(conn, "ninguem", "qualquer") is False


def test_criar_usuario_duplicado_falha(conn):
    criar_usuario(conn, "gestor", "senha-1")
    with pytest.raises(sqlite3.IntegrityError):
        criar_usuario(conn, "gestor", "senha-2")
