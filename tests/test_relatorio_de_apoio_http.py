"""O RELATÓRIO ATRAVESSA A API — e NÃO atravessa para mais ninguém.

O §17 tem duas metades, e esta é a que não pode ser provada por função pura.

A PRIMEIRA — ELE CHEGA
=======================
Gerar, visualizar, baixar em PDF. Com os fatos do banco, e não com os de uma
fixture em memória: o teste grava apoio de verdade em `guided_practice_items`
e exige que o papel diga que houve apoio. Se o leitor não estiver ligado, a
seção vem vazia e o teste fica vermelho.

A SEGUNDA — ELE NÃO É ENCAMINHADO
==================================
Proibido enviar ao professor, encaminhar à Coordenação, compartilhar,
notificar ou integrar a mecanismo de distribuição. Nem botão.

Garantir isso por AUSÊNCIA de código é frágil: amanhã alguém acrescenta a
rota sem saber que não podia. Então aqui ela é VARRIDA — as rotas registradas
do app, a fonte do serviço e a tela do aluno.

E A VARREDURA DISTINGUE O QUE É LEGÍTIMO
=========================================
O §17 manda não eliminar as rotinas de acompanhamento que a instituição já
tem: o portal do professor tem relatórios próprios, e eles continuam de pé —
há teste exigindo que continuem. O que não pode existir é o EDU encaminhando
ESTE relatório, o do aluno.

E ELE SÓ ALCANÇA O PRÓPRIO DONO
================================
A rota não aceita identificador de aluno nenhum. Não é uma checagem que
alguém possa esquecer de fazer: sem o parâmetro, pedir o relatório de outra
pessoa é inexprimível.
"""

from __future__ import annotations

import asyncio
import pathlib
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_session_factory,
)
from agente_ia_edu.db.base import Base
from agente_ia_edu.identity import AuthenticatedUserContext

from _fonte import codigo, menciona

RAIZ = pathlib.Path(__file__).resolve().parent.parent
FONTE = RAIZ / "src/agente_ia_edu"

# O ALUNO DE QA DESTE ARQUIVO — e nenhuma das identidades reservadas do dono,
# cujo histórico é dado real de validação manual. A lista delas vive em
# `tests/test_alunos_reservados.py`, que varre o repositório inteiro: nomeá-las
# aqui, mesmo em comentário, já seria violação — e foi assim que esta linha
# ficou do jeito que está.
ALUNO = "aluno_qa_relatorio"
CONTEUDO = "QUIM-ESTEQUIOMETRIA"

ROTA = "/api/v1/student/learning-support-report"


def _todas_as_rotas(app):
    """TODAS, inclusive as de dentro dos routers incluídos.

    Nesta versão do FastAPI, `app.routes` não é plano: cada `include_router`
    deixa um `_IncludedRouter` que guarda o router original em
    `original_router`. Varrer só o primeiro nível acha 64 entradas e nenhuma
    rota de verdade — foi exatamente o que aconteceu aqui, e por isso existe
    o teste que exige que a varredura ENCONTRE o que vai vigiar.

    E a varredura é estrutural de propósito, e não pelo OpenAPI: uma rota com
    `include_in_schema=False` não apareceria no esquema, e é justamente onde
    um encaminhamento ficaria escondido.
    """
    vistos = set()

    def desce(rotas):
        for r in rotas or []:
            if id(r) in vistos:
                continue
            vistos.add(id(r))
            if getattr(r, "path", None):
                yield r
            interno = getattr(r, "original_router", None)
            if interno is not None:
                yield from desce(getattr(interno, "routes", []))
            yield from desce(getattr(r, "routes", []))

    return list(desce(app.routes))


def _ctx():
    return AuthenticatedUserContext(
        user_id=ALUNO, external_identity_id=ALUNO, role="STUDENT",
        school_id=str(uuid.uuid5(uuid.NAMESPACE_DNS, "qa-relatorio")),
        scope_type="SCHOOL")


