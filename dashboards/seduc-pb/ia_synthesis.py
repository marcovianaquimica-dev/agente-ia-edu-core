"""Sintese executiva curta via IA, cacheada por corte e hash dos dados
agregados de entrada. A entrada e sempre dados ja agregados (nunca texto
de redacao ou devolutiva) - ver Global Constraints do plano.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Callable

GeradorDeSintese = Callable[[dict], str]

MODELO_SINTESE = "gpt-5.6-luna"

PROMPT_SISTEMA = (
    "Voce resume indicadores educacionais agregados em um texto executivo "
    "curto (2 a 4 frases), em portugues, para gestores da SEDUC-PB. Use "
    "somente os numeros fornecidos, nao invente dados."
)


def calcular_hash(dados: dict) -> str:
    serializado = json.dumps(dados, sort_keys=True, default=str)
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def obter_ou_gerar_sintese(
    conn: sqlite3.Connection,
    corte: str,
    dados_agregados: dict,
    gerar: GeradorDeSintese,
) -> str:
    hash_atual = calcular_hash(dados_agregados)
    linha = conn.execute(
        "SELECT texto, hash_dados FROM sinteses WHERE corte = ?", (corte,)
    ).fetchone()
    if linha is not None and linha["hash_dados"] == hash_atual:
        return linha["texto"]

    texto = gerar(dados_agregados)
    agora = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO sinteses (corte, texto, hash_dados, gerado_em)
        VALUES (:corte, :texto, :hash_dados, :gerado_em)
        ON CONFLICT(corte) DO UPDATE SET
            texto = excluded.texto,
            hash_dados = excluded.hash_dados,
            gerado_em = excluded.gerado_em
        """,
        {"corte": corte, "texto": texto, "hash_dados": hash_atual, "gerado_em": agora},
    )
    conn.commit()
    return texto


def gerar_sintese_via_openai(dados_agregados: dict) -> str:
    from openai import OpenAI

    client = OpenAI()
    resposta = client.chat.completions.create(
        model=MODELO_SINTESE,
        messages=[
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": json.dumps(dados_agregados, ensure_ascii=False)},
        ],
    )
    return resposta.choices[0].message.content.strip()
