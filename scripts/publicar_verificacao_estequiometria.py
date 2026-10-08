"""PUBLICA OS ITENS DE VERIFICACAO L0 - e so eles.

POR QUE ESTE SCRIPT EXISTE
===========================
`verificacao_estequiometria.ITENS` e a fonte de verdade pedagogica: o
enunciado, as alternativas, o gabarito, o erro que cada distrator nomeia e a
micro-habilidade que o item verifica. Mas o aluno nao e servido por um modulo
Python - ele e servido pelo Question Bank, pelo mesmo motor de selecao, pelo
mesmo player e pela mesma correcao deterministica.

Este script faz a ponte, exatamente como `publicar_sondagem_estequiometria`
faz para a sondagem. A diferenca cabe em uma linha: a finalidade declarada e
`VERIFICATION`, nao `PROBE`.

E ESSA LINHA E A QUE IMPORTA
=============================
`instrumento_de_sondagem.inelegibilidade` compara a finalidade pedida com a
declarada. Entao um item publicado aqui NAO pode ser escolhido como
instrumento de sondagem, e o item da sondagem NAO pode ser escolhido como
verificacao. Sem essa separacao, o diagnostico mediria com um item escrito
para ser respondido depois do ensino, e a verificacao reserviria o item que o
aluno ja errou - que, medido no banco em 2026-10-08, era literalmente o unico
item de MASSA_MOLAR que existia.

NAO DUPLICA O QUESTION BANK
============================
Nao cria banco paralelo, nao copia questao existente e nao inventa conteudo:
o texto vem de `ITENS`, e `conferir()` refaz os quatro numeros de cada item
(a massa molar e os tres erros nomeados) antes de qualquer escrita.

IDEMPOTENTE
============
A marca e `Question.metadata_["verificacao_key"]`. Reexecutar nao duplica
questao, nao duplica versao, nao duplica classificacao e nao mexe em gabarito
ja publicado. Ha teste.

Uso:
    .venv/bin/python scripts/publicar_verificacao_estequiometria.py
    .venv/bin/python scripts/publicar_verificacao_estequiometria.py --conferir
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
from agente_ia_edu.services.verificacao import (  # noqa: E402
    FINALIDADE_VERIFICACAO,
)
from agente_ia_edu.services.verificacao_estequiometria import (  # noqa: E402
    ITENS,
    conferir,
)

MARCA = "NUCLEO_VERIFICACAO_V1"
ANO = 2026
# As massas atomicas vem no enunciado, como na sondagem: a unica dificuldade
# e aplicar o indice, e e isso que o item verifica. Nao afeta a selecao -
# `PracticeSelectionPolicy.prefer_easier` e False para pratica -, mas dizer
# "media" sobre um item que da todos os dados seria inventar sobre ele.
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
    """{verificacao_key: question_id} do que ja esta no banco."""
    # Filtra em Python, nao em SQL, pelo mesmo motivo do publicador da
    # sondagem: `metadata_` e JSON em Postgres e TEXT em SQLite (onde os
    # testes rodam), e `astext` nao existe nos dois.
    linhas = (await s.execute(
        select(Question.id, Question.metadata_).where(
            Question.created_by_external_identity == MARCA))).all()
    return {(md or {}).get("verificacao_key"): qid
            for qid, md in linhas if (md or {}).get("verificacao_key")}


async def _contexto(s: AsyncSession):
    """O caderno e a revisao de gabarito da verificacao - criados uma vez.

    Caderno PROPRIO, e nao o da sondagem: o numero oficial e a ordem de
    desempate da selecao, e misturar as duas colecoes num caderno so faria a
    posicao de um item de verificacao depender de quantos itens de sondagem
    existem.
    """
    inst = await s.scalar(select(Institution).where(Institution.code == "NUCLEO"))
    if inst is None:
        inst = Institution(code="NUCLEO", name="Nucleo Edu 360")
        s.add(inst)
        await s.flush()
    exame = await s.scalar(select(Exam).where(
        Exam.code == "NUCLEO_VERIFICACAO"))
    if exame is None:
        exame = Exam(institution_id=inst.id, code="NUCLEO_VERIFICACAO",
                     name="Nucleo - Verificacao por micro-habilidade")
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
        ExamBooklet.code == "VERIFICACAO-ESTEQUIOMETRIA-V1"))
    if caderno is None:
        caderno = ExamBooklet(exam_application_id=edicao.id,
                              code="VERIFICACAO-ESTEQUIOMETRIA-V1",
                              color="UNICO")
        s.add(caderno)
        await s.flush()
        doc = SourceDocument(
            exam_application_id=edicao.id, exam_booklet_id=caderno.id,
            document_type="ANSWER_KEY",
            source_url="nucleo://verificacao/estequiometria/v1",
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
        # FAIL CLOSED. Um item de verificacao com gabarito errado nao falha
        # ruidosamente - ele registra uma lacuna que nao existe, e o aluno e
        # mandado reestudar o que ja sabia.
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
            metadata_={"bank": MARCA, "verificacao_key": item.key,
                       "diagnostic_skill": item.habilidade,
                       "purpose": FINALIDADE_VERIFICACAO,
                       "conferencia": item.conferencia,
                       # O ERRO QUE CADA DISTRATOR NOMEIA viaja com a
                       # questao. Nao e usado para servir o item - e para
                       # que, no dia em que alguem perguntar o que significa
                       # a alternativa A, a resposta esteja no banco e nao
                       # so no repositorio.
                       "erros": dict(item.erros)})
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
            difficulty=DIFICULDADE,
            # `reasoning_type` continua DIAGNOSTIC: o item mede. O que
            # distingue sondagem de verificacao e a FINALIDADE declarada no
            # metadata, e e ela que o contrato le - ver o cabecalho de
            # `instrumento_de_sondagem`.
            reasoning_type="DIAGNOSTIC",
            prerequisites=list(GRAFO.prerequisitos(item.habilidade)),
            keywords=[], competencies=[], skills=[item.habilidade],
            status="CLASSIFIED", source="human", lifecycle="ACTIVE",
            # Escritos a mao, no repositorio, com os quatro numeros REFEITOS
            # por conta antes de publicar. Nao vieram de modelo.
            provenance="HUMAN_VALIDATED", prompt_version="verificacao-v1",
            validated_by_external_identity="nucleo_edu_360",
            metadata_={"taxonomy_version": "curriculum-v2",
                       "primary_content_code": CONTEUDO,
                       "visual_dependency": False,
                       "purpose": FINALIDADE_VERIFICACAO,
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
            problemas = conferir()
            print(f"gabaritos: {'OK' if not problemas else 'PROBLEMA'}")
            for p in problemas:
                print(f"  ! {p}")
            existentes = await ja_publicados(s)
            print(f"itens de verificacao no banco: {len(existentes)} de "
                  f"{len(ITENS)}")
            for item in ITENS:
                marca = "ok" if item.key in existentes else "FALTA"
                print(f"  [{marca:<5}] {item.key:<26} {item.habilidade:<14} "
                      f"{item.fonte}")
        else:
            r = await publicar(s)
            print(f"criados: {len(r['criados'])}  "
                  f"ja existiam: {len(r['ja_existiam'])}")
            for k in r["criados"]:
                print(f"  + {k}")
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
