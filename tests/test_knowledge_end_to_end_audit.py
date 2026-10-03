"""CEREBRO - auditoria do caminho ponta a ponta. Invariantes, nao exemplos.

    pergunta -> embedding -> VectorSearcher -> ContextBuilder
             -> GroundedAnswerer -> validacao -> saida publica

Os outros arquivos testam que o caminho FUNCIONA. Este testa que ele nao
pode ser quebrado por distracao: que a politica de direitos nao afrouxou,
que nenhuma saida publica carrega literal comercial, que marcador invalido
continua falhando fechado, e que ninguem expos o caminho por HTTP sem
autenticacao.

A DIFERENCA ENTRE AUSENCIA E GARANTIA
=====================================

Varios itens desta auditoria sao hoje verdadeiros por AUSENCIA: nao existe
rota HTTP para a geracao fundamentada, entao nenhum usuario comum recebe
fonte nenhuma. Isso nao e a regra implementada - e a regra ainda nao
necessaria. Os testes abaixo dizem qual dos dois e cada caso, e o guarda
estrutural quebra de proposito no dia em que a rota aparecer.

Nenhuma chamada paga.
"""

from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from agente_ia_edu.services.knowledge_engine.context_builder import (
    RIGHTS_NO_PROVIDER,
    ContextBuilder,
)
from agente_ia_edu.services.knowledge_engine.grounded_answer import (
    GROUNDED,
    INVALID_EVIDENCE_REFERENCE,
    GroundedAnswerer,
)
from agente_ia_edu.services.knowledge_engine.rights import (
    RIGHTS_CLASSES,
    may_expose_literal_text,
    may_send_literal_to_provider,
    may_use_literal_in_processing,
    max_excerpt_chars,
)

from test_knowledge_context_builder import _hit, _textos
from test_knowledge_grounded_answer import _Roteiro

RAIZ = Path(__file__).resolve().parents[1]
SRC = RAIZ / "src" / "agente_ia_edu"
LITERAL = "SEGREDO EDITORIAL QUE NAO PODE VAZAR"


def _contexto_comercial(n=3):
    hits = [
        _hit(rank=r, rights="COMMERCIAL_REFERENCE",
             raw_text=f"{LITERAL} trecho {r}", titulo="Livro comercial")
        for r in range(1, n + 1)
    ]
    return ContextBuilder().build(hits, texts=_textos(hits))


# ------------------------------------------------- 1. literal em publico


class CommercialLiteralNeverInPublicTests(unittest.IsolatedAsyncioTestCase):
    """O vazamento mais facil de cometer e o mais caro de descobrir."""

    def test_the_context_admin_payload_is_clean(self):
        ctx = _contexto_comercial()
        self.assertNotIn(LITERAL, json.dumps(ctx.admin_payload(),
                                             ensure_ascii=False))

    def test_the_context_repr_is_clean(self):
        ctx = _contexto_comercial()
        self.assertNotIn(LITERAL, repr(ctx))
        self.assertNotIn(LITERAL, str(ctx))

    def test_each_evidence_repr_is_clean(self):
        ctx = _contexto_comercial()
        for e in ctx.evidences:
            self.assertNotIn(LITERAL, repr(e))

    def test_the_prompt_payload_is_the_only_place_that_has_it(self):
        """E tem mesmo: sem literal nao ha o que fundamentar."""
        ctx = _contexto_comercial()
        self.assertIn(LITERAL, ctx.prompt_payload())

    async def test_the_answer_admin_payload_is_clean(self):
        ctx = _contexto_comercial()
        provider = _Roteiro({"answer": "Resposta [E1].",
                             "used_evidence": ["E1"]})
        r = await GroundedAnswerer(provider=provider).answer("p", ctx)
        self.assertNotIn(LITERAL, json.dumps(r.admin_payload(),
                                             ensure_ascii=False))

    async def test_the_diagnostic_raw_field_never_reaches_repr(self):
        """``_raw`` e o texto cru do provider e pode ecoar trecho comercial.

        E ``repr=False`` porque imprimir o objeto num log de diagnostico e o
        vazamento mais facil de cometer. Aqui o provider devolve um JSON
        malformado contendo o literal: a resposta estruturada nao existe,
        entao se o literal aparecer em ``repr`` veio de ``_raw``.
        """
        ctx = _contexto_comercial()
        provider = _Roteiro(None, cru=f"nao e json {LITERAL}")
        r = await GroundedAnswerer(provider=provider).answer("p", ctx)
        self.assertNotIn(LITERAL, repr(r))
        self.assertNotIn(LITERAL, json.dumps(r.admin_payload(),
                                             ensure_ascii=False))

    async def test_characterises_the_model_echoing_commercial_literal(self):
        """RISCO EM ABERTO, e nao um defeito do que foi construido.

        Se o modelo copiar literalmente um trecho comercial para dentro da
        resposta, esse trecho sai: a resposta E a saida publica, e nenhuma
        camada compara o texto gerado contra o literal das evidencias.

        O desenho impede o vazamento pelos CAMINHOS DO SISTEMA - excerpt,
        repr, serializacao, log. Nao impede o modelo de reproduzir o que
        recebeu, porque para isso seria preciso decidir o que fazer ao
        detectar: truncar, recusar, ou marcar. Decisao de produto, nao
        correcao. Registrado com teste para nao ser esquecido.
        """
        ctx = _contexto_comercial()
        provider = _Roteiro({"answer": f"Copiando: {LITERAL} [E1]",
                             "used_evidence": ["E1"]})
        r = await GroundedAnswerer(provider=provider).answer("p", ctx)
        self.assertIn(LITERAL, r.answer)
        self.assertIn(LITERAL, json.dumps(r.admin_payload(),
                                          ensure_ascii=False))

    async def test_no_cited_evidence_carries_an_excerpt_for_commercial(self):
        ctx = _contexto_comercial()
        provider = _Roteiro({"answer": "x [E1] [E2]",
                             "used_evidence": ["E1", "E2"]})
        r = await GroundedAnswerer(provider=provider).answer("p", ctx)
        self.assertEqual(len(r.cited_evidences), 2)
        for e in r.cited_evidences:
            self.assertIsNone(e.excerpt)


