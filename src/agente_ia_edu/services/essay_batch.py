"""Envio em lote de redacoes fisicas pelo professor.

Spec: docs/superpowers/specs/2026-09-28-envio-lote-redacao-design.md

Este arquivo comeca com as funcoes PURAS (normalizacao, leitura do cabecalho,
match contra a turma) porque sao elas que decidem se uma folha de papel vira
automaticamente a redacao de alguem ou cai na fila do professor - a parte do
sistema que mais precisa ser deterministica e auditavel. A classe de servico
(banco, OCR, storage) vem depois delas.
"""

from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
import unicodedata
import uuid
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..providers.contracts import EssayTranscriptionProvider
from ..db.models import (
    Class, EssayBatchPage, EssayBatchUpload, EssayCorrection, PromptAssignment, Person, Student,
    StudentEnrollment, EssaySubmission,
)
from ..providers.errors import ProviderError
from ..db.models import EssaySubmissionPage
from .essay_answer_sheet import HEADER_REGION_FRACTION
from .essay_submission import EssaySubmissionService
from .essay_correction import EssayCorrectionService
from .essay_correction_key import essay_text_hash, normalize_essay_text
from .material_storage import MaterialStorage

logger = logging.getLogger(__name__)


def normalize_person_name(value: str | None) -> str:
    """Maiusculas, sem acento, espacos colapsados (spec s4.2).

    NFKD + descarte de combining marks e a mesma tecnica que o resto do
    projeto usa pra comparar texto acentuado - decompoe "Ç" em "C" + cedilha e
    joga fora a cedilha, em vez de depender de uma tabela de substituicao
    manual que sempre esquece alguma letra.
    """
    decomposed = unicodedata.normalize("NFKD", value or "")
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_marks.upper().split())


def normalize_cpf(value: str | None) -> str:
    """So os digitos - o aluno pode escrever com ponto, com traco ou sem nada."""
    return "".join(ch for ch in (value or "") if ch.isdigit())


# 11 digitos com ate 2 caracteres de pontuacao/espaco entre os grupos: pega
# "123.456.789-00", "123 456 789 00" e "12345678900" igualmente.
_CPF_PATTERN = re.compile(r"\d{3}\D{0,2}\d{3}\D{0,2}\d{3}\D{0,2}\d{2}")

# O rotulo impresso pela folha que o proprio sistema gera e "NOME COMPLETO DO
# PARTICIPANTE", mas o OCR pode comer parte dele, e um professor pode usar uma
# folha antiga onde esta so "NOME:" - as tres formas sao aceitas.
_NAME_LABEL_PATTERN = re.compile(r"^NOME(\s+COMPLETO)?(\s+DO\s+PARTICIPANTE)?\s*:?\s*")

_CPF_LABEL_PATTERN = re.compile(r"^CPF\b\s*:?\s*")

# Rotulos e cabecalhos impressos na propria folha, que nunca sao o nome de
# ninguem - descartados antes do fallback abaixo.
_PRINTED_LABELS = ("FOLHA DE REDACAO", "NOME", "CPF")

# Prompt de sistema pra OCR da regiao de CABECALHO - separado do prompt
# padrao de transcricao (providers/adapters/openai.py::TRANSCRIPTION_
# SYSTEM_PROMPT), que e pro CORPO da redacao e instrui explicitamente "nao
# transcreva... nome completo". Usar o prompt padrao tambem no cabecalho
# pede ao modelo exatamente o oposto do que a imagem tem (so nome e CPF,
# nenhum corpo de redacao) - isso nao so devolvia vazio, como confundia o
# modelo a ponto de RECUSAR a chamada inteira ("Desculpe, nao posso ajudar
# com isso", confirmado ao vivo 2026-10-05). Mora aqui (nao em openai.py)
# porque e o chamador - services/essay_batch.py - quem sabe que esta
# pedindo cabecalho, nao corpo; qualquer provider usa este texto via
# EssayPageTranscriptionRequest.system_prompt.
HEADER_TRANSCRIPTION_SYSTEM_PROMPT = (
    "Esta imagem e o cabecalho padrao de uma folha de prova escolar "
    "(redacao), com dois campos de preenchimento: NOME COMPLETO DO "
    "PARTICIPANTE e CPF, cada um em caixinhas de uma letra/digito por "
    "quadrado - o mesmo formato de um cartao-resposta de vestibular. "
    "Transcreva literalmente o que esta escrito em cada caixinha, letra "
    "por letra (ou digito por digito no CPF), na ordem em que aparecem. "
    "No campo NOME, uma ou mais caixinhas em branco entre um grupo de "
    "letras e outro marcam o espaco entre nome, nome do meio e sobrenome - "
    "represente cada um desses espacos em branco como um unico espaco no "
    "texto (nunca junte dois nomes diferentes numa so palavra). No campo "
    "CPF, junte todos os digitos sem espaco nenhum, numa sequencia continua. "
    "Nao corrija ortografia. Se uma caixinha estiver vazia DENTRO de uma "
    "mesma palavra (nao entre palavras) ou o caractere for genuinamente "
    "ilegivel, pule-a (nao invente uma letra). Devolva APENAS o texto "
    "transcrito dos dois campos, cada um em sua PROPRIA linha, separados "
    "por uma quebra de linha real (ex.:\n"
    "NOME COMPLETO DO PARTICIPANTE: MARIA SILVA\n"
    "CPF: 12345678900\n"
    "), sem comentarios, sem pedir desculpas, sem explicar limitacoes - e "
    "um formulario escolar padrao, nunca recuse transcrever um nome ou "
    "numero de CPF escrito nele."
)

# Teto do lote inteiro, somando todos os arquivos de um mesmo envio (spec s7).
# Decisao do usuario 2026-10-05: subido de 60 pra 200 pra caber uma escola
# inteira num envio so, nao so uma turma tipica. Sem risco de timeout de
# requisicao porque create_essay_batch devolve 202 na hora e o processamento
# roda em segundo plano (background_tasks.add_task) - o teto aqui e so pra
# nao deixar esse processamento crescer sem controle.
MAX_BATCH_PAGES = 200

