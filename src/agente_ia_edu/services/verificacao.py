"""O CONTRATO DO ITEM DE VERIFICACAO - o degrau L0, sem ajuda.

ONDE ELE FICA NA ESCADA
========================
    L3  INVESTIGACAO   uma micropergunta de cada vez
    L2  ENSINO         a explicacao, sabendo o que explicar
    L1  GUIADA         ele tenta, e a dica chega se ele pedir
    L0  AUTONOMO       ESTE ITEM - sozinho, e so ele produz evidencia

O QUE DISTINGUE UM ITEM DE VERIFICACAO DE UM ITEM DE SONDAGEM
==============================================================
Os dois medem UMA micro-habilidade, e nisso sao iguais - `ItemDeSondagem` ja
dizia isso bem. Duas coisas mudam:

1. ELE VEM DEPOIS DO ENSINO, e por isso nao pode ser o item que foi
   ensinado. A sondagem pode perguntar NH3 porque e a primeira coisa que o
   aluno ve; a verificacao nao pode, porque ele acabou de ver a resolucao do
   NH3 passo a passo. Verificar com o item ensinado mede memoria recente.

2. TODO DISTRATOR E UM ERRO NOMEADO. Numa sondagem um distrator plausivel
   basta. Numa verificacao, marcar o distrator e a informacao mais util que
   o aluno pode dar - e ela se perde se ninguem escreveu de antemao o que
   aquela alternativa significa.

   `__post_init__` exige isso: alternativa errada sem erro declarado e um
   item malformado, e um item malformado nao falha ruidosamente - ele mede
   errado, em silencio, e o resultado entra na evidencia do aluno.

O QUE ESTE MODULO NAO SABE
===========================
Nada de nenhuma disciplina. `fonte` e o objeto sobre o qual a conta e
refeita - em Quimica e a formula, e em outra disciplina e outra coisa. Quem
sabe refazer a conta e o modulo de conteudo, no seu `conferir()`.

E ELE NAO DECIDE DOMINIO
=========================
Um item de verificacao respondido corretamente produz EVIDENCIA. Quem
transforma evidencia em banda e `PerformanceThresholdPolicy`, que exige
`min_sample_size` respostas - e um acerto isolado continua sendo amostra
insuficiente. Esta separacao nao e detalhe: e o que impede "acertou uma"
de virar "domina".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

# A finalidade declarada deste tipo de item, como ela viaja na
# classificacao: `PedagogicalClassification.metadata_["purpose"]`.
#
# A sondagem usa `PROBE` e o contrato de `instrumento_de_sondagem` a exige.
# Sem um valor PROPRIO aqui, um item de verificacao seria elegivel como
# instrumento de sondagem - e o diagnostico passaria a medir com um item
# escrito para ser respondido DEPOIS do ensino.
FINALIDADE_VERIFICACAO = "VERIFICATION"


@dataclass(frozen=True)
class ItemDeVerificacao:
    """Um item curado que verifica UMA micro-habilidade, sem apoio."""

    key: str
    habilidade: str
    enunciado: str
    alternativas: Mapping[str, str]
    correta: str
    # letra -> o que marcar aquela alternativa significa. Obrigatorio para
    # toda alternativa errada; ver o cabecalho.
    erros: Mapping[str, str] = field(default_factory=dict)
    # O objeto sobre o qual a conta e refeita. Em Quimica, a formula.
    fonte: str | None = None
    # Como conferir o gabarito sem confiar em quem escreveu o item.
    conferencia: str | None = None

    def __post_init__(self) -> None:
        if not (self.enunciado or "").strip():
            raise ValueError(f"item {self.key!r} sem enunciado")
        if not self.alternativas:
            raise ValueError(f"item {self.key!r} sem alternativas")
        if self.correta not in self.alternativas:
            raise ValueError(
                f"item {self.key!r}: a correta {self.correta!r} nao esta "
                f"entre as alternativas")
        faltando = [letra for letra in self.alternativas
                    if letra != self.correta and letra not in self.erros]
        if faltando:
            raise ValueError(
                f"item {self.key!r}: alternativas sem erro declarado: "
                f"{faltando}. Marcar um distrator e informacao, e ela se "
                f"perde se ninguem escreveu o que ele significa.")


def itens_para(itens: Sequence[ItemDeVerificacao], *, habilidade: str
               ) -> tuple[ItemDeVerificacao, ...]:
    """Os itens que verificam aquela micro-habilidade, na ordem escrita.

    Ordem estavel de proposito: a selecao de questoes do banco e
    deterministica, e uma ordem que variasse aqui tornaria a sequencia
    servida ao aluno irreprodutivel na depuracao.
    """
    return tuple(i for i in (itens or ()) if i.habilidade == habilidade)


__all__ = [
    "FINALIDADE_VERIFICACAO",
    "ItemDeVerificacao",
    "itens_para",
]