# ------------------------------------------- 2. exposicao HTTP / ADMIN


class NoUnauthenticatedExposureTests(unittest.TestCase):
    """GUARDA ESTRUTURAL, e nao verificacao de autorizacao.

    Hoje ``context_builder`` e ``grounded_answer`` nao sao importados por
    nenhuma rota: o caminho ponta a ponta so existe em CLI e testes. Logo
    "usuario comum nao recebe fontes" e verdadeiro por AUSENCIA de rota, nao
    por uma regra implementada.

    Este teste quebra no dia em que alguem publicar o caminho. Quando isso
    acontecer, ele deve ser SUBSTITUIDO por um teste real de autorizacao -
    nao relaxado.
    """

    def _modulos_de_rota(self):
        return sorted((SRC / "api").rglob("*.py"))

    def test_no_api_module_imports_the_generation_path(self):
        proibidos = {"context_builder", "grounded_answer"}
        ofensores = []
        for caminho in self._modulos_de_rota():
            arvore = ast.parse(caminho.read_text(encoding="utf-8"))
            for no in ast.walk(arvore):
                if isinstance(no, ast.ImportFrom) and no.module:
                    if proibidos & set(no.module.split(".")):
                        ofensores.append(caminho.name)
                elif isinstance(no, ast.Import):
                    for alias in no.names:
                        if proibidos & set(alias.name.split(".")):
                            ofensores.append(caminho.name)
        self.assertEqual(
            ofensores, [],
            "O caminho de geracao fundamentada foi exposto por HTTP. "
            "Substitua este guarda por um teste de autorizacao real: "
            "fontes, evidencias e rastreabilidade so podem sair para "
            "PLATFORM_ADMIN.",
        )

    def test_the_existing_knowledge_routes_all_require_platform_admin(self):
        """A convencao que uma rota futura tem de seguir.

        Nao e decorativo: e a prova de que o padrao existe e de qual e ele,
        para que a rota de geracao nao invente outro.
        """
        for nome in ("knowledge_engine.py", "knowledge_lexical.py"):
            caminho = SRC / "api" / "routes" / nome
            texto = caminho.read_text(encoding="utf-8")
            arvore = ast.parse(texto)
            rotas = [
                no for no in ast.walk(arvore)
                if isinstance(no, (ast.FunctionDef, ast.AsyncFunctionDef))
                and any(
                    isinstance(d, ast.Call)
                    and isinstance(d.func, ast.Attribute)
                    and d.func.attr in {"get", "post", "put", "delete", "patch"}
                    for d in no.decorator_list
                )
            ]
            self.assertTrue(rotas, f"{nome} sem rota detectada")
            for rota in rotas:
                defaults = [
                    ast.unparse(d) for d in rota.args.defaults
                    + [k for k in rota.args.kw_defaults if k is not None]
                ]
                self.assertTrue(
                    any("require_platform_admin" in d for d in defaults),
                    f"{nome}:{rota.name} nao exige PLATFORM_ADMIN",
                )


