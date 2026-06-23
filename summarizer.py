import asyncio
from loguru import logger
from google import genai


def build_prompt(task: str, transcript: str) -> str:
    return f"""Adam's task was: "{task}"

Transcript:
{transcript}

Extract concisely:
1. One-sentence answer to the task.
2. Key facts mentioned (names, numbers, addresses, yes/no answers) — bullet list. Write "None" if none.
3. Anything Adam should follow up on — one sentence. Write "Nothing" if none."""


def parse_summary(text: str) -> str:
    return text.strip()


async def summarize(
    task: str,
    transcript: str,
    api_key: str,
    model: str = "gemini-2.0-flash",
) -> str:
    """Summarize a call transcript in the context of the task. Never raises."""
    try:
        client = genai.Client(api_key=api_key)
        prompt = build_prompt(task, transcript)
        response = await asyncio.to_thread(
            lambda: client.models.generate_content(model=model, contents=prompt)
        )
        return parse_summary(response.text)
    except Exception as e:
        logger.error(f"Summarizer failed: {e}")
        return ""
