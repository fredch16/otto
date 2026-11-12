import pytest

from otto_paperless_mcp.cards import AnthropicCardWriter, CardError, card_text, card_writer_from_env


def test_card_model_is_chosen_by_setting(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.delenv("OTTO_CARD_MODEL", raising=False)
    assert card_writer_from_env().model == "anthropic:claude-sonnet-5-5"
    monkeypatch.setenv("OTTO_CARD_MODEL", "anthropic:claude-haiku-5-5")
    assert isinstance(writer := card_writer_from_env(), AnthropicCardWriter)
    assert writer.model == "anthropic:claude-haiku-5-5"
    monkeypatch.setenv("OTTO_CARD_MODEL", "off")
    assert card_writer_from_env() is None
    monkeypatch.setenv("OTTO_CARD_MODEL", "nope:model")
    with pytest.raises(CardError, match="Unknown card provider"):
        card_writer_from_env()


def test_cards_are_off_without_a_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OTTO_CARD_MODEL", raising=False)
    assert card_writer_from_env() is None


def test_card_text_cites_pages():
    text = card_text({"document_type": "invoice", "language": "German", "date": "",
                      "parties": [{"name": "Stadtwerke", "role": "supplier"}], "summary": "Stromrechnung.",
                      "key_facts": [{"fact": "68,92 EUR", "page": 1}],
                      "deadlines": [{"date": "2026-11-15", "description": "Abbuchung", "page": 1}]})
    assert "Parties: Stadtwerke (supplier)" in text and "- 68,92 EUR (p. 1)" in text
    assert "- 2026-11-15: Abbuchung (p. 1)" in text and "Date" not in text
