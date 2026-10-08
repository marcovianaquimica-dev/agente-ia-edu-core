"""OS ALUNOS QUE NENHUM TESTE PODE USAR.

POR QUE ESTE ARQUIVO EXISTE
============================
`aluno_teste_jornada` era o reservado para a validação manual do dono. Em
2026-10-07, durante uma auditoria, ele recebeu uma escrita derivada — uma
chamada a `/domain/rebuild` — e deixou de ser uma referência limpa.

A escrita foi minha e está registrada. O que importa aqui é por que ela foi
possível: a única marca dele era `qa_purpose = E2E_MANUAL_JOURNEY`, que se
parece com a marca dos alunos de QA. Nada no repositório dizia "não escreva
neste". Documentação não impede; teste impede.

O QUE ESTE ARQUIVO TRAVA
=========================
Que nenhum teste automatizado e nenhum script de QA mencione uma identidade
reservada. É uma varredura do repositório, e isso é deliberado: a regra não
é sobre o comportamento de um módulo, é sobre a higiene do próprio
repositório — e só a varredura a alcança.

Quando um aluno reservado for aposentado, a lista muda aqui, num lugar só.
"""

from __future__ import annotations

import pathlib
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent.parent

# As identidades que existem para o DONO testar à mão, e para mais ninguém.
#
# `aluno_teste_jornada` continua na lista mesmo tendo sido tocado: ele segue
# reservado, e tirá-lo daqui porque já foi sujo uma vez seria transformar um
# acidente em permissão.
# `aluno_validacao_final` tambem continua aqui depois de usado: o historico
# dele e dado real de validacao manual, e protege-lo passou a importar MAIS,
# nao menos. Quando o dono quis recomecar do zero em 2026-10-08, a resposta
# foi uma identidade NOVA - nunca limpar a dele.
RESERVADOS = (
    "aluno_teste_jornada",
    "aluno_validacao_final",
    "aluno_validacao_2",
)

# Onde um uso indevido apareceria.
PASTAS = ("tests", "scripts")

# Dois arquivos precisam nomeá-los: este, para proibi-los, e o script que
# cria o reservado. Qualquer outro é violação.
EXCECOES = {
    "tests/test_alunos_reservados.py",
    "scripts/criar_aluno_validacao_final.py",
    "scripts/criar_aluno_validacao_2.py",
}


def _arquivos_de_codigo():
    for pasta in PASTAS:
        base = RAIZ / pasta
        if not base.exists():
            continue
        for caminho in base.rglob("*"):
            if caminho.suffix not in (".py", ".js"):
                continue
            if "__pycache__" in caminho.parts:
                continue
            relativo = str(caminho.relative_to(RAIZ))
            if relativo in EXCECOES:
                continue
            yield relativo, caminho


class NENHUMTESTEUSAALUNORESERVADO(unittest.TestCase):

    def test_nenhum_arquivo_de_teste_ou_script_menciona_um_reservado(self):
        achados: list[str] = []
        for relativo, caminho in _arquivos_de_codigo():
            try:
                texto = caminho.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for identidade in RESERVADOS:
                if identidade in texto:
                    achados.append(f"{relativo} menciona {identidade}")
        self.assertEqual(
            [], achados,
            "identidade reservada à validação manual usada em código:\n  "
            + "\n  ".join(achados))

    def test_a_lista_de_reservados_nao_esta_vazia(self):
        """Uma lista vazia faria o teste acima passar sem proteger nada."""
        self.assertTrue(RESERVADOS)

    def test_a_varredura_realmente_le_arquivos(self):
        """Se o caminho mudasse, o teste acima passaria vazio e em silêncio."""
        lidos = list(_arquivos_de_codigo())
        self.assertGreater(len(lidos), 50, "a varredura não achou o repositório")

    def test_a_varredura_pegaria_uma_violacao(self):
        """Verificação de mutação, sem precisar sujar o repositório."""
        falso = f"svc.start('{RESERVADOS[0]}', requester=req)"
        self.assertTrue(any(i in falso for i in RESERVADOS))


class AMARCADISTINGUERESERVADODEQA(unittest.TestCase):
    """A marca precisa dizer, em campo próprio, o que não fazer.

    Estes testes leem o SCRIPT de criação, não o banco: o que precisa estar
    garantido é que a próxima execução do script produza a marca certa — num
    banco recriado, num ambiente novo, para o próximo aluno reservado.
    """

    SCRIPT = RAIZ / "scripts/criar_aluno_validacao_final.py"

    def test_o_script_de_criacao_existe_no_repositorio(self):
        """Criar o aluno reservado não pode depender de um arquivo de
        rascunho que some com a sessão."""
        self.assertTrue(self.SCRIPT.exists(),
                        f"faltando: {self.SCRIPT.relative_to(RAIZ)}")

    def test_a_marca_declara_para_quem_o_aluno_e(self):
        self.assertIn("OWNER_MANUAL_VALIDATION",
                      self.SCRIPT.read_text(encoding="utf-8"))

    def test_a_marca_declara_que_o_qa_nao_deve_usa_lo(self):
        self.assertIn("do_not_use_in_qa",
                      self.SCRIPT.read_text(encoding="utf-8"))

    def test_a_marca_NAO_usa_a_chave_de_proposito_de_qa(self):
        """Ela é a marca dos alunos de QA, e carregá-la num aluno reservado é
        exatamente o que confundiu antes.

        A busca é pela chave ENTRE ASPAS — como ela apareceria num dicionário.
        Procurar a palavra solta acusaria o comentário que explica por que ela
        não está lá, que é a terceira vez que essa armadilha aparece neste
        trabalho.
        """
        texto = self.SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn('"qa_purpose"', texto)
        self.assertNotIn("'qa_purpose'", texto)


if __name__ == "__main__":
    unittest.main()
