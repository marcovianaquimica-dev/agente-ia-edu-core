"""A ESCADA DE APOIO — e a retirada dele.

O QUE FALTAVA
==============
O sistema já sabia oferecer ajuda. Ele não sabia TIRÁ-LA. Cada ciclo
recomeçava com o mesmo grau de assistência, e um aluno que tivesse acabado de
resolver duas etapas sozinho recebia, no ciclo seguinte, exatamente o mesmo
andaime do primeiro dia.

A escada aqui é derivada do que já aconteceu, e nada é persistido só para ela:

    L3  INVESTIGAÇÃO   uma micropergunta de cada vez — "vamos fazer juntos"
    L2  ENSINO         a explicação, agora sabendo o que explicar
    L1  GUIADA         ele tenta, a dica chega se pedir
    L0  AUTÔNOMO       questão sozinho — a única que vira evidência

A REGRA QUE IMPORTA
====================
Só o L0 produz evidência de domínio. Subir a escada não dá nota, e descê-la
não tira. A escada decide QUANTO AJUDAR, nunca QUANTO O ALUNO SABE — e há
teste de que o módulo não conhece a política de domínio.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from agente_ia_edu.services.escada_de_apoio import (
    NIVEIS,
    NIVEL_AUTONOMO,
    NIVEL_ENSINO,
    NIVEL_GUIADA,
    NIVEL_INVESTIGACAO,
    nivel_de_apoio,
    produz_evidencia,
    rotulo_do_nivel,
)


class AESCADATEMQUATRODEGRAUS(unittest.TestCase):

    def test_do_apoio_alto_ao_autonomo(self):
        self.assertEqual(
            (NIVEL_INVESTIGACAO, NIVEL_ENSINO, NIVEL_GUIADA, NIVEL_AUTONOMO),
            NIVEIS)

    def test_cada_degrau_tem_um_rotulo_para_o_aluno(self):
        for n in NIVEIS:
            with self.subTest(n=n):
                self.assertTrue(rotulo_do_nivel(n).strip())

    def test_nenhum_rotulo_mostra_o_codigo_do_degrau(self):
        """"Você está no nível L2" não quer dizer nada para um adolescente."""
        for n in NIVEIS:
            with self.subTest(n=n):
                self.assertNotIn(n.lower(), rotulo_do_nivel(n).lower())


class OPRIMEIRODEGRAUEAINVESTIGACAO(unittest.TestCase):
    """§7: investigar antes de despejar a resolução."""

    def test_sem_historico_nenhum_comeca_investigando(self):
        self.assertEqual(
            NIVEL_INVESTIGACAO,
            nivel_de_apoio(investigacao_concluida=False, ja_ensinado=False,
                           guiada_concluida=False, ha_investigacao=True))

    def test_sem_investigacao_escrita_o_primeiro_degrau_e_o_ensino(self):
        """Conteúdo sem cadeia curada não inventa uma: cai no degrau abaixo."""
        self.assertEqual(
            NIVEL_ENSINO,
            nivel_de_apoio(investigacao_concluida=False, ja_ensinado=False,
                           guiada_concluida=False, ha_investigacao=False))


class OAPOIODIMINUIAPOSPROGRESSO(unittest.TestCase):
    """§12 — o fading, medido degrau a degrau."""

    def test_concluir_a_investigacao_leva_ao_ensino(self):
        self.assertEqual(
            NIVEL_ENSINO,
            nivel_de_apoio(investigacao_concluida=True, ja_ensinado=False,
                           guiada_concluida=False, ha_investigacao=True))

    def test_depois_de_estudar_vem_a_guiada(self):
        self.assertEqual(
            NIVEL_GUIADA,
            nivel_de_apoio(investigacao_concluida=True, ja_ensinado=True,
                           guiada_concluida=False, ha_investigacao=True))

    def test_depois_da_guiada_ele_tenta_sozinho(self):
        self.assertEqual(
            NIVEL_AUTONOMO,
            nivel_de_apoio(investigacao_concluida=True, ja_ensinado=True,
                           guiada_concluida=True, ha_investigacao=True))

    def test_a_escada_so_desce(self):
        """Percorrendo o caminho inteiro, o apoio nunca volta a subir."""
        caminho = [
            dict(investigacao_concluida=False, ja_ensinado=False,
                 guiada_concluida=False),
            dict(investigacao_concluida=True, ja_ensinado=False,
                 guiada_concluida=False),
            dict(investigacao_concluida=True, ja_ensinado=True,
                 guiada_concluida=False),
            dict(investigacao_concluida=True, ja_ensinado=True,
                 guiada_concluida=True),
        ]
        indices = [NIVEIS.index(nivel_de_apoio(ha_investigacao=True, **p))
                   for p in caminho]
        self.assertEqual(sorted(indices), indices)
        self.assertEqual(len(set(indices)), len(indices))


class SOOAUTONOMOPRODUZEVIDENCIA(unittest.TestCase):
    """§13. A invariante que todo o resto do sistema já respeita."""

    def test_o_autonomo_produz(self):
        self.assertTrue(produz_evidencia(NIVEL_AUTONOMO))

    def test_os_tres_degraus_assistidos_nao_produzem(self):
        for n in (NIVEL_INVESTIGACAO, NIVEL_ENSINO, NIVEL_GUIADA):
            with self.subTest(n=n):
                self.assertFalse(produz_evidencia(n))

    def test_exatamente_um_degrau_produz_evidencia(self):
        self.assertEqual(1, sum(1 for n in NIVEIS if produz_evidencia(n)))


class AESCADANAOCONHECEODOMINIO(unittest.TestCase):

    FONTE = (pathlib.Path(__file__).resolve().parent.parent
             / "src/agente_ia_edu/services/escada_de_apoio.py")

    def test_o_modulo_nao_toca_banco_nem_provedor(self):
        baixo = self.FONTE.read_text(encoding="utf-8").lower()
        for proibido in ("asyncsession", "sqlalchemy", "db.models",
                         "textgenerationprovider", "build_text_provider"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, baixo)

    def test_o_modulo_nao_importa_a_politica_de_dominio(self):
        arvore = ast.parse(self.FONTE.read_text(encoding="utf-8"))
        nomes: list[str] = []
        for no in ast.walk(arvore):
            if isinstance(no, ast.ImportFrom):
                nomes.append(no.module or "")
                nomes += [a.name for a in no.names]
            elif isinstance(no, ast.Import):
                nomes += [a.name for a in no.names]
        for proibido in ("pedagogical_analysis", "curriculum_domain_map"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, nomes)

    def test_o_modulo_nao_escreve_numero_de_corte(self):
        for no in ast.walk(ast.parse(self.FONTE.read_text(encoding="utf-8"))):
            if isinstance(no, ast.Constant) and isinstance(no.value, float):
                self.fail(f"literal float na escada de apoio: {no.value}")


if __name__ == "__main__":
    unittest.main()