# Teto POR ARQUIVO PDF do envio em lote - DELIBERADAMENTE maior que o
# EssaySubmissionService._MAX_PDF_PAGES (20) do envio INDIVIDUAL do aluno.
# Os dois nao podem mais ser o mesmo numero: o envio individual roda
# SINCRONO (risco real de timeout, ver o comentario de _MAX_PDF_PAGES), e o
# lote roda em background (sem esse risco) - por isso aceita um PDF unico
# bem maior, ate 200 paginas, sem precisar dividir em varios arquivos.
_MAX_PDF_PAGES_BATCH = 200

ALLOWED_BATCH_SUFFIXES = (".png", ".jpg", ".jpeg", ".pdf")
_PDF_SUFFIXES = (".pdf",)


def _strip_trailing_cpf(normalized_line: str) -> str:
    """Corta a linha no primeiro sinal de CPF (o rotulo ou os digitos).

    Confirmado como necessario: o OCR de uma REGIAO pequena (o cabecalho) muito
    frequentemente devolve tudo numa unica linha - "NOME COMPLETO DO
    PARTICIPANTE JOAO DA SILVA CPF 123.456.789-00". Sem este corte o nome
    extraido seria "JOAO DA SILVA CPF 123.456.789-00", que nao casa com aluno
    nenhum e manda toda folha bem preenchida pra fila manual.
    """
    cut = len(normalized_line)
    digits = _CPF_PATTERN.search(normalized_line)
    if digits is not None:
        cut = min(cut, digits.start())
    label = re.search(r"\bCPF\b", normalized_line)
    if label is not None:
        cut = min(cut, label.start())
    return normalized_line[:cut].strip()


def _looks_like_a_person_name(normalized_line: str) -> bool:
    """Duas ou mais palavras, so letras e espaco. Deliberadamente estreito: e
    usado apenas no fallback (quando o rotulo NOME nao foi lido), e um falso
    positivo aqui vira uma redacao atribuida ao aluno errado, enquanto um falso
    negativo so manda a pagina pra fila do professor."""
    if any(ch.isdigit() for ch in normalized_line):
        return False
    words = normalized_line.split()
    return len(words) >= 2 and all(word.isalpha() for word in words)


def parse_header_text(header_text: str | None) -> tuple[str | None, str | None]:
    """Extrai (nome normalizado, CPF so-digitos) do texto lido por OCR na
    REGIAO de cabecalho da folha.

    Estrategia, em ordem:
      1. A linha que comeca com o rotulo NOME - se tiver conteudo depois do
         rotulo, e esse o nome, cortado no primeiro sinal de CPF (ver
         _strip_trailing_cpf: o OCR do cabecalho costuma devolver tudo numa
         linha so); senao, a proxima linha que nao seja outro rotulo nem um CPF.
      2. Fallback (rotulo ilegivel): a linha mais longa que "parece nome de
         pessoa" e nao e um dos rotulos impressos na propria folha. O titulo da
         proposta tambem e impresso no cabecalho, mas ele quase sempre tem
         digito, artigo ou pontuacao - e, quando nao tem, o caminho 1 ja
         resolveu. Se o fallback errar, a pagina cai na fila manual, que e o
         comportamento seguro.

    Devolve o nome JA NORMALIZADO (e o que vai tanto pro match quanto pra
    coluna ocr_name_raw - ver o docstring de EssayBatchPage).
    """
    lines = [line.strip() for line in (header_text or "").splitlines()]
    normalized_lines = [normalize_person_name(line) for line in lines]
    normalized_lines = [line for line in normalized_lines if line]

    cpf_match = _CPF_PATTERN.search(header_text or "")
    cpf = normalize_cpf(cpf_match.group(0)) if cpf_match else None
    if cpf is not None and len(cpf) != 11:
        cpf = None

    name: str | None = None
    for index, line in enumerate(normalized_lines):
        label = _NAME_LABEL_PATTERN.match(line)
        if label is None:
            continue
        remainder = _strip_trailing_cpf(line[label.end():].strip())
        if remainder:
            name = remainder
        else:
            for candidate in normalized_lines[index + 1:]:
                if _CPF_LABEL_PATTERN.match(candidate) or _CPF_PATTERN.search(candidate):
                    continue
                if _NAME_LABEL_PATTERN.match(candidate):
                    continue
                name = _strip_trailing_cpf(candidate)
                break
        break

    if not name:
        candidates = [
            line for line in normalized_lines
            if _looks_like_a_person_name(line)
            and not any(line.startswith(label) for label in _PRINTED_LABELS)
        ]
        if candidates:
            name = max(candidates, key=len)

    return (name or None), cpf


def match_student(
    name_raw: str | None, roster: Sequence[tuple[uuid.UUID, str]]
) -> uuid.UUID | None:
    """O student_id sse EXATAMENTE UM aluno do roster tem o mesmo nome
    normalizado (spec s4.3). Zero ou dois-ou-mais devolvem None, e a pagina vai
    pra fila de resolucao manual - homonimos na mesma turma nunca sao
    desempatados automaticamente, nem pelo CPF (decisao do brainstorm: o CPF e
    pista pro professor, nunca criterio de match).

    ``roster`` e uma sequencia de (student_id, full_name) - o nome vem de
    Person.full_name, montado pela query de alunos ativos da turma.
    """
    target = normalize_person_name(name_raw)
    if not target:
        return None
    matches = [
        student_id for student_id, full_name in roster
        if normalize_person_name(full_name) == target
    ]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        return None

    # Fallback: o OCR em caixinhas as vezes perde o espaco entre nome, nome do
    # meio e sobrenome (caixinha de separacao mal detectada), devolvendo
    # "DANIELANTONIODASILVA" em vez de "DANIEL ANTONIO DA SILVA" - comparar
    # sem nenhum espaco recupera esse caso sem nunca desempatar homonimos
    # (ainda exige exatamente um candidato).
    target_glued = target.replace(" ", "")
    glued_matches = [
        student_id for student_id, full_name in roster
        if normalize_person_name(full_name).replace(" ", "") == target_glued
    ]
    return glued_matches[0] if len(glued_matches) == 1 else None


