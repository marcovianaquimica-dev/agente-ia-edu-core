from ranking import calcular_ranking


def test_ranking_sem_empates():
    notas = [("a", 900), ("b", 800), ("c", 700)]
    assert calcular_ranking(notas) == {"a": 1, "b": 2, "c": 3}


def test_ranking_com_empate_no_topo():
    notas = [("a", 800), ("b", 800), ("c", 600)]
    assert calcular_ranking(notas) == {"a": 1, "b": 1, "c": 3}


def test_ranking_com_empate_no_meio():
    notas = [("a", 900), ("b", 700), ("c", 700), ("d", 500)]
    assert calcular_ranking(notas) == {"a": 1, "b": 2, "c": 2, "d": 4}


def test_ranking_lista_vazia():
    assert calcular_ranking([]) == {}