class _Base(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        # COMO A APLICAÇÃO: com False, um MissingGreenlet de leitura passa.
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=True)

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        self.loop.run_until_complete(prep())
        self.app = create_app()
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.app.dependency_overrides[get_current_authenticated_context] = _ctx
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        self.client.close()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _gravar_apoio(self, *, skill: str, dicas: int, sozinho=False,
                      aluno: str = ALUNO):
        """Apoio de verdade na tabela de verdade — não um dicionário fingido."""
        from agente_ia_edu.db.models import GuidedPracticeItem

        async def grava():
            async with self.factory() as s:
                s.add(GuidedPracticeItem(
                    id=uuid.uuid4(), student_external_id=aluno,
                    item_key=f"{skill}-{uuid.uuid4().hex[:6]}",
                    content_code=CONTEUDO, skill=skill,
                    attempts=1, hints_used=dicas, max_hint_level=dicas,
                    solved_unaided=sozinho, completed=True))
                await s.commit()

        self.loop.run_until_complete(grava())

    def _pedir(self, **params) -> dict:
        r = self.client.get(ROTA, params=params)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()


class ORELATORIOCHEGA(_Base):

    def test_a_rota_existe_e_devolve_o_documento(self):
        d = self._pedir()
        self.assertTrue(d.get("titulo"))
        self.assertTrue(d.get("secoes"))
        self.assertTrue(d.get("ressalva"))

    def test_sem_nada_registrado_ele_diz_isso_em_vez_de_inventar(self):
        d = self._pedir()
        texto = " ".join(i for s in d["secoes"] for i in s["itens"]).lower()
        self.assertIn("ainda não", texto)

    def test_o_apoio_GRAVADO_aparece_no_papel(self):
        """A prova de que o leitor está ligado ao banco, e não a uma fixture."""
        self._gravar_apoio(skill="MASSA_MOLAR", dicas=2)
        d = self._pedir()
        com_apoio = next(s for s in d["secoes"] if s["chave"] == "com_apoio")
        junto = " ".join(com_apoio["itens"])
        self.assertIn("olar", junto, f"o apoio gravado não chegou: {junto}")

    def test_e_o_apoio_de_OUTRO_aluno_nao_aparece(self):
        self._gravar_apoio(skill="COISA_ALHEIA", dicas=3, aluno="aluno_qa_outro")
        d = self._pedir()
        inteiro = " ".join(i for s in d["secoes"] for i in s["itens"]).lower()
        self.assertNotIn("alheia", inteiro)

    def test_quem_resolveu_sem_ajuda_nao_entra_como_apoio(self):
        self._gravar_apoio(skill="FEITO_SOZINHO", dicas=0, sozinho=True)
        d = self._pedir()
        com_apoio = next(s for s in d["secoes"] if s["chave"] == "com_apoio")
        self.assertNotIn("sozinho", " ".join(com_apoio["itens"]).lower())


class OPDFBAIXA(_Base):

    def setUp(self):
        from agente_ia_edu.services.report_render import pdf_available
        if not pdf_available():
            self.skipTest("pymupdf ausente neste ambiente")
        super().setUp()

    def test_a_rota_do_pdf_devolve_um_pdf(self):
        r = self.client.get(ROTA + ".pdf")
        self.assertEqual(r.status_code, 200, r.text[:300])
        self.assertIn("application/pdf", r.headers.get("content-type", ""))
        self.assertTrue(r.content.startswith(b"%PDF"), r.content[:20])

    def test_e_o_pdf_tem_o_TEXTO_do_relatorio_dentro(self):
        """Um PDF válido e vazio passaria no teste acima. Este abre e lê."""
        import pymupdf

        self._gravar_apoio(skill="MASSA_MOLAR", dicas=2)
        r = self.client.get(ROTA + ".pdf")
        doc = pymupdf.open(stream=r.content, filetype="pdf")
        texto = "\n".join(p.get_text() for p in doc)
        doc.close()
        self.assertIn("olar", texto, "o PDF saiu sem o conteúdo do relatório")
        self.assertIn("sozinho", texto.lower())

    def test_e_vem_como_download_com_nome(self):
        r = self.client.get(ROTA + ".pdf")
        disp = r.headers.get("content-disposition", "")
        self.assertIn("attachment", disp.lower())
        self.assertIn(".pdf", disp.lower())


