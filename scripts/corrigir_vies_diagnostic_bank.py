"""Tira o vies posicional do Nucleo Diagnostic Bank - fonte e banco.

O QUE ACONTECEU
===============
O bloco que criou o banco MEDIU o vies nos itens candidatos e corrigiu ali:
9 gabaritos em "B" viraram 21/21/21/21/14%. Mas a correcao ficou no pipeline
de geracao. O arquivo que foi efetivamente carregado -
`scripts/data/diagnostic_bank_balanceamento_v2.json` - e ANTERIOR a ela, e os
14 itens que o aluno responde chegaram com 9 gabaritos em "B".

Descoberto em 2026-10-04 conferindo o Caminho A no navegador.

POR QUE E GRAVE
===============
Num teste comum, vies posicional infla uma nota. Num DIAGNOSTICO ele produz
FALSO-PRONTO: quem marca "B" em tudo acerta 64%, a politica conclui que ele
domina Balanceamento, e o sistema o libera para Estequiometria sem que ele
saiba balancear uma equacao. O instrumento mente sobre a pessoa que deveria
estar medindo.

O QUE ESTE SCRIPT FAZ
=====================
1. aplica `redistribuir_gabaritos` ao ARQUIVO-FONTE e regrava;
2. propaga para o BANCO: texto das alternativas, `is_valid_option` e a entrada
   de gabarito (`AnswerKeyEntry`) de cada item.

A QUIMICA NAO MUDA. A redistribuicao reordena alternativas - o mesmo item, as
mesmas cinco opcoes, em outra ordem - de forma deterministica, para ser
reproduzivel e auditavel. Ha teste conferindo que as cinco continuam
distintas e que a correta continua tendo texto.

Idempotente: rodar de novo nao muda nada.

Uso:
    PYTHONPATH=src .venv/bin/python scripts/corrigir_vies_diagnostic_bank.py
    ... --conferir     (so relata a distribuicao)
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import pathlib
import sys

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
if str(_RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(_RAIZ / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession, async_sessionmaker, create_async_engine,
)

from agente_ia_edu.db.models import (  # noqa: E402
    AnswerKeyEntry, BookletQuestion, Question, QuestionOption, QuestionVersion,
)
from agente_ia_edu.services.diagnostic_bank import (  # noqa: E402
    ItemCandidato, redistribuir_gabaritos,
)
from agente_ia_edu.services.formula_quimica import subscrever  # noqa: E402

FONTE = _RAIZ / "scripts" / "data" / "diagnostic_bank_balanceamento_v2.json"
BANK_TAG = "nucleo-diagnostic-bank-v1"
LETRAS = ("A", "B", "C", "D", "E")


def url() -> str:
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    env = _RAIZ / ".env"
    vals = {}
    if env.exists():
        for linha in env.read_text(encoding="utf-8").splitlines():
            linha = linha.strip()
            if linha and not linha.startswith("#") and "=" in linha:
                k, _, v = linha.partition("=")
                vals[k.strip()] = v.strip()
    return (f"postgresql+psycopg://{vals.get('POSTGRES_USER', 'agenteedu')}:"
            f"{vals.get('POSTGRES_PASSWORD', 'agenteedu_dev')}@localhost:5433/"
            f"{vals.get('POSTGRES_DB', 'agente_ia_edu')}")


def distribuicao(itens) -> dict:
    return dict(sorted(collections.Counter(
        i["correct_answer"] for i in itens).items()))


def corrigir_fonte() -> list[dict]:
    bruto = json.loads(FONTE.read_text(encoding="utf-8"))
    aprovados = [r for r in bruto if r["decisao"]["status"] == "AI_VERIFIED"]
    itens = [r["item"] for r in aprovados]
    print("  antes :", distribuicao(itens))

    candidatos = [ItemCandidato(**dict(i)) for i in itens]
    novos = redistribuir_gabaritos(candidatos)
    for registro, novo in zip(aprovados, novos):
        registro["item"]["options"] = dict(novo.options)
        registro["item"]["correct_answer"] = novo.correct_answer

    FONTE.write_text(json.dumps(bruto, ensure_ascii=False, indent=2),
                     encoding="utf-8")
    print("  depois:", distribuicao([r["item"] for r in aprovados]))
    return [r["item"] for r in aprovados]


async def propagar(s: AsyncSession, itens: list[dict]) -> int:
    """Leva a nova ordem para o banco, casando pelo ENUNCIADO.

    O enunciado identifica o item melhor que a posicao: a redistribuicao nao
    o altera, e casar por indice assumiria que a ordem de insercao nunca muda.
    """
    por_enunciado = {subscrever(i["stem"]): i for i in itens}

    linhas = (await s.execute(
        select(QuestionVersion)
        .join(Question, Question.id == QuestionVersion.question_id)
        .where(Question.metadata_["bank"].as_string() == BANK_TAG)
    )).scalars().all()

    tocados = 0
    for v in linhas:
        item = por_enunciado.get(v.statement)
        if item is None:
            print(f"  [aviso] item do banco sem correspondencia na fonte: "
                  f"{(v.statement or '')[:60]!r}")
            continue
        esperado = {k: subscrever(t) for k, t in item["options"].items()}
        correta = item["correct_answer"]

        opcoes = {o.option_key: o for o in (await s.execute(
            select(QuestionOption).where(
                QuestionOption.question_version_id == v.id))).scalars().all()}
        mudou = False
        for letra in LETRAS:
            o = opcoes.get(letra)
            if o is None:
                continue
            if o.text != esperado.get(letra):
                o.text = esperado[letra]
                mudou = True
            certa = (letra == correta)
            if bool(o.is_valid_option) != certa:
                o.is_valid_option = certa
                mudou = True

        # O gabarito oficial do caderno precisa acompanhar, senao a correcao
        # deterministica continua apontando para a letra antiga.
        bq = (await s.execute(select(BookletQuestion).where(
            BookletQuestion.question_version_id == v.id))).scalars().first()
        if bq is not None:
            entrada = (await s.execute(select(AnswerKeyEntry).where(
                AnswerKeyEntry.booklet_question_id == bq.id))).scalars().first()
            if entrada is not None and entrada.official_answer_label != correta:
                entrada.official_answer_label = correta
                entrada.resolved_option_id = opcoes[correta].id
                mudou = True

        tocados += int(mudou)
    return tocados


async def conferir(s: AsyncSession) -> None:
    linhas = (await s.execute(
        select(QuestionOption.option_key)
        .join(QuestionVersion, QuestionVersion.id == QuestionOption.question_version_id)
        .join(Question, Question.id == QuestionVersion.question_id)
        .where(Question.metadata_["bank"].as_string() == BANK_TAG,
               QuestionOption.is_valid_option.is_(True))
    )).all()
    c = collections.Counter(r[0] for r in linhas)
    total = sum(c.values()) or 1
    print("\n  no banco:")
    for letra in LETRAS:
        n = c.get(letra, 0)
        print(f"    {letra}  {n:2}  {n / total:5.0%}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conferir", action="store_true")
    args = ap.parse_args()

    engine = create_async_engine(url())
    fabrica = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with fabrica() as s:
            if args.conferir:
                await conferir(s)
                return
            print("ARQUIVO-FONTE")
            itens = corrigir_fonte()
            print("\nBANCO")
            n = await propagar(s, itens)
            await s.commit()
            print(f"  {n} iten(s) atualizado(s)")
            await conferir(s)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
