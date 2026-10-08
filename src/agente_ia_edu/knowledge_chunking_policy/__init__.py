"""Politicas de chunking versionadas do CEREBRO / Knowledge Engine.

Mesmo regime de ``essay_engine_contract`` e ``classification_prompts``: uma
mudanca de forma e um modulo ``vN`` NOVO, nunca uma edicao de um existente.
Um chunk persistido guarda em ``metadata.chunking_policy_version`` a versao
sob a qual nasceu, e precisa continuar explicavel por ela.
"""
