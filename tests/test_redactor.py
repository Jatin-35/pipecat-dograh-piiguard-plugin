import pytest

from pipecat_dograh_piiguard.redactor import Redactor


@pytest.mark.asyncio
async def test_redacts_email_and_phone_with_placeholders():
    r = Redactor(strategy="placeholder")
    result = await r.redact("Hi, my email is jane@example.com and phone is 415-555-2671.")
    assert "[EMAIL_ADDRESS]" in result.redacted
    assert "[PHONE_NUMBER]" in result.redacted
    assert "jane@example.com" not in result.redacted


@pytest.mark.asyncio
async def test_rejects_invalid_luhn_credit_card():
    r = Redactor(strategy="placeholder")
    # Fails Luhn checksum -> not a valid card -> not redacted.
    result = await r.redact("card 4111111111111112")
    assert "[CREDIT_CARD]" not in result.redacted


@pytest.mark.asyncio
async def test_redacts_valid_luhn_credit_card():
    r = Redactor(strategy="placeholder")
    result = await r.redact("card 4111111111111111")
    assert "[CREDIT_CARD]" in result.redacted


@pytest.mark.asyncio
async def test_mask_strategy_preserves_visible_tail():
    from pipecat_dograh_piiguard.strategies import mask_strategy

    r = Redactor(strategy=lambda text, entities: mask_strategy(text, entities, visible_tail=4))
    result = await r.redact("card 4111111111111111")
    assert result.redacted == "card ************1111"


@pytest.mark.asyncio
async def test_empty_text_short_circuits():
    r = Redactor()
    result = await r.redact("")
    assert result.redacted == ""
    assert result.entities == []


@pytest.mark.asyncio
async def test_entity_types_filter_restricts_group():
    from pipecat_dograh_piiguard.detectors.regex import RegexPIIDetector

    r = Redactor(detector=RegexPIIDetector(entity_types=["pci"]), strategy="placeholder")
    result = await r.redact("email jane@example.com card 4111111111111111")
    assert "[CREDIT_CARD]" in result.redacted
    assert "jane@example.com" in result.redacted  # untouched: not in the "pci" group
