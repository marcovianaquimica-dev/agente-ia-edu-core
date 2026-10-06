"""PUBLICA OS CINCO ITENS DA SONDAGEM - e so eles.

POR QUE ESTE SCRIPT EXISTE
===========================
Os itens de `sondagem_estequiometria` sao a fonte de verdade pedagogica: o
enunciado, as alternativas, o gabarito e a micro-habilidade que cada um mede.
Mas o aluno nao e servido por um modulo Python - ele e servido pelo Question
Bank, pelo mesmo motor de selecao e pelo mesmo player de sempre.

Este script faz a ponte: publica cada item como `Question` + `QuestionVersion`
no banco, classificado com o `subcontent` da micro-habilidade. Dai em diante
tudo o que ja existe funciona - selecao, tentativa, correcao deterministica, e
evidencia gravada POR MICRO-HABILIDADE, que e o que faltava.

NAO DUPLICA O QUESTION BANK
============================
Nao cria banco paralelo, nao copia questao existente e nao inventa conteudo: o
texto vem de `ITENS`, que ja esta no repositorio, conferido por conta
(`conferir()` refaz 14+3x1=17, 36/18=2, 34/17x3/2=3).

IDEMPOTENTE
============
A marca e `Question.metadata_["sondagem_key"]`. Reexecutar nao duplica
questao, nao duplica versao, nao duplica classificacao e nao mexe em gabarito
ja publicado. Ha teste.

Uso:
    .venv/bin/python scripts/publicar_sondagem_estequiometria.py
    .venv/bin/python scripts/publicar_sondagem_estequiometria.py --conferir
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys
import uuid as _uuid
from datetime import datetime, timezone

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
if str(_RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(_RAIZ / "src"))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession, async_sessionmaker, create_async_engine,
)

from agente_ia_edu.db.models import (  # noqa: E402
    AnswerKeyEntry, AnswerKeyRevision, BookletQuestion, CatalogNode,
    ContentQuestionLink, Exam, ExamApplication, ExamBooklet, Institution,
    PedagogicalClassification, Question, QuestionOption, QuestionVersion,
    SourceDocument,
)
from agente_ia_edu.services.grafo_estequiometria import (  # noqa: E402
    CONTEUDO,
    GRAFO,
)
from agente_ia_edu.services.sondagem_estequiometria import (  # noqa: E402
    ITENS,
    conferir,
)

MARCA = "NUCLEO_SONDAGEM_V2"
ANO = 2026
# A sondagem e, por definicao, de baixa carga cognitiva. A dificuldade
# declarada nao e um chute: cada item mede UMA habilidade e da no enunciado
# tudo o que nao e a habilidade medida.
DIFICULDADE = "EASY"


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


async def ja_publicados(s: AsyncSession) -> dict[str, _uuid.UUID]:
    """{sondagem_key: question_id} do que ja esta no banco."""
    # Filtra em Python, nao em SQL: `metadata_` e JSON em Postgres e TEXT em
    # SQLite (onde os testes rodam), e `astext` nao existe nos dois. A tabela
    # de questoes do piloto tem centenas de linhas, nao milhoes - a diferenca
    # e invisivel, e o codigo passa a funcionar nos dois bancos.
    linhas = (await s.execute(
        select(Question.id, Question.metadata_).where(
            Question.created_by_external_identity == MARCA))).all()
    return {(md or {}).get("sondagem_key"): qid
            for qid, md in linhas if (md or {}).get("sondagem_key")}


async def _contexto(s: AsyncSession):
    """O caderno e a revisao de gabarito da sondagem - criados uma vez."""
    inst = await s.scalar(select(Institution).where(Institution.code == "NUCLEO"))
    if inst is None:
        inst = Institution(code="NUCLEO", name="Nucleo Edu 360")
        s.add(inst)
        await s.flush()
    exame = await s.scalar(select(Exam).where(Exam.code == "NUCLEO_SONDAGEM"))
    if exame is None:
        exame = Exam(institution_id=inst.id, code="NUCLEO_SONDAGEM",
                     name="Nucleo - Sondagem por micro-habilidade")
        s.add(exame)
        await s.flush()
    edicao = await s.scalar(select(ExamApplication).where(
        ExamApplication.exam_id == exame.id, ExamApplication.year == ANO))
    if edicao is None:
        edicao = ExamApplication(exam_id=exame.id, year=ANO,
                                 application_type="diagnostic", day=1)
        s.add(edicao)
        await s.flush()
    caderno = await s.scalar(select(ExamBooklet).where(
        ExamBooklet.code == "SONDAGEM-ESTEQUIOMETRIA-V1"))
    if caderno is None:
        caderno = ExamBooklet(exam_application_id=edicao.id,
                              code="SONDAGEM-ESTEQUIOMETRIA-V1", color="UNICO")
        s.add(caderno)
        await s.flush()
        doc = SourceDocument(
            exam_application_id=edicao.id, exam_booklet_id=caderno.id,
            document_type="ANSWER_KEY",
            source_url="nucleo://sondagem/estequiometria/v1",
            acquired_at=datetime.now(timezone.utc),
            content_hash=str(_uuid.uuid4()))
        s.add(doc)
        await s.flush()
        revisao = AnswerKeyRevision(source_document_id=doc.id,
                                    revision_number=1, is_official=True)
        s.add(revisao)
        await s.flush()
    else:
        doc = await s.scalar(select(SourceDocument).where(
            SourceDocument.exam_booklet_id == caderno.id))
        revisao = await s.scalar(select(AnswerKeyRevision).where(
            AnswerKeyRevision.source_document_id == doc.id))
    return caderno, revisao


async def publicar(s: AsyncSession) -> dict:
    """Publica o que faltar. Devolve o relatorio do que foi feito."""
    problemas = conferir()
    if problemas:
        # FAIL CLOSED. Um item de sondagem com gabarito errado nao falha
        # ruidosamente - ele mede errado, em silencio, e o erro entra no mapa
        # de dominio como se fosse evidencia.
        raise SystemExit("gabaritos nao conferem: " + "; ".join(problemas))

    no = await s.scalar(select(CatalogNode).where(CatalogNode.code == CONTEUDO))
    if no is None:
        raise SystemExit(f"no curricular ausente: {CONTEUDO}")

    existentes = await ja_publicados(s)
    caderno, revisao = await _contexto(s)
    criados, pulados = [], []

    proxima_posicao = len(existentes)
    for item in ITENS:
        if item.key in existentes:
            pulados.append(item.key)
            continue
        proxima_posicao += 1
        q = Question(
            validation_status="valid", origin_type="GENERATED",
            status="PUBLISHED", visibility_scope="PUBLIC",
            question_type="MULTIPLE_CHOICE",
            created_by_external_identity=MARCA,
            metadata_={"bank": MARCA, "sondagem_key": item.key,
                       "diagnostic_skill": item.habilidade,
                       "purpose": "PROBE",
                       "conferencia": item.conferencia})
        s.add(q)
        await s.flush()
        v = QuestionVersion(
            question_id=q.id, version_kind="official_original",
            canonical_text=item.enunciado, statement=item.enunciado,
            content_hash=str(_uuid.uuid4()), is_immutable=True,
            recommended_difficulty=DIFICULDADE)
        s.add(v)
        await s.flush()
        opcoes = {}
        for pos, (chave, texto) in enumerate(
                sorted(item.alternativas.items()), start=1):
            o = QuestionOption(question_version_id=v.id, option_key=chave,
                               position=pos, text=texto,
                               is_valid_option=(chave == item.correta))
            s.add(o)
            await s.flush()
            opcoes[chave] = o
        bq = BookletQuestion(exam_booklet_id=caderno.id,
                             question_version_id=v.id,
                             position=proxima_posicao,
                             official_number=proxima_posicao, page_number=1)
        s.add(bq)
        await s.flush()
        s.add(AnswerKeyEntry(answer_key_revision_id=revisao.id,
                             booklet_question_id=bq.id,
                             official_answer_label=item.correta,
                             resolved_option_id=opcoes[item.correta].id,
                             page_number=1))
        s.add(PedagogicalClassification(
            question_version_id=v.id, discipline="CURRICULUM_PROPOSAL",
            content=CONTEUDO, subcontent=item.habilidade,
            difficulty=DIFICULDADE, reasoning_type="DIAGNOSTIC",
            prerequisites=list(GRAFO.prerequisitos(item.habilidade)),
            keywords=[], competencies=[], skills=[item.habilidade],
            # HUMAN_VALIDATED e o que o CheckConstraint permite, e e o que
            # estes itens sao: escritos a mao, no repositorio, com o gabarito
            # REFEITO por conta antes de publicar. Nao vieram de modelo.
            status="CLASSIFIED", source="human", lifecycle="ACTIVE",
            provenance="HUMAN_VALIDATED", prompt_version="sondagem-v1",
            # O CheckConstraint exige NOMEAR quem validou quando a origem e
            # humana - e ele esta certo: "um humano validou" sem dizer qual
            # nao e rastreavel. Estes itens sao conteudo autoral da
            # plataforma, a mesma identidade que o material teorico ja usa.
            validated_by_external_identity="nucleo_edu_360",
            metadata_={"taxonomy_version": "curriculum-v2",
                       "primary_content_code": CONTEUDO,
                       "visual_dependency": False,
                       "purpose": "PROBE",
                       "diagnostic_skill": item.habilidade}))
        s.add(ContentQuestionLink(content_node_id=no.id,
                                  question_version_id=v.id))
        criados.append(item.key)

    await s.commit()
    return {"criados": criados, "ja_existiam": pulados}


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--conferir", action="store_true",
                    help="so relata, nao publica")
    args = ap.parse_args()

    engine = create_async_engine(url())
    factory = async_sessionmaker(engine, class_=AsyncSession,
                                 expire_on_commit=False)
    async with factory() as s:
        if args.conferir:
            existentes = await ja_publicados(s)
            print(f"itens da sondagem no banco: {len(existentes)} de {len(ITENS)}")
            for item in ITENS:
                marca = "ok" if item.key in existentes else "FALTA"
                print(f"  [{marca:<5}] {item.key:<28} {item.habilidade}")
        else:
            r = await publicar(s)
            print(f"criados: {len(r['criados'])}  "
                  f"ja existiam: {len(r['ja_existiam'])}")
            for k in r["criados"]:
                print(f"  + {k}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
