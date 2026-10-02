import pytest

from faixas import classificar


@pytest.mark.parametrize(
    "nota, esperado",
    [
        (0, "Muito baixo"),
        (399, "Muito baixo"),
        (400, "Baixo"),
        (599, "Baixo"),
        (600, "Adequado"),
        (799, "Adequado"),
        (800, "Alto"),
        (899, "Alto"),
        (900, "Muito alto"),
        (1000, "Muito alto"),
    ],
)
def test_classificar_faixas(nota, esperado):
    assert classificar(nota) == esperado


def test_classificar_fora_do_intervalo_leva_erro():
    with pytest.raises(ValueError):
        classificar(1001)
    with pytest.raises(ValueError):
        classificar(-1)