def text_from_ocr_tokens(tokens: list[dict] | None) -> str:
    """Reconstroi o texto a partir dos offsets start/end dos proprios tokens.

    Mesma logica de essay_submission._reconstruct_text_from_tokens (e pelo mesmo
    motivo documentado la: concatenar t["text"] cola palavras vizinhas sempre
    que o espaco entre dois tokens nao virou token proprio), porem sobre os
    DICTS que EssaySubmissionPage.ocr_tokens guarda, e nao sobre os dataclasses
    EssayOcrToken. Duplicado de proposito em vez de importado: aquela funcao e
    privada do modulo de submissao e tipada pros dataclasses.
    """
    if not tokens:
        return ""
    length = max(token["end"] for token in tokens)
    buffer = [" "] * length
    for token in tokens:
        for offset, character in enumerate(token["text"]):
            position = token["start"] + offset
            if position < length:
                buffer[position] = character
    return "".join(buffer).strip()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def consecutive_runs(pages: Sequence[EssayBatchPage]) -> list[list[EssayBatchPage]]:
    """Corridas MAXIMAIS de paginas com numero consecutivo e o MESMO
    matched_student_id (spec s4.4 reconciliada com s7).

    Uma pagina sem aluno casado quebra a corrida e nunca entra em nenhuma:
    resolver essa pagina e trabalho do professor. Uma segunda corrida do mesmo
    aluno, separada por paginas de outra pessoa, e um grupo PROPRIO - e o que
    torna possivel um aluno entregar duas redacoes no mesmo lote.

    ``pages`` precisa vir ordenada por page_number.
    """
    runs: list[list[EssayBatchPage]] = []
    current: list[EssayBatchPage] = []
    for page in pages:
        if page.matched_student_id is None:
            current = []
            continue
        if (
            current
            and current[-1].matched_student_id == page.matched_student_id
            and page.page_number == current[-1].page_number + 1
        ):
            current.append(page)
            continue
        current = [page]
        runs.append(current)
    return runs


# Module-level (nao de instancia, nao de sessao) de proposito: cada requisicao
# HTTP cria sua propria EssayBatchService, mas todas compartilham a MESMA cota
# de rate limit da OpenAI (uma unica API key pro processo inteiro). Confirmado
# ao vivo (2026-10-05): dois lotes de 21 paginas enviados em sequencia rapida
# processavam em PARALELO (cada um sua propria background task) e dobravam a
# carga de chamadas de visao, estourando o limite de tokens/min pra quase
# todo mundo - nao e sobre corretude de um lote sozinho, e sobre dois lotes
# nunca competirem pela mesma cota ao mesmo tempo. _PAGE_PACING_SECONDS so
# paceia chamadas DENTRO de um lote; este lock paceia lotes ENTRE si.
_BATCH_PROCESSING_LOCK = asyncio.Lock()


