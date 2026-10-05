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

from . import v1

_REGISTRO = {v1.VERSION: v1}
VERSAO_ATUAL = v1.VERSION


def prompt_da_conversa(versao: str | None = None):
    """O modulo de prompt da versao pedida, ou o atual."""
    escolhida = versao or VERSAO_ATUAL
    modulo = _REGISTRO.get(escolhida)
    if modulo is None:
        raise KeyError(f"versao de prompt desconhecida: {escolhida!r}; "
                       f"conhecidas: {sorted(_REGISTRO)}")
    return modulo


__all__ = ["prompt_da_conversa", "VERSAO_ATUAL"]
