import pytest
from unittest.mock import patch, MagicMock
from summarizer import build_prompt, parse_summary, summarize


def test_build_prompt_includes_task_and_transcript():
    prompt = build_prompt(task="ask how their day went", transcript="AI: Hi\nThem: Good!")
    assert "ask how their day went" in prompt
    assert "AI: Hi" in prompt
    assert "Them: Good!" in prompt


def test_build_prompt_requests_three_sections():
    prompt = build_prompt(task="get address", transcript="...")
    assert "1." in prompt
    assert "2." in prompt
    assert "3." in prompt


def test_parse_summary_strips_whitespace():
    assert parse_summary("  Hello.  \n") == "Hello."


def test_parse_summary_empty_string():
    assert parse_summary("") == ""


@pytest.mark.asyncio
async def test_summarize_returns_model_text():
    mock_response = MagicMock()
    mock_response.text = "They had a good day."

    with patch("summarizer.genai.Client") as MockClient:
        instance = MockClient.return_value
        instance.models.generate_content.return_value = mock_response
        result = await summarize(
            task="ask how their day went",
            transcript="AI: Hi\nThem: Great!",
            api_key="test-key",
        )
    assert result == "They had a good day."


@pytest.mark.asyncio
async def test_summarize_returns_empty_string_on_error():
    with patch("summarizer.genai.Client", side_effect=Exception("API error")):
        result = await summarize(task="test", transcript="test", api_key="bad-key")
    assert result == ""
