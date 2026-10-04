"""Piloto do Nucleo Diagnostic Bank: persiste os itens gerados e mede a
selecao REAL pela PracticeSelectionPolicy.

POR QUE UM BANCO DESCARTAVEL
=============================
O banco de desenvolvimento esta carimbado em `063_platform_material_target`,
revisao que nao existe na cadeia deste worktree (`063_embedding_activation`).
Sao linhagens divergentes - ver docs/diagnostic-bank-v1.md. Forcar upgrade ou
carimbar a mao seria mexer no banco do usuario com uma cadeia que nao e a
dele, entao o piloto roda num banco proprio, construido pela cadeia real.

Uso:
    .venv/bin/python scripts/piloto_diagnostic_bank.py --itens /tmp/diagnostic_v2.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import sys
import uuid as _uuid
from datetime import datetime, timezone

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
if str(_RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(_RAIZ / "src"))

import sqlalchemy as sa  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession, async_sessionmaker, create_async_engine,
)

from agente_ia_edu.db.models import (  # noqa: E402
    AnswerKeyEntry, AnswerKeyRevision, BookletQuestion, CatalogNode, Exam,
    ExamApplication, ExamBooklet, Institution, PedagogicalClassification,
    Question, QuestionOption, QuestionVersion, SourceDocument,
)
from agente_ia_edu.db.models.catalog import (  # noqa: E402
    CatalogNodePrerequisite, ContentQuestionLink,
)
from agente_ia_edu.services.adaptive_practice import PracticeSelectionPolicy  # noqa: E402
from agente_ia_edu.services.chemistry_balance import (  # noqa: E402
    FormulaInvalida, equacao_balanceada,
)
from agente_ia_edu.services.curriculum_taxonomy import (  # noqa: E402
    CurriculumTaxonomyService,
)
from agente_ia_edu.services.diagnostic_bank import (  # noqa: E402
    BANK_TAG, CONTENT_CODE, LETRAS, ORIGIN_TYPE, ItemCandidato,
    redistribuir_gabaritos, vies_posicional,
)
from agente_ia_edu.services.pedagogical_analysis import (  # noqa: E402
    PerformanceThresholdPolicy,
)
from agente_ia_edu.services.question_bank import (  # noqa: E402
    QuestionBankFilters, QuestionBankService,
)

BANCO = "agente_ia_edu_diagnostic_piloto"
ESTEQ = "CHEMISTRY-PHYSICAL-STOICHIOMETRY"


def _url(nome: str) -> str:
    u = os.getenv("POSTGRES_USER", "agenteedu")
    p = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    return f"postgresql+psycopg://{u}:{p}@localhost:5433/{nome}"


def _admin(sql: str):
    motor = sa.create_engine(_url("postgres"), isolation_level="AUTOCOMMIT")
    try:
        with motor.connect() as c:
            return c.execute(sa.text(sql))
    finally:
        motor.dispose()


async def _semear_catalogo(url: str) -> None:
    engine = create_async_engine(url)
    try:
        fabrica = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with fabrica() as s:
            await CurriculumTaxonomyService(s).seed_reference_fixture()
    finally:
        await engine.dispose()


async def _persistir(s: AsyncSession, itens: list[ItemCandidato]) -> list[str]:
    # O seed de referencia do projeto nao cobre toda a arvore de Quimica, e
    # este piloto roda num banco recem-criado. Monta o que faltar.
    disc = await s.scalar(select(CatalogNode).where(CatalogNode.code == "CHEMISTRY"))
    if disc is None:
        disc = CatalogNode(code="CHEMISTRY", name="Quimica",
                           node_type="DISCIPLINE", position=0, active=True)
        s.add(disc); await s.flush()
        disc.root_id = disc.id; await s.flush()
    geral = await s.scalar(select(CatalogNode).where(
        CatalogNode.code == "CHEMISTRY-GENERAL"))
    if geral is None:
        geral = CatalogNode(code="CHEMISTRY-GENERAL", name="Quimica Geral",
                            node_type="AREA", position=1, parent_id=disc.id,
                            root_id=disc.root_id or disc.id, active=True)
        s.add(geral); await s.flush()
    fisico = await s.scalar(select(CatalogNode).where(
        CatalogNode.code == "CHEMISTRY-PHYSICAL"))
    if fisico is None:
        fisico = CatalogNode(code="CHEMISTRY-PHYSICAL", name="Fisico-Quimica",
                             node_type="AREA", position=2, parent_id=disc.id,
                             root_id=disc.root_id or disc.id, active=True)
        s.add(fisico); await s.flush()
    esteq = await s.scalar(select(CatalogNode).where(CatalogNode.code == ESTEQ))
    if esteq is None:
        esteq = CatalogNode(code=ESTEQ, name="Estequiometria e calculos quimicos",
                            node_type="CONTENT", position=1, parent_id=fisico.id,
                            root_id=disc.root_id or disc.id, active=True)
        s.add(esteq); await s.flush()
    balanc = await s.scalar(select(CatalogNode).where(
        CatalogNode.code == CONTENT_CODE))
    if balanc is None:
        balanc = CatalogNode(code=CONTENT_CODE,
                             name="Reacoes quimicas e balanceamento",
                             node_type="CONTENT", position=9,
                             parent_id=geral.id, root_id=geral.root_id,
                             active=True)
        s.add(balanc); await s.flush()
    if esteq is not None and not await s.scalar(select(CatalogNodePrerequisite).where(
            CatalogNodePrerequisite.content_node_id == esteq.id,
            CatalogNodePrerequisite.prerequisite_node_id == balanc.id)):
        s.add(CatalogNodePrerequisite(content_node_id=esteq.id,
                                      prerequisite_node_id=balanc.id))

    inst = Institution(code="NUCLEO", name="Nucleo Edu 360")
    s.add(inst); await s.flush()
    exame = Exam(institution_id=inst.id, code="NUCLEO_DIAGNOSTIC",
                 name="Nucleo Diagnostic Bank")
    s.add(exame); await s.flush()
    edicao = ExamApplication(exam_id=exame.id, year=2026,
                             application_type="diagnostic", day=1)
    s.add(edicao); await s.flush()
    caderno = ExamBooklet(exam_application_id=edicao.id,
                          code="BALANCEAMENTO-V1", color="UNICO")
    s.add(caderno); await s.flush()
    doc = SourceDocument(exam_application_id=edicao.id, exam_booklet_id=caderno.id,
                         document_type="ANSWER_KEY",
                         source_url="nucleo://diagnostic-bank/balanceamento/v1",
                         acquired_at=datetime.now(timezone.utc),
                         content_hash=str(_uuid.uuid4()))
    s.add(doc); await s.flush()
    revisao = AnswerKeyRevision(source_document_id=doc.id, revision_number=1,
                                is_official=True)
    s.add(revisao); await s.flush()

    ids = []
    for numero, item in enumerate(itens, start=1):
        q = Question(validation_status="valid", origin_type=ORIGIN_TYPE,
                     status="PUBLISHED", visibility_scope="PUBLIC",
                     question_type="MULTIPLE_CHOICE",
                     created_by_external_identity=BANK_TAG,
                     metadata_={"bank": BANK_TAG,
                                "diagnostic_skill": item.diagnostic_skill,
                                "diagnostic_objective": item.diagnostic_objective,
                                "generation": {"actor_type": "AI",
                                               "version": item.generator_version},
                                "verification": {"actor_type": "AI"},
                                "equacoes": item.equacoes})
        s.add(q); await s.flush()
        v = QuestionVersion(question_id=q.id, version_kind="official_original",
                            canonical_text=item.stem, statement=item.stem,
                            content_hash=str(_uuid.uuid4()), is_immutable=True,
                            recommended_difficulty=item.difficulty)
        s.add(v); await s.flush()
        opcoes = {}
        for pos, letra in enumerate(LETRAS, start=1):
            o = QuestionOption(question_version_id=v.id, option_key=letra,
                               position=pos, text=item.options[letra],
                               is_valid_option=(letra == item.correct_answer))
            s.add(o); await s.flush(); opcoes[letra] = o
        bq = BookletQuestion(exam_booklet_id=caderno.id, question_version_id=v.id,
                             position=numero, official_number=numero, page_number=1)
        s.add(bq); await s.flush()
        s.add(AnswerKeyEntry(answer_key_revision_id=revisao.id,
                             booklet_question_id=bq.id,
                             official_answer_label=item.correct_answer,
                             resolved_option_id=opcoes[item.correct_answer].id,
                             page_number=1))
        s.add(PedagogicalClassification(
            question_version_id=v.id, discipline="CURRICULUM_PROPOSAL",
            content=CONTENT_CODE, subcontent=item.diagnostic_skill,
            difficulty=item.difficulty, reasoning_type="DIAGNOSTIC",
            prerequisites=[], keywords=[], competencies=[],
            skills=[item.diagnostic_skill], status="CLASSIFIED", source="ai",
            lifecycle="ACTIVE", provenance="AI_VERIFIED",
            model_version=item.generator_version, prompt_version="v2",
            metadata_={"taxonomy_version": "curriculum-v2",
                       "primary_content_code": CONTENT_CODE,
                       "visual_dependency": False,
                       "diagnostic_skill": item.diagnostic_skill}))
        s.add(ContentQuestionLink(content_node_id=balanc.id,
                                  question_version_id=v.id))
        ids.append(str(v.id))
    await s.commit()
    return ids


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--itens", default="/tmp/diagnostic_v2.json")
    args = ap.parse_args()

    bruto = json.load(open(args.itens, encoding="utf-8"))
    aprovados = [ItemCandidato(**r["item"]) for r in bruto
                 if r["decisao"]["status"] == "AI_VERIFIED"]
    print(f"itens AI_VERIFIED no arquivo: {len(aprovados)}")
    print(f"vies ANTES: "
          f"{ {k: f'{v:.0%}' for k, v in vies_posicional(aprovados).items()} }")
    itens = redistribuir_gabaritos(aprovados)
    print(f"vies DEPOIS: "
          f"{ {k: f'{v:.0%}' for k, v in vies_posicional(itens).items()} }")

    # a quimica da resposta correta, conferida por contagem
    ruins = []
    for i in itens:
        try:
            if not equacao_balanceada(i.options[i.correct_answer]):
                ruins.append(i.stem[:50])
        except FormulaInvalida:
            pass          # nem toda resposta correta e uma equacao
    print(f"respostas que SAO equacao e nao balanceiam: {len(ruins)}")

    _admin(f'DROP DATABASE IF EXISTS "{BANCO}"')
    _admin(f'CREATE DATABASE "{BANCO}"')
    url = _url(BANCO)
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "023_curriculum_taxonomy")
    await _semear_catalogo(url)
    command.upgrade(cfg, "head")
    print(f"\nbanco descartavel construido pela cadeia real: {BANCO}")

    engine = create_async_engine(url)
    fabrica = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with fabrica() as s:
            ids = await _persistir(s, itens)
            print(f"itens persistidos: {len(ids)}")

        async with fabrica() as s:
            bank = QuestionBankService(s)
            await bank._load_catalog()
            politica = PracticeSelectionPolicy.default()
            minimo = PerformanceThresholdPolicy.default().min_sample_size
            print(f"\n=== SELECAO REAL (PracticeSelectionPolicy) ===")
            print(f"minimo exigido pelo microdiagnostico: {minimo}")
            for code in (CONTENT_CODE, ESTEQ):
                page = await bank.list_questions(
                    QuestionBankFilters(content_code=code,
                                        classification_state="CLASSIFIED"),
                    page=1, page_size=200,
                    order_by="official_number", order_direction="asc")
                mantidas = [it for it in page.items
                            if not (politica.exclude_visual_dependency
                                    and it.has_visual_dependency)
                            and not (politica.exclude_protected and it.is_protected)]
                marca = "OK " if len(mantidas) >= minimo else "FALTA "
                print(f"  {marca}{code:<34} selecionaveis={len(mantidas)}")
    finally:
        await engine.dispose()
        _admin(f'DROP DATABASE IF EXISTS "{BANCO}"')
        print(f"\nbanco descartavel removido")


if __name__ == "__main__":
    asyncio.run(main())
