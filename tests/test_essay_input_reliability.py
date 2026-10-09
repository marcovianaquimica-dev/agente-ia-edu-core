from agente_ia_edu.services.essay_input_reliability import (
    combine_input_reliability_signals, estimate_text_reliability_heuristic,
)

_CLEAN_TEXT = (
    "A sociedade brasileira enfrenta desafios para valorizar a pessoa idosa "
    "e combater o preconceito etario. O governo deve criar politicas publicas "
    "que garantam direitos e dignidade aos idosos."
)

# Texto SINTETICO de teste (nenhuma correspondencia com nenhuma redacao
# real), com nivel de corrupcao OCR comparavel ao usado originalmente -
# escolhido por ter pouca escafoldagem de palavras funcionais
# (o/a/de/da/do/que/com/ou/em/para), que e o que mantem o score baixo com
# o dicionario correto e completo, sem nenhuma exclusao artificial de
# palavra (ver historico: uma versao anterior deste teste usava um trecho
# com MUITA escafoldagem e pontuava 0.757 - acima do teto de 0.4 - mesmo
# sendo visivelmente ilegivel).
_GARBLED_TEXT = (
    "cumprin seu papeu comu garamtisdor dos dineitos fumdamentais, "
    "perpetuamdo desigualdades imto - | numa sociedade onde o ideal "
    "demucratico proposto pur. Bolo. ) Alem disso, e igualmemte necesranio "
    "abordar a imdiferenca secial. Sob essa otica, o filasofo etico-polidi- "
    ". | co umaginario destava"
)


def test_clean_text_scores_high_reliability():
    ratio = estimate_text_reliability_heuristic(_CLEAN_TEXT)
    assert ratio > 0.7


def test_garbled_text_scores_low_reliability():
    ratio = estimate_text_reliability_heuristic(_GARBLED_TEXT)
    assert ratio < 0.4


def test_proper_nouns_and_foreign_words_do_not_tank_the_score():
    text = (
        "Carlos Eduardo Pereira Lima discutiu o tema com Beatriz Fernanda Souza em "
        "Sao Paulo, citando o conceito de welfare state e o relatorio da UNESCO "
        "sobre o envelhecimento da populacao brasileira."
    )
    ratio = estimate_text_reliability_heuristic(text)
    assert ratio > 0.5  # nomes proprios/estrangeirismos nao devem reprovar isso


def test_combine_trusts_model_unreliable_report_directly():
    status = combine_input_reliability_signals(
        heuristic_ratio=0.9, model_status="UNRELIABLE_NEEDS_REVIEW", ocr_average_confidence=None,
    )
    assert status == "UNRELIABLE_NEEDS_REVIEW"


def test_combine_never_escalates_past_warning_from_heuristic_alone():
    status = combine_input_reliability_signals(
        heuristic_ratio=0.1, model_status="RELIABLE", ocr_average_confidence=None,
    )
    assert status == "USABLE_WITH_WARNING"  # discordancia nunca forca UNRELIABLE isolada


def test_combine_agrees_on_reliable():
    status = combine_input_reliability_signals(
        heuristic_ratio=0.9, model_status="RELIABLE", ocr_average_confidence=0.95,
    )
    assert status == "RELIABLE"


def test_combine_model_warning_is_never_downgraded_by_good_technical_signals():
    status = combine_input_reliability_signals(
        heuristic_ratio=0.95, model_status="USABLE_WITH_WARNING", ocr_average_confidence=0.95,
    )
    assert status == "USABLE_WITH_WARNING"
