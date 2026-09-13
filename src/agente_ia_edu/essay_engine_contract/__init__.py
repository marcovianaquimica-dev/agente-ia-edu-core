"""Versioned engine-output contracts.

A correction persisted under ``essay_engine_output_v1`` must stay readable by the
schema it was born with, so a shape change is a NEW ``vN`` module, never an edit
to an existing one - the same rule ``classification_prompts`` follows.
"""