# ------------------------------------------------ 3. politica de direitos


class RightsPolicyNotWeakenedTests(unittest.TestCase):
    def test_the_three_zones_are_distinct_questions(self):
        """Processamento, provider e saida publica sao perguntas diferentes.

        Se as tres funcoes fossem a mesma coisa, a separacao seria decorativa
        e a primeira licenca restritiva vazaria.
        """
        self.assertTrue(may_use_literal_in_processing("COMMERCIAL_REFERENCE"))
        self.assertTrue(may_send_literal_to_provider("COMMERCIAL_REFERENCE"))
        self.assertFalse(may_expose_literal_text("COMMERCIAL_REFERENCE"))

    def test_every_rights_function_fails_closed_on_an_unknown_class(self):
        for fn in (may_use_literal_in_processing,
                   may_send_literal_to_provider,
                   may_expose_literal_text):
            with self.subTest(fn=fn.__name__):
                self.assertFalse(fn("CLASSE_QUE_NAO_EXISTE"))
                self.assertFalse(fn(""))

    def test_commercial_has_no_excerpt_budget_at_all(self):
        self.assertIsNone(max_excerpt_chars("COMMERCIAL_REFERENCE"))

    def test_a_class_that_may_not_reach_the_provider_is_excluded_by_name(self):
        """Excluida com motivo, nao incluida sem texto."""
        hits = [_hit(rank=1, rights="OWN"),
                _hit(rank=2, rights="CLASSE_DESCONHECIDA")]
        ctx = ContextBuilder().build(hits, texts=_textos(hits))
        self.assertEqual(len(ctx.evidences), 1)
        self.assertEqual(ctx.excluded[0]["reason"], RIGHTS_NO_PROVIDER)

    def test_the_known_classes_are_still_the_declared_set(self):
        """Se alguem acrescentar classe, que seja deliberado."""
        self.assertIn("COMMERCIAL_REFERENCE", RIGHTS_CLASSES)
        self.assertEqual(len(RIGHTS_CLASSES), len(set(RIGHTS_CLASSES)))


# --------------------------------------------- 4. portoes falham fechados


class GatesFailClosedTests(unittest.IsolatedAsyncioTestCase):
    async def test_an_invalid_marker_still_denies_grounding(self):
        ctx = _contexto_comercial()
        provider = _Roteiro({"answer": "Inventado [E7].",
                             "used_evidence": ["E7"]})
        r = await GroundedAnswerer(provider=provider).answer("p", ctx)
        self.assertEqual(r.status, INVALID_EVIDENCE_REFERENCE)
        self.assertFalse(r.is_grounded)

    async def test_normalisation_did_not_open_a_hole(self):
        """A correcao de ``[E1]`` normalizou GRAFIA, nao afrouxou validacao."""
        ctx = _contexto_comercial(2)
        provider = _Roteiro({"answer": "Resposta sem marcador no corpo.",
                             "used_evidence": ["[E1]", "[E9]"]})
        r = await GroundedAnswerer(provider=provider).answer("p", ctx)
        self.assertEqual(r.status, INVALID_EVIDENCE_REFERENCE)
        self.assertIn("E9", r.invalid_markers)
        self.assertNotIn("E1", r.invalid_markers)

    async def test_exactly_one_status_has_is_grounded_true(self):
        """A propriedade central: fundamentada e um estado, nao um adjetivo."""
        ctx = _contexto_comercial()
        casos = {
            GROUNDED: {"answer": "ok [E1]", "used_evidence": ["E1"]},
            INVALID_EVIDENCE_REFERENCE: {"answer": "ok [E9]",
                                         "used_evidence": ["E9"]},
        }
        for esperado, payload in casos.items():
            with self.subTest(status=esperado):
                r = await GroundedAnswerer(
                    provider=_Roteiro(payload)).answer("p", ctx)
                self.assertEqual(r.status, esperado)
                self.assertEqual(r.is_grounded, esperado == GROUNDED)


# -------------------------------------------------- 5. observabilidade


class ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_degradation_reasons_survive_to_the_admin_payload(self):
        ctx = _contexto_comercial()
        provider = _Roteiro({"answer": "x [E1]", "used_evidence": ["E1"]})
        r = await GroundedAnswerer(provider=provider).answer(
            "p", ctx, retrieval_degraded=True, allow_degraded=True,
            degradation_reasons=("MISSING_EMBEDDINGS",),
        )
        publico = r.admin_payload()
        self.assertTrue(publico["degraded"])
        self.assertIn("MISSING_EMBEDDINGS", publico["degradation_reasons"])

    async def test_real_token_counts_reach_the_admin_payload(self):
        ctx = _contexto_comercial()
        provider = _Roteiro({"answer": "x [E1]", "used_evidence": ["E1"]})
        r = await GroundedAnswerer(provider=provider).answer("p", ctx)
        self.assertEqual(r.admin_payload()["input_tokens"], 120)
        self.assertEqual(r.admin_payload()["output_tokens"], 40)

    def test_every_hit_is_accounted_for_as_included_or_excluded(self):
        """Nenhuma evidencia some em silencio - a conta tem de fechar."""
        hits = [_hit(rank=r, raw_text="X" * 4000) for r in range(1, 11)]
        ctx = ContextBuilder().build(hits, texts=_textos(hits))
        self.assertEqual(len(ctx.evidences) + len(ctx.excluded), len(hits))
        for x in ctx.excluded:
            self.assertIn("reason", x)
            self.assertIn("rank", x)
            self.assertIn("chunk_id", x)

    def test_the_available_markers_are_always_reported(self):
        """Para julgar um marcador invalido e preciso saber quais existiam."""
        ctx = _contexto_comercial(4)
        self.assertEqual(ctx.marker_set(), {"E1", "E2", "E3", "E4"})


# ------------------------------------------------- 6. isolamento do CLI


class NoLoggingYetTests(unittest.TestCase):
    """"Logs publicos nao vazam ``raw_text``" e hoje verdadeiro por AUSENCIA.

    Nenhum modulo de ``knowledge_engine`` escreve log: nao ha ``logging``,
    nao ha ``logger``, nao ha ``print``. O literal nao vaza em log porque
    nao existe log.

    Este guarda quebra quando o primeiro log aparecer. Nao e para proibir
    logging - e para que quem o introduzir decida explicitamente o que pode
    ser registrado. ``BuiltContext`` e ``GroundedAnswer`` ja tem ``repr``
    limpo, mas um ``logger.debug(texto_do_chunk)`` passaria por cima disso.
    """

    def test_the_knowledge_engine_writes_no_logs_today(self):
        pasta = SRC / "services" / "knowledge_engine"
        ofensores = []
        for caminho in sorted(pasta.glob("*.py")):
            arvore = ast.parse(caminho.read_text(encoding="utf-8"))
            for no in ast.walk(arvore):
                if isinstance(no, ast.Import):
                    if any(a.name.split(".")[0] == "logging" for a in no.names):
                        ofensores.append(f"{caminho.name}: import logging")
                elif isinstance(no, ast.ImportFrom) and no.module == "logging":
                    ofensores.append(f"{caminho.name}: from logging")
                elif isinstance(no, ast.Call) and isinstance(no.func, ast.Name):
                    if no.func.id == "print":
                        ofensores.append(f"{caminho.name}: print()")
        self.assertEqual(
            ofensores, [],
            "Surgiu log no knowledge_engine. Decida explicitamente o que "
            "pode ser registrado: literal de obra comercial nao pode, e "
            "repr limpo nao protege contra log de texto cru.",
        )


class CliDoesNotBypassTheAdminPayloadTests(unittest.TestCase):
    """O CLI e hoje o unico consumidor, logo o unico vazamento possivel."""

    def test_the_cli_builds_its_report_from_the_admin_payloads(self):
        texto = (RAIZ / "scripts" / "cerebro_ask.py").read_text(encoding="utf-8")
        self.assertIn("admin_payload()", texto)
        self.assertNotIn("prompt_payload()", texto)

    def test_the_cli_warns_when_the_model_declares_evidence_insufficient(self):
        """O sinal e consultivo, e tem de continuar visivel.

        ``sufficient: false`` nao altera o status - decisao em aberto -, mas
        some do relatorio seria pior: quem le passaria a nao ter como saber.
        Este teste existe para que a remocao do aviso seja deliberada.
        """
        texto = (RAIZ / "scripts" / "cerebro_ask.py").read_text(encoding="utf-8")
        self.assertIn("model_says_sufficient is False", texto)
        self.assertIn("INSUFICIENTES", texto)

    def test_the_cli_reads_raw_text_only_to_feed_the_builder(self):
        """Ler o literal e permitido no processamento interno; imprimi-lo
        nao. A unica mencao a ``raw_text`` deve ser a consulta que alimenta
        ``texts``."""
        texto = (RAIZ / "scripts" / "cerebro_ask.py").read_text(encoding="utf-8")
        mencoes = [linha.strip() for linha in texto.splitlines()
                   if "raw_text" in linha]
        self.assertEqual(len(mencoes), 1, f"mencoes a raw_text: {mencoes}")
        self.assertIn("select(", mencoes[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
