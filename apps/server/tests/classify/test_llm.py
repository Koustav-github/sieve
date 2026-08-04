from unittest.mock import MagicMock

from app.classify import llm as llm_module
from app.classify.schemas import L3ClassificationResult, SubjectExtractionResult


def test_build_l3_llm_binds_l3_classification_schema(monkeypatch):
    fake_chat = MagicMock()
    fake_bound = MagicMock()
    fake_chat.with_structured_output.return_value = fake_bound
    monkeypatch.setattr(llm_module, "ChatAnthropic", MagicMock(return_value=fake_chat))

    result = llm_module.build_l3_llm()

    fake_chat.with_structured_output.assert_called_once_with(L3ClassificationResult)
    assert result is fake_bound


def test_build_subject_extraction_llm_binds_subject_schema(monkeypatch):
    fake_chat = MagicMock()
    fake_bound = MagicMock()
    fake_chat.with_structured_output.return_value = fake_bound
    monkeypatch.setattr(llm_module, "ChatAnthropic", MagicMock(return_value=fake_chat))

    result = llm_module.build_subject_extraction_llm()

    fake_chat.with_structured_output.assert_called_once_with(SubjectExtractionResult)
    assert result is fake_bound


def test_build_l3_llm_passes_configured_api_key(monkeypatch):
    monkeypatch.setattr(llm_module.settings, "anthropic_api_key", "test-key-123")
    fake_chat_cls = MagicMock()
    fake_chat_cls.return_value.with_structured_output.return_value = MagicMock()
    monkeypatch.setattr(llm_module, "ChatAnthropic", fake_chat_cls)

    llm_module.build_l3_llm()

    _, kwargs = fake_chat_cls.call_args
    assert kwargs["api_key"] == "test-key-123"