class NAOEENCAMINHADO(_Base):
    """A metade obrigatória do §17, varrida em quatro lugares."""

    def _rotas_deste_relatorio(self):
        alvo = "learning-support-report"
        return [r for r in _todas_as_rotas(self.app)
                if alvo in str(getattr(r, "path", ""))]

    def test_a_varredura_encontra_as_rotas_que_deve_vigiar(self):
        """Sem isto, a varredura passaria verde vigiando o vazio."""
        self.assertTrue(self._rotas_deste_relatorio(),
                        "a varredura não achou rota nenhuma deste relatório — "
                        "ela estaria provando nada")

    def test_nenhuma_delas_aceita_metodo_que_produza_efeito(self):
        permitidos = {"GET", "HEAD", "OPTIONS"}
        for r in self._rotas_deste_relatorio():
            for metodo in (getattr(r, "methods", None) or set()):
                with self.subTest(f"{r.path} {metodo}"):
                    self.assertIn(metodo, permitidos,
                                  "rota com efeito colateral neste relatório")

    def test_nenhuma_delas_tem_verbo_de_envio_no_caminho(self):
        proibidos = ("send", "enviar", "forward", "encaminhar", "share",
                     "compartilhar", "notify", "notificar", "deliver",
                     "dispatch", "email", "mail", "teacher", "coordination")
        for r in self._rotas_deste_relatorio():
            for palavra in proibidos:
                with self.subTest(f"{r.path} {palavra}"):
                    self.assertNotIn(palavra, str(r.path).lower())

    def test_a_rota_nao_aceita_identificador_de_OUTRO_aluno(self):
        """Autorização por impossibilidade: sem parâmetro, não há o que vazar."""
        for r in self._rotas_deste_relatorio():
            caminho = str(r.path).lower()
            for suspeito in ("{student", "{aluno", "{external", "{user"):
                with self.subTest(f"{caminho} {suspeito}"):
                    self.assertNotIn(suspeito, caminho)

    def test_a_fonte_do_servico_nao_sabe_transportar_nada(self):
        fonte = codigo(FONTE / "services/relatorio_de_apoio.py")
        for transporte in ("smtplib", "aiosmtplib", "sendgrid", "httpx",
                           "requests", "boto3", "twilio"):
            with self.subTest(transporte):
                self.assertFalse(menciona(fonte, transporte),
                                 f"o relatório do aluno importou {transporte}")

    def test_e_nao_conhece_destinatario(self):
        fonte = codigo(FONTE / "services/relatorio_de_apoio.py")
        for quem in ("recipient", "destinatario", "to_address", "mailto"):
            with self.subTest(quem):
                self.assertFalse(menciona(fonte, quem))

    def test_a_tela_do_aluno_nao_tem_botao_de_enviar_o_relatorio(self):
        """§17: "Não incluir botão de envio ou compartilhamento integrado"."""
        arquivos = sorted(FONTE.glob("web/aluno*.js"))
        self.assertTrue(arquivos, "a tela do aluno não foi encontrada")
        for arquivo in arquivos:
            fonte = arquivo.read_text(encoding="utf-8").lower()
            for acao in ("relatorio-enviar", "relatorio-compartilhar",
                         "relatorio-encaminhar", "report-send", "report-share",
                         "navigator.share"):
                with self.subTest(f"{arquivo.name} {acao}"):
                    self.assertNotIn(acao, fonte)


class OQUEJAEXISTIANAOFOIELIMINADO(unittest.TestCase):
    """§17: "Não as elimine indiscriminadamente".

    A instituição já tem acompanhamento pedagógico legítimo — o portal do
    professor exporta relatórios próprios. A proibição é sobre o EDU
    encaminhar ESTE relatório, e não sobre existir relatório na plataforma.
    Se a varredura acima um dia for "resolvida" apagando o que é legítimo,
    este teste fica vermelho.
    """

    def test_o_relatorio_do_professor_continua_de_pe(self):
        from agente_ia_edu.services.report_export import ReportExportService

        self.assertTrue(hasattr(ReportExportService, "export_student_report"))

    def test_e_a_exportacao_em_pdf_dele_tambem(self):
        from agente_ia_edu.services.report_render import render_pdf

        self.assertTrue(callable(render_pdf))


if __name__ == "__main__":
    unittest.main()
