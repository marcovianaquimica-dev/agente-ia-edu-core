"""Prompts da CONVERSA do Assessor Pedagogico - artefatos versionados.

Mesma convencao de `classification_prompts`: o sistema e dono do prompt, ele e
independente de fornecedor (nenhum nome de vendor, modelo, SDK ou parametro
especifico), e NUNCA se edita a redacao de uma versao existente - uma mudanca
de redacao e um modulo novo mais uma entrada no registro.

A razao e a decisao arquitetural do Nucleo Edu 360: a inteligencia pertence ao
sistema, e a IA e um componente substituivel. Um prompt versionado e o que
permite trocar o modelo sem perder o que o sistema aprendeu a pedir.
"""

from __future__ import annotations

from . import explicacao_v1, explicacao_v2, v1, v2, v3, v4

# A v3 corrige o que a v2 EXIGIA: "2 a 5 frases" para toda pergunta, e
# "termine oferecendo" ao fim de toda resposta. O §4 da especificacao recusa
# as duas - o teto universal e a oferta obrigatoria -, e a decisao de quanto
# falar passou para `services/concisao`, com teste. A v2 fica no registro:
# ela e o que conversou com quem usou aquelas telas.
# A v4 corrige o que a v3 PODIA recusar: a regra "se a pergunta nao tiver
# nada a ver com o estudo, traga de volta ao ponto" pegava, na leitura
# literal, a curiosidade de OUTRA DISCIPLINA - que tem tudo a ver com estudo.
# O §9 manda responder a curiosidade espontanea e permitir exploracao
# introdutoria de conteudo avancado, sem exigir pre-requisito. A v4 separa
# "assunto de estudo fora do trilho" de "nada a ver com estudo", e recebe o
# percurso como argumento, de `services/percurso`.
_REGISTRO = {v1.VERSION: v1, v2.VERSION: v2, v3.VERSION: v3, v4.VERSION: v4}
VERSAO_ATUAL = v4.VERSION

# A EXPLICACAO DE UM ERRO TEM O SEU PROPRIO REGISTRO.
#
# Nao e a mesma conversa com outra redacao: muda o que entra (ali o gabarito
# fica de fora de proposito; aqui a questao ja foi corrigida e ele precisa
# entrar), muda o que se pede e muda o campo da resposta. Dois registros
# separados deixam as duas versoes andarem no seu proprio ritmo.
# A v2 corrige o que a v1 PEDIA: "comece pelo que ele provavelmente fez".
# Medido no navegador em 2026-10-07, o modelo obedeceu e o aluno leu "voce
# dobrou a proporcao de H2" - uma operacao mental afirmada a partir de uma
# letra marcada. A v1 fica no registro: ela e o que explicou para quem leu
# aquelas telas, e apagar isso seria apagar o historico.
_REGISTRO_DA_EXPLICACAO = {explicacao_v1.VERSION: explicacao_v1,
                           explicacao_v2.VERSION: explicacao_v2}
VERSAO_ATUAL_DA_EXPLICACAO = explicacao_v2.VERSION


def prompt_da_conversa(versao: str | None = None):
    """O modulo de prompt da versao pedida, ou o atual."""
    escolhida = versao or VERSAO_ATUAL
    modulo = _REGISTRO.get(escolhida)
    if modulo is None:
        raise KeyError(f"versao de prompt desconhecida: {escolhida!r}; "
                       f"conhecidas: {sorted(_REGISTRO)}")
    return modulo


def prompt_da_explicacao(versao: str | None = None):
    """O modulo de prompt da explicacao de erro, da versao pedida ou atual."""
    escolhida = versao or VERSAO_ATUAL_DA_EXPLICACAO
    modulo = _REGISTRO_DA_EXPLICACAO.get(escolhida)
    if modulo is None:
        raise KeyError(f"versao de prompt desconhecida: {escolhida!r}; "
                       f"conhecidas: {sorted(_REGISTRO_DA_EXPLICACAO)}")
    return modulo


__all__ = ["prompt_da_conversa", "prompt_da_explicacao", "VERSAO_ATUAL",
           "VERSAO_ATUAL_DA_EXPLICACAO"]
