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

from . import explicacao_v1, explicacao_v2, v1, v2

_REGISTRO = {v1.VERSION: v1, v2.VERSION: v2}
VERSAO_ATUAL = v2.VERSION

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
