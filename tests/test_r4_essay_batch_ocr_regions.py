"""R4 lote - recorte da regiao de cabecalho e OCR separado de cabecalho/corpo.

O transcritor e falso (nenhuma chamada de IA nos testes): ele devolve tokens
diferentes conforme o nome do arquivo de imagem que recebe, que e exatamente o
que prova que o servico chamou o OCR DUAS vezes, uma por regiao, em vez de uma
so na pagina inteira.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from agente_ia_edu.providers.errors import ProviderError
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult
from agente_ia_edu.services.essay_answer_sheet import HEADER_REGION_FRACTION
from agente_ia_edu.services.essay_batch import EssayBatchService


def _tokens(text: str, confidence: float = 0.95):
    return (EssayOcrToken(text=text, confidence=confidence, start=0, end=len(text)),)


class FakeTranscriber:
    """Devolve o texto de cabecalho pra imagem cujo nome termina em _header e o
    texto de corpo pra que termina em _body."""

    def __init__(self, header_text: str, body_text: str, *, fail_on: str | None = None):
        self.header_text = header_text
        self.body_text = body_text
        self.fail_on = fail_on
        self.calls: list[str] = []

    async def transcribe_page(self, request):
        name = request.image_path.name
        self.calls.append(name)
        if self.fail_on and self.fail_on in name:
            raise ProviderError("transcricao indisponivel")
        text = self.header_text if "_header" in name else self.body_text
        return EssayPageTranscriptionResult(
            tokens=_tokens(text), provider="fake", model="fake-1"
        )


def _write_page_image(dest: Path, *, header_text: str, body_text: str) -> Path:
    """Uma imagem de pagina A4 sintetica: o texto de cabecalho no topo, o de
    corpo bem abaixo da fronteira de HEADER_REGION_FRACTION."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595.44, height=842.40)
    page.insert_text((50, 60), header_text, fontsize=11)
    page.insert_text((50, 500), body_text, fontsize=11)
    page.get_pixmap(dpi=150).save(str(dest))
    doc.close()
    return dest


class CropRegionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="r4_crop_"))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_splits_the_page_at_the_header_fraction_keeping_full_resolution(self):
        import pymupdf

        source = _write_page_image(
            self.tmp_dir / "page_1.png", header_text="NOME", body_text="corpo"
        )
        full = pymupdf.Pixmap(str(source))

        header_path, body_path = EssayBatchService._crop_regions(source, self.tmp_dir)

        header = pymupdf.Pixmap(str(header_path))
        body = pymupdf.Pixmap(str(body_path))
        # Mesma largura que o original: o recorte nao pode reduzir a resolucao,
        # senao o OCR le uma imagem pior do que a que recebemos.
        self.assertEqual(header.width, full.width)
        self.assertEqual(body.width, full.width)
        self.assertAlmostEqual(
            header.height, full.height * HEADER_REGION_FRACTION, delta=2
        )
        self.assertAlmostEqual(
            header.height + body.height, full.height, delta=2
        )

    def test_raises_value_error_for_an_unreadable_image(self):
        broken = self.tmp_dir / "broken.png"
        broken.write_bytes(b"nao sou uma imagem")
        with self.assertRaises(ValueError):
            EssayBatchService._crop_regions(broken, self.tmp_dir)

    def test_crop_regions_accepts_a_custom_header_fraction(self):
        """O recorte aceita uma fracao de cabecalho customizada."""
        import pymupdf

        source = _write_page_image(
            self.tmp_dir / "page_2.png", header_text="NOME", body_text="corpo"
        )
        full = pymupdf.Pixmap(str(source))

        # Com a fracao padrao, o header ocupa HEADER_REGION_FRACTION da altura
        header_default, _ = EssayBatchService._crop_regions(source, self.tmp_dir)
        header_default_img = pymupdf.Pixmap(str(header_default))

        # Com uma fracao maior (0.5), o header deve ser maior
        half_dir = self.tmp_dir / "half"
        half_dir.mkdir(exist_ok=True)
        header_half, _ = EssayBatchService._crop_regions(
            source, half_dir, header_fraction=0.5
        )
        header_half_img = pymupdf.Pixmap(str(header_half))

        # O header com 0.5 deve ser maior que o header com a fracao padrao
        self.assertGreater(header_half_img.height, header_default_img.height)
        # E deve ser aproximadamente metade da altura da imagem original
        self.assertAlmostEqual(header_half_img.height, full.height * 0.5, delta=2)


class ReadPageRegionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="r4_read_"))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def test_reads_name_cpf_and_body_with_two_separate_ocr_calls(self):
        source = _write_page_image(
            self.tmp_dir / "page_1.png", header_text="cabecalho", body_text="corpo"
        )
        transcriber = FakeTranscriber(
            header_text="NOME COMPLETO DO PARTICIPANTE Joao da Silva CPF 123.456.789-00",
            body_text="A mobilidade urbana no Brasil enfrenta desafios.",
        )
        service = EssayBatchService(None, transcriber=transcriber)

        name, cpf, body = await service.read_page_regions(source)

        self.assertEqual(name, "JOAO DA SILVA")
        self.assertEqual(cpf, "12345678900")
        self.assertEqual(body, "A mobilidade urbana no Brasil enfrenta desafios.")
        self.assertEqual(len(transcriber.calls), 2)
        self.assertTrue(any("_header" in call for call in transcriber.calls))
        self.assertTrue(any("_body" in call for call in transcriber.calls))

    async def test_unreadable_header_still_returns_the_body(self):
        source = _write_page_image(
            self.tmp_dir / "page_2.png", header_text="cabecalho", body_text="corpo"
        )
        transcriber = FakeTranscriber(
            header_text="", body_text="Texto do corpo assim mesmo.", fail_on="_header"
        )
        service = EssayBatchService(None, transcriber=transcriber)

        name, cpf, body = await service.read_page_regions(source)

        self.assertIsNone(name)
        self.assertIsNone(cpf)
        self.assertEqual(body, "Texto do corpo assim mesmo.")

    async def test_body_ocr_failure_propagates(self):
        source = _write_page_image(
            self.tmp_dir / "page_3.png", header_text="cabecalho", body_text="corpo"
        )
        transcriber = FakeTranscriber(header_text="NOME: Ana", body_text="", fail_on="_body")
        service = EssayBatchService(None, transcriber=transcriber)

        with self.assertRaises(ProviderError):
            await service.read_page_regions(source)

    async def test_substantial_extracted_pdf_text_skips_body_ocr_entirely(self):
        """Decisao do usuario 2026-10-05: um PDF com camada de texto digital
        real (extracted_pdf_text, de _expand_to_page_images) usa esse texto
        pro CORPO sem chamada de visao nenhuma - mais barato, mais preciso.
        O CABECALHO continua sempre por visao (nome em caixinhas e fragil
        demais pra reconstruir da camada de texto bruta)."""
        source = _write_page_image(
            self.tmp_dir / "page_4.png", header_text="cabecalho", body_text="corpo"
        )
        transcriber = FakeTranscriber(
            header_text="NOME COMPLETO DO PARTICIPANTE Joao da Silva",
            body_text="ISSO NUNCA DEVERIA SER LIDO - SE APARECER, A VISAO FOI CHAMADA",
        )
        service = EssayBatchService(None, transcriber=transcriber)
        extracted = "Um texto digital de verdade, com mais de trinta caracteres."

        name, _cpf, body = await service.read_page_regions(
            source, extracted_pdf_text=extracted
        )

        self.assertEqual(name, "JOAO DA SILVA")
        self.assertEqual(body, extracted.strip())
        # So o cabecalho chamou visao - nenhuma chamada pro corpo.
        self.assertEqual(len(transcriber.calls), 1)
        self.assertTrue(all("_header" in call for call in transcriber.calls))

    async def test_short_extracted_pdf_text_still_runs_body_ocr(self):
        """Abaixo do teto (EssaySubmissionService._MIN_EXTRACTED_TEXT_CHARS,
        30 - mesmo valor que o envio individual ja usa pra essa decisao) nao
        e confiavel o bastante: pode ser so o rotulo impresso da folha
        ("FOLHA DE REDAÇÃO", etc.) capturado sem nenhuma redacao de verdade
        por tras, entao continua rodando OCR normal."""
        source = _write_page_image(
            self.tmp_dir / "page_5.png", header_text="cabecalho", body_text="corpo"
        )
        transcriber = FakeTranscriber(
            header_text="NOME: Ana", body_text="Texto lido por OCR de verdade.",
        )
        service = EssayBatchService(None, transcriber=transcriber)

        _name, _cpf, body = await service.read_page_regions(
            source, extracted_pdf_text="curto demais"
        )

        self.assertEqual(body, "Texto lido por OCR de verdade.")
        self.assertEqual(len(transcriber.calls), 2)

    async def test_none_extracted_pdf_text_runs_body_ocr_as_before(self):
        """Default explicito: sem extracted_pdf_text (imagem, ou PDF sem
        camada de texto - foto/scan real), comportamento identico a antes."""
        source = _write_page_image(
            self.tmp_dir / "page_6.png", header_text="cabecalho", body_text="corpo"
        )
        transcriber = FakeTranscriber(
            header_text="NOME: Ana", body_text="Texto lido por OCR de verdade.",
        )
        service = EssayBatchService(None, transcriber=transcriber)

        _name, _cpf, body = await service.read_page_regions(source)

        self.assertEqual(body, "Texto lido por OCR de verdade.")
        self.assertEqual(len(transcriber.calls), 2)


if __name__ == "__main__":
    unittest.main()
