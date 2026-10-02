"""Consultas de leitura sobre o snapshot - usadas pelas telas do app web."""
from __future__ import annotations

import sqlite3

ORDENACOES_VALIDAS = {"nota_final", "c1", "c2", "c3", "c4", "c5"}


def _calcular_metricas(linhas: list[sqlite3.Row]) -> dict:
    notas = [linha["nota_final"] for linha in linhas]
    total = len(notas)
    if total == 0:
        return {
            "total": 0,
            "media": 0,
            "mediana": 0,
            "desvio_padrao": 0,
            "distribuicao_faixas": {},
            "distribuicao_faixas_pct": {},
        }
    media = sum(notas) / total
    ordenadas = sorted(notas)
    meio = total // 2
    mediana = ordenadas[meio] if total % 2 == 1 else (ordenadas[meio - 1] + ordenadas[meio]) / 2
    variancia = sum((n - media) ** 2 for n in notas) / total
    desvio_padrao = variancia ** 0.5
    distribuicao: dict[str, int] = {}
    for linha in linhas:
        distribuicao[linha["faixa_classificacao"]] = distribuicao.get(linha["faixa_classificacao"], 0) + 1
    distribuicao_faixas_pct = {
        faixa: round(quantidade / total * 100, 1) for faixa, quantidade in distribuicao.items()
    }
    return {
        "total": total,
        "media": round(media, 1),
        # normalizado para float: sem isso, a mediana de uma quantidade impar
        # de notas (indice direto, sem divisao) chega como int do sqlite e
        # renderiza diferente da mediana de uma quantidade par (sempre float).
        "mediana": round(float(mediana), 1),
        "desvio_padrao": round(desvio_padrao, 1),
        "distribuicao_faixas": distribuicao,
        "distribuicao_faixas_pct": distribuicao_faixas_pct,
    }


def buscar_metricas_gerais(conn: sqlite3.Connection) -> dict:
    linhas = conn.execute("SELECT nota_final, faixa_classificacao FROM redacoes").fetchall()
    return _calcular_metricas(linhas)


def buscar_metricas_por_escola(conn: sqlite3.Connection, escola_id: str) -> dict:
    linhas = conn.execute(
        "SELECT nota_final, faixa_classificacao FROM redacoes WHERE escola_id = ?",
        (escola_id,),
    ).fetchall()
    return _calcular_metricas(linhas)


def buscar_escolas(conn: sqlite3.Connection) -> list[dict]:
    linhas = conn.execute(
        "SELECT DISTINCT escola_id, escola_nome FROM redacoes ORDER BY escola_nome"
    ).fetchall()
    return [dict(linha) for linha in linhas]


def buscar_turmas_por_escola(conn: sqlite3.Connection, escola_id: str) -> list[dict]:
    linhas = conn.execute(
        """
        SELECT turma_id, turma_nome, AVG(nota_final) AS media_nota
        FROM redacoes
        WHERE escola_id = ? AND turma_id IS NOT NULL
        GROUP BY turma_id, turma_nome
        ORDER BY turma_nome
        """,
        (escola_id,),
    ).fetchall()
    return [
        {
            "turma_id": linha["turma_id"],
            "turma_nome": linha["turma_nome"],
            "media_nota": round(linha["media_nota"], 1),
        }
        for linha in linhas
    ]


def escola_existe(conn: sqlite3.Connection, escola_id: str) -> bool:
    linha = conn.execute(
        "SELECT 1 FROM redacoes WHERE escola_id = ? LIMIT 1", (escola_id,)
    ).fetchone()
    return linha is not None


def turma_existe(conn: sqlite3.Connection, turma_id: str) -> bool:
    linha = conn.execute(
        "SELECT 1 FROM redacoes WHERE turma_id = ? LIMIT 1", (turma_id,)
    ).fetchone()
    return linha is not None


def buscar_nome_escola(conn: sqlite3.Connection, escola_id: str) -> str | None:
    linha = conn.execute(
        "SELECT escola_nome FROM redacoes WHERE escola_id = ? LIMIT 1", (escola_id,)
    ).fetchone()
    return linha["escola_nome"] if linha is not None else None


def buscar_ranking(
    conn: sqlite3.Connection,
    escola_id: str | None = None,
    turma_id: str | None = None,
    ordenar_por: str = "nota_final",
) -> list[dict]:
    if ordenar_por not in ORDENACOES_VALIDAS:
        ordenar_por = "nota_final"
    condicoes = []
    parametros: list[str] = []
    if escola_id:
        condicoes.append("escola_id = ?")
        parametros.append(escola_id)
    if turma_id:
        condicoes.append("turma_id = ?")
        parametros.append(turma_id)
    where = f"WHERE {' AND '.join(condicoes)}" if condicoes else ""
    linhas = conn.execute(
        f"""
        SELECT posicao_geral, nome_aluno, escola_id, escola_nome, turma_nome,
               nota_final, c1, c2, c3, c4, c5, faixa_classificacao
        FROM redacoes
        {where}
        ORDER BY {ordenar_por} DESC
        """,
        parametros,
    ).fetchall()
    return [dict(linha) for linha in linhas]


def buscar_redacoes_por_turma(conn: sqlite3.Connection, turma_id: str) -> list[dict]:
    linhas = conn.execute(
        """
        SELECT nome_aluno, nota_final, c1, c2, c3, c4, c5, faixa_classificacao
        FROM redacoes
        WHERE turma_id = ?
        ORDER BY nota_final DESC
        """,
        (turma_id,),
    ).fetchall()
    return [dict(linha) for linha in linhas]
