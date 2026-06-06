"""Sanitize an empty ANTHROPIC_AUTH_TOKEN before building the Anthropic client.

Claude Desktop injects ``ANTHROPIC_AUTH_TOKEN=""`` into the environment. Left
in place, the Anthropic SDK prefers the (empty) Bearer token over the valid
``sk-ant-...`` api_key and emits ``Authorization: Bearer ``, which httpcore
rejects as an illegal header — surfacing as a generic ``APIConnectionError``.
``_sanitize_auth_env`` removes the empty token so api_key auth is used.
"""

import os

from docorganizer.cli import _sanitize_auth_env


def test_empty_auth_token_is_removed(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "")
    _sanitize_auth_env()
    assert "ANTHROPIC_AUTH_TOKEN" not in os.environ


def test_unset_auth_token_stays_unset(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    _sanitize_auth_env()  # must not raise
    assert "ANTHROPIC_AUTH_TOKEN" not in os.environ


def test_real_auth_token_is_preserved(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "sk-ant-real-token")
    _sanitize_auth_env()
    assert os.environ["ANTHROPIC_AUTH_TOKEN"] == "sk-ant-real-token"