class EssayBatchService:
    """Processa um lote de folhas de redacao fisicas.

    ``correction_factory`` existe pro teste: em producao e
    ``EssayCorrectionService``, e o servico chama
    ``correction_factory(session).correct(submission_id)`` quando uma submissao
    do lote fica pronta. Injetavel pra que nenhum teste deste modulo precise de
    provedor de IA configurado.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: MaterialStorage | None = None,
        transcriber: EssayTranscriptionProvider | None = None,
        correction_factory: Callable[[AsyncSession], Any] | None = None,
    ) -> None:
        self.session = session
        self._storage = storage or MaterialStorage()
        self._transcriber = transcriber
        self._correction_factory = correction_factory

    def _submission_service(self) -> EssaySubmissionService:
        """Uma instancia de EssaySubmissionService usada SO pelo seu _ocr_page:
        as 3 tentativas, o piso de confianca media de 0.6 e a releitura de
        reconciliacao de tokens duvidosos ja estao resolvidos la (confirmados em
        producao ao longo de 2026-09), e reimplementar isso aqui seria copiar a
        parte mais delicada do sistema."""
        return EssaySubmissionService(self.session, transcriber=self._transcriber)

    @staticmethod
    def _crop_regions(
        image_path: Path, dest_dir: Path, *, header_fraction: float = HEADER_REGION_FRACTION,
    ) -> tuple[Path, Path]:
        """Separa uma imagem de pagina em (cabecalho, corpo) pela fracao fixa de
        altura que a folha gerada por services/essay_answer_sheet.py usa.

        pymupdf abre um PNG/JPG como documento de uma pagina so; as coordenadas
        da pagina saem em PONTOS (72dpi), nao em pixels, entao a matriz de
        escala abaixo e o que preserva a resolucao original do arquivo - sem ela
        um PNG de 827px de largura sairia recortado com 596px, e o OCR passaria
        a ler uma imagem PIOR do que a que recebemos.
        """
        try:
            import pymupdf as _mu
        except ImportError:
            import fitz as _mu  # type: ignore

        try:
            full = _mu.Pixmap(str(image_path))
        except Exception as exc:
            raise ValueError(f"{image_path.name} is not a readable image file") from exc

        doc = _mu.open(str(image_path))
        try:
            page = doc[0]
            rect = page.rect
            scale = full.width / rect.width
            matrix = _mu.Matrix(scale, scale)
            split_y = rect.y0 + rect.height * header_fraction
            header_path = dest_dir / f"{image_path.stem}_header.png"
            body_path = dest_dir / f"{image_path.stem}_body.png"
            page.get_pixmap(
                matrix=matrix, clip=_mu.Rect(rect.x0, rect.y0, rect.x1, split_y)
            ).save(str(header_path))
            page.get_pixmap(
                matrix=matrix, clip=_mu.Rect(rect.x0, split_y, rect.x1, rect.y1)
            ).save(str(body_path))
            return header_path, body_path
        finally:
            doc.close()

    async def _ocr_region(self, image_path: Path, *, system_prompt: str | None = None) -> str:
        """Texto de UMA regiao ja recortada. Usa uma EssaySubmissionPage
        TRANSITORIA, nunca adicionada a sessao: _ocr_page so escreve em
        ``page.ocr_tokens`` e nunca chama add()/flush(), entao o objeto serve
        aqui apenas como recipiente dos tokens e nada e persistido."""
        sink = EssaySubmissionPage(
            id=uuid.uuid4(), essay_submission_id=uuid.uuid4(),
            page_number=1, storage_uri=str(image_path),
        )
        await self._submission_service()._ocr_page(
            sink, image_path, system_prompt=system_prompt
        )
        return text_from_ocr_tokens(sink.ocr_tokens)

    async def read_page_regions(
        self, image_path: Path, *, extracted_pdf_text: str | None = None
    ) -> tuple[str | None, str | None, str]:
        """(nome normalizado, CPF so-digitos, texto do corpo) de uma pagina.

        Uma falha de OCR no CABECALHO nao e fatal: a pagina vai pra fila de
        resolucao manual e o professor identifica o aluno olhando a imagem, mas
        o texto que o aluno escreveu continua aproveitavel. Uma falha no CORPO
        propaga (ProviderError), porque sem texto nao ha redacao nenhuma pra
        corrigir - quem chama trata isso marcando a pagina NEEDS_REVIEW.

        ``extracted_pdf_text`` (de EssayBatchPage.extracted_pdf_text, capturada
        na intake - ver _expand_to_page_images): quando substancial (mesmo
        teto de EssaySubmissionService._MIN_EXTRACTED_TEXT_CHARS que o envio
        individual ja usa pra essa mesma decisao), o CORPO usa esse texto
        direto, SEM chamada de visao nenhuma - mais barato e mais preciso que
        OCR pra texto que ja nasceu digital. So existe quando o arquivo
        enviado era um PDF com camada de texto real (nunca existe em foto/
        scan de papel fisico - decisao do usuario, 2026-10-05, revertendo a
        decisao original de 2026-09-28 de nunca usar essa camada aqui).

        O CABECALHO continua SEMPRE por visao, mesmo com texto extraido
        disponivel: o nome vem em caixinhas de uma letra por vez, e a camada
        de texto bruta devolve isso uma letra por linha, sem separacao de
        palavra - reconstruir o nome dai pra bater com o aluno seria fragil
        demais pra confiar.
        """
        use_extracted_body = (
            extracted_pdf_text is not None
            and len(extracted_pdf_text.strip())
            >= EssaySubmissionService._MIN_EXTRACTED_TEXT_CHARS
        )
        scratch_dir = Path(tempfile.mkdtemp(prefix="r4_batch_regions_"))
        try:
            header_path, body_path = await asyncio.to_thread(
                self._crop_regions, image_path, scratch_dir
            )
            try:
                header_text = await self._ocr_region(
                    header_path, system_prompt=HEADER_TRANSCRIPTION_SYSTEM_PROMPT
                )
            except ProviderError as exc:
                logger.warning(
                    "OCR do cabecalho falhou para %s, pagina ira para revisao manual: %s",
                    image_path, exc,
                )
                header_text = ""
            if use_extracted_body:
                body_text = extracted_pdf_text
            else:
                body_text = await self._ocr_region(body_path)
            name, cpf = parse_header_text(header_text)
            return name, cpf, body_text.strip()
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)

    @classmethod
    def _expand_to_page_images(
        cls, source_paths: Sequence[Path]
    ) -> list[tuple[Path, str | None]]:
        """Uma lista plana de (imagem da pagina, texto extraido do PDF ou
        None), na ordem dos arquivos enviados: um PDF vira N paginas (via o
        mesmo _split_pdf_pages que a submissao individual usa, mas com o
        teto PROPRIO do lote - _MAX_PDF_PAGES_BATCH, maior que o do envio
        individual), uma imagem (PNG/JPG) continua sendo uma pagina so, sem
        texto nenhum (nunca ha camada de texto numa foto). Roda fora do
        event loop no chamador - rasterizar PDF e CPU-bound.

        O texto extraido PRECISA sobreviver ate aqui e ser persistido pelo
        chamador (create_batch): o PDF original e apagado pela rota logo
        depois (limpeza do tmp_dir, spec s7), e e so no processamento em
        segundo plano (process_batch, minutos depois) que esse texto e
        realmente usado - ver read_page_regions."""
        if not source_paths:
            raise ValueError("Envie ao menos um arquivo de redacao.")
        pages: list[tuple[Path, str | None]] = []
        for source_path in source_paths:
            suffix = source_path.suffix.lower()
            if suffix not in ALLOWED_BATCH_SUFFIXES:
                raise ValueError(f"Formato de arquivo nao suportado: {suffix!r}")
            if suffix in _PDF_SUFFIXES:
                # Levanta ValueError com a mensagem do proprio limite quando o
                # PDF passa de _MAX_PDF_PAGES_BATCH (teto do lote, nao o do
                # envio individual do aluno).
                split = EssaySubmissionService._split_pdf_pages(
                    source_path, max_pages=_MAX_PDF_PAGES_BATCH
                )
                pages.extend(split)
            else:
                pages.append((source_path, None))
        if len(pages) > MAX_BATCH_PAGES:
            raise ValueError(
                f"Este envio tem {len(pages)} paginas, acima do limite de "
                f"{MAX_BATCH_PAGES} por lote. Divida em mais de um envio."
            )
        return pages

    async def _assignment_for_class_or_raise(
        self, *, school_id: uuid.UUID, essay_prompt_id: uuid.UUID, class_id: uuid.UUID
    ) -> PromptAssignment:
        assignment = await self.session.scalar(
            select(PromptAssignment).where(
                PromptAssignment.school_id == school_id,
                PromptAssignment.essay_prompt_id == essay_prompt_id,
                PromptAssignment.class_id == class_id,
            )
        )
        if assignment is None:
            raise ValueError(
                "Esta proposta nao esta atribuida a esta turma - atribua a proposta "
                "a turma antes de enviar as redacoes em lote."
            )
        return assignment

    async def create_batch(
        self,
        *,
        school_id: uuid.UUID,
        essay_prompt_id: uuid.UUID,
        class_id: uuid.UUID | None = None,
        grade_level_id: uuid.UUID | None = None,
        uploaded_by_external_identity: str,
        source_paths: list[Path],
    ) -> dict:
        """Cria o lote em PROCESSING com TODAS as suas paginas ja gravadas em
        MaterialStorage, e devolve os campos como dict simples.

        class_id e grade_level_id sao mutuamente exclusivos - nenhum dos
        dois (escola inteira), so class_id (turma unica, com checagem
        antecipada de atribuicao como sempre) ou so grade_level_id (serie
        inteira, sem checagem antecipada: cada corrida resolve a
        atribuicao pela turma REAL do aluno matched via
        _assignment_for_student, que ja trata gracilmente um aluno sem
        atribuicao valida marcando a pagina NEEDS_REVIEW sem derrubar o
        lote inteiro - ver _materialize_run).

        Os limites sao checados ANTES de qualquer escrita, pra que um envio
        recusado nao deixe meio lote no banco. total_pages e fixado aqui (os
        PDFs ja vem separados em paginas), entao o progresso do processamento
        e sempre legivel como paginas_com_status_final/total_pages.

        Devolve dict e nao o objeto ORM pelo motivo de sempre neste projeto: a
        rota commita logo em seguida e expire_on_commit=True faria o proximo
        acesso a um atributo do objeto virar MissingGreenlet.
        """
        if class_id is not None and grade_level_id is not None:
            raise ValueError(
                "Escolha turma ou serie, nunca as duas - ou nenhuma para a escola inteira."
            )
        if class_id is not None:
            await self._assignment_for_class_or_raise(
                school_id=school_id, essay_prompt_id=essay_prompt_id, class_id=class_id
            )
        pages = await asyncio.to_thread(self._expand_to_page_images, source_paths)

        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=school_id, essay_prompt_id=essay_prompt_id,
            class_id=class_id, grade_level_id=grade_level_id,
            uploaded_by_external_identity=uploaded_by_external_identity,
            status="PROCESSING", total_pages=len(pages),
        )
        self.session.add(batch)
        await self.session.flush()

        for page_number, (image_path, extracted_text) in enumerate(pages, start=1):
            managed_path, _digest = await asyncio.to_thread(self._storage.store, image_path)
            self.session.add(EssayBatchPage(
                id=uuid.uuid4(), batch_id=batch.id, page_number=page_number,
                storage_uri=str(managed_path), extracted_pdf_text=extracted_text,
                status="NEEDS_REVIEW",
            ))
        await self.session.flush()

        return {
            "id": batch.id, "school_id": batch.school_id,
            "essay_prompt_id": batch.essay_prompt_id, "class_id": batch.class_id,
            "grade_level_id": batch.grade_level_id,
            "status": batch.status, "total_pages": batch.total_pages,
        }

    async def class_roster(
        self, *, school_id: uuid.UUID, class_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, str, str | None]]:
        """(student_id, full_name, document_number) de cada aluno com matricula
        ACTIVE nesta turma. Mesmo JOIN que essay_teacher_dashboard.py:198-203 ja
        usa pra montar a lista de alunos de uma turma, acrescido do
        document_number (o CPF que o professor ve como pista na tela de
        resolucao manual)."""
        rows = (await self.session.execute(
            select(StudentEnrollment.student_id, Person.full_name, Person.document_number)
            .join(Student, Student.id == StudentEnrollment.student_id)
            .join(Person, Person.id == Student.person_id)
            .where(
                StudentEnrollment.school_id == school_id,
                StudentEnrollment.class_id == class_id,
                StudentEnrollment.status == "ACTIVE",
            )
            .order_by(Person.full_name)
        )).all()
        return [(student_id, full_name, document_number) for student_id, full_name, document_number in rows]

    async def roster_for_batch(
        self, batch: EssayBatchUpload
    ) -> list[tuple[uuid.UUID, str, str | None]]:
        """Roster de alunos ativos no escopo do lote: a turma unica (delega
        pra class_roster, sem duplicar a query), todas as turmas da serie
        (grade_level_id), ou a escola inteira (nenhum dos dois) - mesma
        forma de retorno de class_roster em qualquer um dos 3 casos."""
        if batch.class_id is not None:
            return await self.class_roster(school_id=batch.school_id, class_id=batch.class_id)

        condicoes = [
            StudentEnrollment.school_id == batch.school_id,
            StudentEnrollment.status == "ACTIVE",
        ]
        if batch.grade_level_id is not None:
            condicoes.append(
                StudentEnrollment.class_id.in_(
                    select(Class.id).where(
                        Class.school_id == batch.school_id,
                        Class.grade_level_id == batch.grade_level_id,
                    )
                )
            )
        rows = (await self.session.execute(
            select(StudentEnrollment.student_id, Person.full_name, Person.document_number)
            .join(Student, Student.id == StudentEnrollment.student_id)
            .join(Person, Person.id == Student.person_id)
            .where(*condicoes)
            .order_by(Person.full_name)
            .distinct()
        )).all()
        return [
            (student_id, full_name, document_number)
            for student_id, full_name, document_number in rows
        ]

    async def _assignment_for_student(
        self, *, school_id: uuid.UUID, essay_prompt_id: uuid.UUID, student_id: uuid.UUID
    ) -> PromptAssignment:
        """A atribuicao desta proposta ao aluno: por turma ATIVA dele
        (ramo 1, prioridade - igual sempre foi) OU diretamente a ele
        (ramo 2, novo - R2 passou a permitir atribuir uma proposta a um
        aluno especifico, independente da turma dele ter sido atribuida).

        Nao e simplesmente a turma do lote: a spec s7 decide que a turma da
        submissao final vem do ALUNO, nao do class_id escolhido no upload -
        entao um aluno resolvido manualmente que esteja em outra turma recebe a
        atribuicao da turma DELE. Pro caminho automatico isso cai naturalmente
        na atribuicao do proprio lote, ja que o match so olha alunos daquela
        turma (ou da serie/escola, nos escopos mais amplos).
        """
        assignment = await self.session.scalar(
            select(PromptAssignment)
            .join(
                StudentEnrollment,
                StudentEnrollment.class_id == PromptAssignment.class_id,
            )
            .where(
                PromptAssignment.school_id == school_id,
                PromptAssignment.essay_prompt_id == essay_prompt_id,
                StudentEnrollment.student_id == student_id,
                StudentEnrollment.status == "ACTIVE",
            )
            .order_by(PromptAssignment.created_at)
        )
        if assignment is not None:
            return assignment

        assignment = await self.session.scalar(
            select(PromptAssignment).where(
                PromptAssignment.school_id == school_id,
                PromptAssignment.essay_prompt_id == essay_prompt_id,
                PromptAssignment.student_id == student_id,
            )
        )
        if assignment is None:
            raise ValueError(
                "Esta proposta nao esta atribuida a nenhuma turma ativa nem "
                "diretamente a este aluno."
            )
        return assignment

    async def _materialize_run(
        self, batch: EssayBatchUpload, run: list[EssayBatchPage]
    ) -> uuid.UUID | None:
        """Transforma UMA corrida em (ou atualiza) uma EssaySubmission e devolve
        o id dela quando ela precisa ser corrigida; None quando a corrida nao
        produz redacao nenhuma.

        A submissao nasce SUBMITTED com canonical_text ja preenchido e
        anchor_mode TEXT_OFFSET - o mesmo formato atomico de
        EssaySubmissionService.start_typed_submission, e NAO o de
        start_photo_submission (que depende de EssaySubmissionPage e de um
        reviewed_text humano que este fluxo deliberadamente nao tem).
        """
        text = "\n\n".join(
            (page.ocr_body_text or "").strip()
            for page in run
            if (page.ocr_body_text or "").strip()
        )
        if not text:
            # Spec s4.5: nome batido mas nenhum texto reconhecido nunca vira uma
            # redacao vazia - vai pro professor resolver olhando a imagem.
            for page in run:
                page.status = "NEEDS_REVIEW"
                page.essay_submission_id = None
                page.matched_student_id = None
            return None

        student_id = run[0].matched_student_id
        try:
            assignment = await self._assignment_for_student(
                school_id=batch.school_id,
                essay_prompt_id=batch.essay_prompt_id,
                student_id=student_id,
            )
        except ValueError as exc:
            logger.warning(
                "corrida do aluno %s no lote %s sem atribuicao valida: %s",
                student_id, batch.id, exc,
            )
            for page in run:
                page.status = "NEEDS_REVIEW"
                page.essay_submission_id = None
                page.matched_student_id = None
            return None

        existing_id = next(
            (page.essay_submission_id for page in run if page.essay_submission_id is not None),
            None,
        )
        if existing_id is not None:
            submission = await self.session.get(EssaySubmission, existing_id)
            new_hash = essay_text_hash(text)
            if new_hash != submission.normalized_text_hash:
                # O texto canonico mudou de verdade (uma pagina resolvida
                # manualmente se juntou a corrida DEPOIS que a IA ja tinha
                # corrigido a submissao com o texto antigo/incompleto -
                # Problema 1 CRITICAL do fix-round-1-brief.md). A correcao
                # antiga aponta pra um texto que nao existe mais (nota e
                # ancoras de anotacao errados), entao ela precisa ser
                # apagada AQUI, antes de run_corrections ser disparado de
                # novo - assim EssayCorrectionService.correct() (que e
                # idempotente por design: existe correcao -> devolve ela sem
                # chamar a IA) encontra a submissao SEM correcao valida e
                # roda a IA de verdade sobre o texto completo e atualizado.
                # Nunca mexe na idempotencia de correct() em si - so garante
                # que a submissao deixa de "ja ter" uma correcao quando o
                # texto dela mudou.
                await self.session.execute(
                    delete(EssayCorrection).where(
                        EssayCorrection.essay_submission_id == submission.id
                    )
                )
            submission.canonical_text = normalize_essay_text(text)
            submission.normalized_text_hash = new_hash
        else:
            submission = EssaySubmission(
                id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=batch.school_id,
                prompt_assignment_id=assignment.id, student_id=student_id,
                mode="PHOTO", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                canonical_text=normalize_essay_text(text),
                normalized_text_hash=essay_text_hash(text),
                submitted_at=_utcnow(),
            )
            self.session.add(submission)
            await self.session.flush()

        for page in run:
            if page.status != "RESOLVED_MANUAL":
                page.status = "MATCHED_AUTO"
            page.essay_submission_id = submission.id
        await self.session.flush()
        return submission.id

    async def materialize_batch(self, batch_id: uuid.UUID) -> list[uuid.UUID]:
        """Percorre TODAS as corridas do lote e devolve os ids das submissoes
        que precisam de correcao. Commita uma vez ao final - as paginas ja
        estavam commitadas individualmente pelo process_batch."""
        batch = await self.session.get(EssayBatchUpload, batch_id)
        if batch is None:
            raise ValueError(f"EssayBatchUpload not found: {batch_id}")
        pages = (await self.session.execute(
            select(EssayBatchPage)
            .where(EssayBatchPage.batch_id == batch_id)
            .order_by(EssayBatchPage.page_number)
        )).scalars().all()

        submission_ids: list[uuid.UUID] = []
        for run in consecutive_runs(pages):
            submission_id = await self._materialize_run(batch, run)
            if submission_id is not None:
                submission_ids.append(submission_id)
        await self.session.commit()
        return submission_ids

    async def run_corrections(self, submission_ids: Sequence[uuid.UUID]) -> None:
        """Dispara a correcao de cada submissao, uma por vez, cada uma com o seu
        proprio commit. Best-effort: uma correcao que estoure (provedor fora do
        ar, bug) nunca pode desfazer a submissao, que ja esta duravelmente
        SUBMITTED - o professor a ve como "Em correcao" e o retry manual que ja
        existe na fila de revisao resolve."""
        factory = self._correction_factory or EssayCorrectionService
        for submission_id in submission_ids:
            try:
                await factory(self.session).correct(submission_id)
                await self.session.commit()
            except Exception:
                await self.session.rollback()
                logger.exception(
                    "correcao do lote falhou para essay_submission_id=%s", submission_id
                )

    async def resolve_page(
        self, *, batch_id: uuid.UUID, page_id: uuid.UUID, student_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """O professor diz de quem e esta pagina.

        Depois de atribuir o aluno, a corrida que CONTEM esta pagina e
        re-materializada inteira - e assim que uma pagina resolvida ao lado de
        outra ja resolvida do mesmo aluno entra na MESMA submissao em vez de
        criar uma segunda (spec s2, rota de resolve), sem nenhuma regra de
        concatenacao propria: e exatamente a mesma que o caminho automatico usa.
        """
        batch = await self.session.get(EssayBatchUpload, batch_id)
        if batch is None:
            raise ValueError(f"EssayBatchUpload not found: {batch_id}")
        page = await self.session.get(EssayBatchPage, page_id)
        if page is None or page.batch_id != batch_id:
            raise ValueError("Esta pagina nao pertence a este lote.")
        if not (page.ocr_body_text or "").strip():
            raise ValueError(
                "Esta pagina nao tem texto reconhecido - nao da para criar uma redacao "
                "vazia. Envie uma foto mais nitida desta folha em um novo lote."
            )
        student = await self.session.get(Student, student_id)
        if student is None or student.school_id != batch.school_id:
            raise ValueError("Este aluno nao pertence a esta escola.")

        page.matched_student_id = student_id
        page.status = "RESOLVED_MANUAL"
        await self.session.flush()

        pages = (await self.session.execute(
            select(EssayBatchPage)
            .where(EssayBatchPage.batch_id == batch_id)
            .order_by(EssayBatchPage.page_number)
        )).scalars().all()
        run = next(
            (group for group in consecutive_runs(pages) if any(p.id == page_id for p in group)),
            [page],
        )
        submission_id = await self._materialize_run(batch, run)
        await self.session.commit()
        return [submission_id] if submission_id is not None else []

    async def _process_page(
        self, page: EssayBatchPage, roster: Sequence[tuple[uuid.UUID, str, str | None]],
        *, storage_uri: str, page_number: int, batch_id: uuid.UUID,
        extracted_pdf_text: str | None,
    ) -> None:
        """Le uma pagina e grava o que foi lido. Best-effort: qualquer falha
        (OCR esgotou as tentativas, imagem corrompida) deixa a pagina em
        NEEDS_REVIEW com os campos de leitura vazios, mesmo padrao por item que
        essay_proposal.create_assignments_bulk ja usa.

        storage_uri/page_number/batch_id/extracted_pdf_text vem do CHAMADOR,
        nunca lidos de ``page`` aqui: process_batch commita apos cada pagina,
        e expire_on_commit=True (producao) expira TODO objeto da sessao no
        commit anterior - ler um atributo de ``page`` a partir da segunda
        pagina em diante dispara um refresh sincrono que estoura com
        MissingGreenlet em contexto async (confirmado ao vivo 2026-10-05: o
        lote travava pra sempre em PROCESSING a partir da pagina 2). Atribuir
        um valor NOVO a ``page.status``/``page.ocr_*`` abaixo continua seguro
        - so LER que nao e."""
        try:
            name, cpf, body_text = await self.read_page_regions(
                Path(storage_uri), extracted_pdf_text=extracted_pdf_text
            )
        except (ProviderError, ValueError) as exc:
            logger.warning(
                "pagina %s do lote %s nao pode ser lida, vai para revisao manual: %s",
                page_number, batch_id, exc,
            )
            page.status = "NEEDS_REVIEW"
            return
        page.ocr_name_raw = name
        page.ocr_cpf_raw = cpf
        page.ocr_body_text = body_text
        page.matched_student_id = match_student(
            name, [(student_id, full_name) for student_id, full_name, _document in roster]
        )

    # Pausa entre paginas que de fato chamaram visao (nunca entre paginas que
    # usaram extracted_pdf_text, que nao custam chamada nenhuma) - decisao do
    # usuario 2026-10-05: evita rajada de chamadas de imagem contra o limite
    # de tokens/min da OpenAI (confirmado ao vivo: 21 paginas em sequencia
    # rapida estouravam o teto mesmo com cota alta). Nao e sobre corretude,
    # so sobre nao competir com a propria rajada - por isso fica fora de
    # _process_page (que so sabe o que falta nao e assunto dela).
    _PAGE_PACING_SECONDS = 2.0

    async def process_batch(self, batch_id: uuid.UUID) -> None:
        """Processa TODAS as paginas do lote, em sequencia (nunca em paralelo -
        um lote de 60 paginas dispararia 120+ chamadas simultaneas ao provedor
        de OCR), commitando depois de cada uma, pra que uma leitura de status
        feita no meio do caminho sempre reflita o progresso real (spec s5).

        O trecho que le as paginas (OCR de cabecalho/corpo) roda sob
        _BATCH_PROCESSING_LOCK: dois lotes nunca disparam rajadas de visao ao
        mesmo tempo, que era o cenario que de fato estourava o limite de
        tokens/min (confirmado ao vivo 2026-10-05, dois lotes de 21 paginas
        em sequencia rapida). run_corrections, no fim, roda FORA do lock de
        proposito - e sequencial por si so e muito mais lenta (20-30min pra
        um lote de 21), travar um segundo lote inteiro atras dela faria um
        segundo professor esperar o OCR dele sem necessidade.

        Ao fim marca o lote DONE. Nunca levanta por falha de uma pagina - ver
        _process_page.
        """
        async with _BATCH_PROCESSING_LOCK:
            batch = await self.session.get(EssayBatchUpload, batch_id)
            if batch is None:
                raise ValueError(f"EssayBatchUpload not found: {batch_id}")
            roster = await self.roster_for_batch(batch)
            pages = (await self.session.execute(
                select(EssayBatchPage)
                .where(EssayBatchPage.batch_id == batch_id)
                .order_by(EssayBatchPage.page_number)
            )).scalars().all()
            # Capturado ANTES do loop, enquanto nada ainda expirou - ver o
            # docstring de _process_page pro porque isto e necessario a partir
            # da segunda pagina.
            page_reads = [
                (p.storage_uri, p.page_number, p.batch_id, p.extracted_pdf_text) for p in pages
            ]

            for index, (page, (storage_uri, page_number, page_batch_id, extracted_pdf_text)) in enumerate(
                zip(pages, page_reads)
            ):
                if index > 0:
                    await asyncio.sleep(self._PAGE_PACING_SECONDS)
                await self._process_page(
                    page, roster,
                    storage_uri=storage_uri, page_number=page_number, batch_id=page_batch_id,
                    extracted_pdf_text=extracted_pdf_text,
                )
                await self.session.commit()

            # Agrupa as corridas e cria as submissoes SO depois que todas as
            # paginas foram lidas: uma corrida so e conhecida quando se sabe
            # quem esta na pagina seguinte.
            submission_ids = await self.materialize_batch(batch_id)

            batch = await self.session.get(EssayBatchUpload, batch_id)
            batch.status = "DONE"
        await self.session.commit()

        # Correcao por ultimo, com o lote ja DONE: e a parte lenta, e o
        # professor nao deve ficar vendo "processando" so por causa dela.
        await self.run_corrections(submission_ids)

    async def get_batch_status(self, batch_id: uuid.UUID) -> dict:
        """Estado agregado do lote pra tela de acompanhamento do professor.

        Devolve dicts puros (nunca objetos ORM) pelo motivo de sempre: a rota
        monta a resposta e commita, e expire_on_commit=True transformaria
        qualquer acesso posterior a um atributo em MissingGreenlet.

        ``available_students`` e a lista pro seletor da tela de resolucao
        manual: alunos ATIVOS da turma do lote que ainda NAO tem nenhuma pagina
        deste lote vinculada a uma submissao (spec s2).
        """
        batch = await self.session.get(EssayBatchUpload, batch_id)
        if batch is None:
            raise ValueError(f"EssayBatchUpload not found: {batch_id}")

        pages = (await self.session.execute(
            select(EssayBatchPage)
            .where(EssayBatchPage.batch_id == batch_id)
            .order_by(EssayBatchPage.page_number)
        )).scalars().all()

        # Problema 2 (IMPORTANT) do fix-round-1-brief.md: status de cada
        # pagina so sai de NEEDS_REVIEW dentro de _materialize_run, que so
        # roda depois que a ULTIMA pagina do lote inteiro foi processada
        # (process_batch). Enquanto o lote esta PROCESSING, uma pagina em
        # NEEDS_REVIEW pode estar em dois estados bem diferentes que o
        # status por si so nao distingue:
        #   - "ainda nem foi lida pelo OCR" (o mesmo estado em que
        #     create_batch a deixou: ocr_body_text IS NULL) - nao e uma
        #     pendencia real, so ainda nao chegou a vez dela;
        #   - "ja foi lida E ja casou com um aluno" (ocr_body_text
        #     preenchido, matched_student_id setado) - vai virar
        #     MATCHED_AUTO quando o lote terminar, tambem nao e uma
        #     pendencia real agora.
        # So a pagina PROCESSADA e SEM MATCH e uma pendencia de verdade.
        # Uma vez que o lote nao esta mais PROCESSING, todas as paginas ja
        # passaram por _process_page (inclusive a que falhou o OCR pra
        # sempre, com ocr_body_text ainda None) - nesse caso o antigo
        # comportamento (NEEDS_REVIEW = precisa de revisao) continua certo.
        batch_still_processing = batch.status == "PROCESSING"

        def _not_yet_processed(page: EssayBatchPage) -> bool:
            return (
                batch_still_processing
                and page.status == "NEEDS_REVIEW"
                and page.ocr_body_text is None
            )

        processed_pages = [page for page in pages if not _not_yet_processed(page)]
        needs_review = [
            page for page in processed_pages
            if page.status == "NEEDS_REVIEW" and page.matched_student_id is None
        ]
        taken_student_ids = {
            page.matched_student_id for page in pages
            if page.essay_submission_id is not None and page.matched_student_id is not None
        }
        roster = await self.roster_for_batch(batch)

        return {
            "id": batch.id,
            "school_id": batch.school_id,
            "essay_prompt_id": batch.essay_prompt_id,
            "class_id": batch.class_id,
            "grade_level_id": batch.grade_level_id,
            "status": batch.status,
            "total_pages": batch.total_pages,
            "matched_count": len(processed_pages) - len(needs_review),
            "needs_review_count": len(needs_review),
            "processed_count": len(processed_pages),
            "needs_review_pages": [
                {
                    "id": page.id,
                    "page_number": page.page_number,
                    "ocr_name_raw": page.ocr_name_raw,
                    "ocr_cpf_raw": page.ocr_cpf_raw,
                    # O seletor de aluno fica desabilitado quando nao ha texto:
                    # resolver essa pagina so criaria uma redacao vazia, que
                    # resolve_page recusa de qualquer forma.
                    "has_text": bool((page.ocr_body_text or "").strip()),
                    # Sempre None aqui: `needs_review` ja filtrou fora
                    # qualquer pagina com matched_student_id preenchido.
                    # Exposto pro frontend como defesa em profundidade.
                    "matched_student_id": page.matched_student_id,
                }
                for page in needs_review
            ],
            "available_students": [
                {
                    "student_id": student_id,
                    "full_name": full_name,
                    "document_number": document_number,
                }
                for student_id, full_name, document_number in roster
                if student_id not in taken_student_ids
            ],
        }


__all__ = [
    "ALLOWED_BATCH_SUFFIXES",
    "consecutive_runs",
    "EssayBatchService",
    "MAX_BATCH_PAGES",
    "match_student",
    "normalize_cpf",
    "normalize_person_name",
    "parse_header_text",
    "text_from_ocr_tokens",
]
