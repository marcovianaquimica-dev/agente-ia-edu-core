"""Plano de acao curto gerado por IA, cacheado por corte e hash dos dados
agregados de entrada. A entrada e sempre dados ja agregados (nunca texto de
redacao ou devolutiva) - ver Global Constraints do plano original e a spec.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Callable

GeradorDePlanoAcao = Callable[[dict], dict]

MODELO_SINTESE = "gpt-5.6-luna"

CHAVES_PLANO_ACAO = ("ponto_forte", "ponto_atencao", "recomendacao")

PROMPT_SISTEMA = (
    "Voce analisa indicadores educacionais agregados (nota media, distribuicao "
    "por faixa, medias por competencia C1-C5) e produz um diagnostico curto "
    "para gestores da SEDUC-PB. Responda em JSON estrito com exatamente estas "
    "3 chaves, cada uma com 1 frase: \"ponto_forte\" (o que esta indo bem), "
    "\"ponto_atencao\" (a maior fragilidade) e \"recomendacao\" (uma orientacao "
    "pedagogica concreta). Use somente os numeros fornecidos, nunca invente dados."
)


def calcular_hash(dados: dict) -> str:
    serializado = json.dumps(dados, sort_keys=True, default=str)
    return hashlib.sha256(serializado.encode("utf-8")).hexdigest()


def obter_ou_gerar_plano_acao(
    conn: sqlite3.Connection,
    corte: str,
    dados_agregados: dict,
    gerar: GeradorDePlanoAcao,
) -> dict:
    hash_atual = calcular_hash(dados_agregados)
    linha = conn.execute(
        "SELECT texto, hash_dados FROM sinteses WHERE corte = ?", (corte,)
    ).fetchone()
    if linha is not None and linha["hash_dados"] == hash_atual:
        return json.loads(linha["texto"])

    plano = gerar(dados_agregados)
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
        {
            "corte": corte,
            "texto": json.dumps(plano, ensure_ascii=False),
            "hash_dados": hash_atual,
            "gerado_em": agora,
        },
    )
    conn.commit()
    return plano


def _validar_plano_acao(bruto: dict) -> dict:
    if not all(chave in bruto for chave in CHAVES_PLANO_ACAO):
        raise ValueError("resposta da IA nao tem as 3 chaves esperadas do plano de acao")
    return {chave: str(bruto[chave]) for chave in CHAVES_PLANO_ACAO}


def gerar_plano_acao_via_openai(dados_agregados: dict) -> dict:
    from openai import OpenAI

    client = OpenAI()
    resposta = client.chat.completions.create(
        model=MODELO_SINTESE,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": PROMPT_SISTEMA},
            {"role": "user", "content": json.dumps(dados_agregados, ensure_ascii=False)},
        ],
    )
    bruto = json.loads(resposta.choices[0].message.content)
    return _validar_plano_acao(bruto)
