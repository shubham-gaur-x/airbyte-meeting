"""LLM extractor — supports Groq (cloud) or Ollama (local) as backend."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import time
from typing import Optional

import httpx
import structlog

from models import ExtractedMeeting, RawEmail
from utils import with_retry

log = structlog.get_logger()

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "qwen/qwen3-32b"  # fast, free, great structured output


class OllamaUnavailableError(Exception):
    pass


class LowConfidenceError(Exception):
    def __init__(self, confidence: float, title: str) -> None:
        self.confidence = confidence
        self.title = title
        super().__init__(f"Low confidence {confidence:.2f} for '{title}'")


def _strip_markdown(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _build_prompt(email: RawEmail) -> str:
    return f"""You are a meeting intelligence assistant. Extract structured data from the meeting email below.
Return ONLY a valid JSON object with these exact fields (no markdown, no explanation):

{{
  "title": "meeting title",
  "kind": "meeting",
  "platform": "google-meet|zoom|teams|in-person|unknown",
  "date": "YYYY-MM-DD or null",
  "start_time": "HH:MM or null",
  "end_time": "HH:MM or null",
  "duration_minutes": 60,
  "location": "",
  "attendees": [{{"name": "Full Name", "email": "email@example.com", "role": "organizer|attendee|optional"}}],
  "summary": "2-3 sentence summary",
  "topics": ["topic1", "topic2"],
  "decisions": ["decision text"],
  "action_items": [{{"owner": "name or email", "task": "task description", "due": "YYYY-MM-DD or null", "done": false, "priority": "high|medium|low"}}],
  "key_quotes": [],
  "links": [],
  "sentiment": "positive|neutral|negative",
  "follow_up_needed": true,
  "confidence": 0.95
}}

Subject: {email.subject}
From: {email.sender}
Date: {email.received_at.strftime('%Y-%m-%d')}
Body:
{email.body_text[:4000]}"""


def _use_groq() -> bool:
    return bool(os.environ.get("GROQ_API_KEY", "").strip())


@with_retry(max_attempts=3, base_delay=2.0)
async def _extract_groq(email: RawEmail) -> ExtractedMeeting:
    api_key = os.environ["GROQ_API_KEY"]
    t0 = time.monotonic()
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
        resp = await client.post(
            GROQ_API_URL,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": _build_prompt(email)}],
                "temperature": 0.0,
                "max_tokens": 2048,
            },
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"]

    duration_ms = int((time.monotonic() - t0) * 1000)
    raw = _strip_markdown(raw)

    # qwq model sometimes adds <think>...</think> before JSON — strip it
    raw = re.sub(r"<think>.*?</think>\s*", "", raw, flags=re.DOTALL).strip()
    raw = _strip_markdown(raw)

    meeting = ExtractedMeeting.model_validate_json(raw)
    log.info("extractor.groq", model=GROQ_MODEL, duration_ms=duration_ms,
             confidence=meeting.confidence, title=meeting.title, attendees=len(meeting.attendees))

    if meeting.confidence < 0.3:
        raise LowConfidenceError(meeting.confidence, meeting.title)
    return meeting


@with_retry(max_attempts=3, base_delay=2.0)
async def _extract_ollama(email: RawEmail) -> ExtractedMeeting:
    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    model = os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")

    auth = os.environ.get("OLLAMA_NGROK_AUTH", "").strip()
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = "Basic " + base64.b64encode(auth.encode()).decode()

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:
            t0 = time.monotonic()
            resp = await client.post(
                f"{base_url}/api/generate",
                headers=headers,
                json={
                    "model": model,
                    "prompt": _build_prompt(email),
                    "stream": False,
                    "options": {"temperature": 0.0, "num_predict": 2048},
                },
            )
            resp.raise_for_status()
    except httpx.ConnectError as exc:
        raise OllamaUnavailableError(f"Cannot reach Ollama at {base_url}.") from exc

    duration_ms = int((time.monotonic() - t0) * 1000)
    raw = _strip_markdown(resp.json()["response"])
    meeting = ExtractedMeeting.model_validate_json(raw)
    log.info("extractor.ollama", model=model, duration_ms=duration_ms,
             confidence=meeting.confidence, title=meeting.title, attendees=len(meeting.attendees))

    if meeting.confidence < 0.3:
        raise LowConfidenceError(meeting.confidence, meeting.title)
    return meeting


async def extract(email: RawEmail) -> ExtractedMeeting:
    """Extract structured meeting data. Uses Groq if GROQ_API_KEY is set, else Ollama."""
    if _use_groq():
        return await _extract_groq(email)
    return await _extract_ollama(email)


def extract_sync(email: RawEmail) -> ExtractedMeeting:
    """Synchronous wrapper for CLI scripts."""
    return asyncio.run(extract(email))
