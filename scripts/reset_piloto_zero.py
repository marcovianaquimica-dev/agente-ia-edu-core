"""Volta o PILOTO ZERO ao estado inicial, e so ele.

O QUE ESTE SCRIPT APAGA
=======================
As RESPOSTAS e a EVIDENCIA do Aluno Teste A, as atividades que ele mesmo
comecou (praticas e microdiagnosticos) e o que ele JA LEU. Depois disso ele
volta a ser um aluno sem historico, e o cenario recomeca do zero.

A leitura entrou em 2026-10-05: `MaterialProgress` nao e evidencia de dominio,
entao sobrevivia ao reset - e com ele o Assessor via "ja estudou" e pulava a
explicacao, justamente o caminho que este script existe para repetir.

O QUE ELE NAO APAGA, NUNCA
===========================
    Question Bank        561 questoes importadas
    extracoes            2.972
    classificacoes       as do acervo
    Diagnostic Bank      os 14 itens AI_VERIFIED de Balanceamento
    material teorico     a explicacao de Balanceamento (conteudo do Nucleo)
    outras escolas       Partner, BWalk26, ESCOLA TESTE
    outros usuarios      alice, bruno, hugo, prof_mendes...
    a atividade da escola

O alvo e sempre o aluno do piloto, por `student_external_id`, e as atividades
que ele iniciou. A atividade de Estequiometria CONTINUA: ela e da turma, foi
criada pelo professor, e apagar seria apagar trabalho da escola.

POR QUE APAGAR A EVIDENCIA E NAO O ALUNO
=========================================
Derrubar e recriar Person/User/Student arrastaria matricula, vinculo e
qualquer coisa que venha a apontar para eles. O que torna o teste repetivel e
o aluno nao ter historico - entao e o historico que sai.

Uso:
    .venv/bin/python scripts/reset_piloto_zero.py            (pede confirmacao)
    .venv/bin/python scripts/reset_piloto_zero.py --sim      (sem perguntar)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
if str(_RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(_RAIZ / "src"))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession, async_sessionmaker, create_async_engine,
)

from agente_ia_edu.db.models import ActivityAssignment  # noqa: E402
from agente_ia_edu.db.models.assessments import (  # noqa: E402
    ActivityAnswer, ActivityAttempt, ActivityResult, ActivityResultItem,
    DomainContentMastery,
)
from agente_ia_edu.db.models.material_progress import MaterialProgress  # noqa: E402

ALUNO_ID = "aluno_teste_a"
TURMA = "PILOTO_3A"


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
    u = vals.get("POSTGRES_USER", "agenteedu")
    p = vals.get("POSTGRES_PASSWORD", "agenteedu_dev")
    d = vals.get("POSTGRES_DB", "agente_ia_edu")
    return f"postgresql+psycopg://{u}:{p}@localhost:5433/{d}"


async def limpar(s: AsyncSession, *, recriar_atividade: bool = False) -> dict:
    contagem = {}

    # 1. resultados e seus itens (os itens primeiro: FK)
    resultados = [r[0] for r in (await s.execute(
        select(ActivityResult.id).where(
            ActivityResult.student_external_id == ALUNO_ID))).all()]
    if resultados:
        r = await s.execute(delete(ActivityResultItem).where(
            ActivityResultItem.result_id.in_(resultados)))
        contagem["itens de resultado"] = r.rowcount or 0
        r = await s.execute(delete(ActivityResult).where(
            ActivityResult.id.in_(resultados)))
        contagem["resultados"] = r.rowcount or 0

    # 2. tentativas e respostas
    tentativas = [r[0] for r in (await s.execute(
        select(ActivityAttempt.id).where(
            ActivityAttempt.student_external_id == ALUNO_ID))).all()]
    if tentativas:
        r = await s.execute(delete(ActivityAnswer).where(
            ActivityAnswer.attempt_id.in_(tentativas)))
        contagem["respostas"] = r.rowcount or 0
        r = await s.execute(delete(ActivityAttempt).where(
            ActivityAttempt.id.in_(tentativas)))
        contagem["tentativas"] = r.rowcount or 0

    # 3. o mapa de dominio dele
    r = await s.execute(delete(DomainContentMastery).where(
        DomainContentMastery.student_external_id == ALUNO_ID))
    contagem["dominio"] = r.rowcount or 0

    # 3b. o que ele JA LEU.
    #
    # `MaterialProgress` nao e evidencia de dominio - por isso ele sobreviveu
    # ao reset ate 2026-10-05, e com ele o cenario nao voltava ao zero: o
    # Assessor via "ja estudou" e pulava a explicacao, entao o caminho que
    # este script existe para repetir nao se repetia.
    #
    # O material em si NAO e tocado: ele e conteudo do Nucleo, como o acervo.
    r = await s.execute(delete(MaterialProgress).where(
        MaterialProgress.student_external_id == ALUNO_ID))
    contagem["leitura de material"] = r.rowcount or 0

    # 4. as atividades que ELE iniciou (praticas e microdiagnosticos).
    #    A atividade da TURMA fica: e da escola, nao dele.
    r = await s.execute(delete(ActivityAssignment).where(
        ActivityAssignment.target_type == "STUDENT",
        ActivityAssignment.target_id == ALUNO_ID))
    contagem["praticas e diagnosticos"] = r.rowcount or 0

    # 5. (opcional) a atividade da TURMA. Fora desta opcao ela nunca e tocada:
    #    e trabalho da escola, nao historico do aluno.
    if recriar_atividade:
        r = await s.execute(delete(ActivityAssignment).where(
            ActivityAssignment.target_type == "CLASS",
            ActivityAssignment.target_id == TURMA))
        contagem["atividade da turma"] = r.rowcount or 0

    await s.commit()
    return contagem


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", action="store_true", help="nao pedir confirmacao")
    ap.add_argument("--recriar-atividade", action="store_true",
                    help="apaga TAMBEM a atividade da turma, para que o seed a "
                         "monte de novo. So util quando o acervo mudou e a "
                         "atividade ficou menor do que poderia ser.")
    args = ap.parse_args()

    if not args.sim:
        print(f"Vai apagar o historico de {ALUNO_ID} (turma {TURMA}).")
        print("O Question Bank, o Diagnostic Bank, as outras escolas e a")
        print("atividade da escola NAO sao tocados.")
        if input("confirmar? [s/N] ").strip().lower() not in ("s", "sim"):
            print("cancelado.")
            return

    engine = create_async_engine(url())
    fabrica = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with fabrica() as s:
            contagem = await limpar(s, recriar_atividade=args.recriar_atividade)
    finally:
        await engine.dispose()

    print("\n--- RESET DO PILOTO ZERO ---")
    for k, v in contagem.items():
        print(f"  {k:28} {v}")
    print(f"\n  {ALUNO_ID} voltou a nao ter historico.")
    print("  Repita o cenario entrando no portal do aluno.")


if __name__ == "__main__":
    asyncio.run(main())
