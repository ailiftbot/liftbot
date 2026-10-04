"""Streaming LLM fallback chain: Groq -> Gemini -> OpenRouter (-> offline demo).

A provider is skipped/fallen-through ONLY if it produced no tokens. If it
fails after streaming part of an answer we stop there rather than appending a
second, different answer from the next provider.
"""
from __future__ import annotations

import re
import json
import logging
import os
from typing import Dict, Generator, List

import httpx

logger = logging.getLogger(__name__)

PROVIDER_TIMEOUT = 30.0
UNAVAILABLE_REPLY = 'I am having trouble responding right now. Please try again in a moment.'
CREATIVE_KEYWORDS = ('write', 'draft', 'slogan', 'creative', 'email')


class ProviderNotConfigured(RuntimeError):
    pass


def _role(m: Dict[str, str]) -> str:
    return 'user' if m.get('role') == 'visitor' else 'assistant'


class LLMFallbackChain:
    def __init__(self):
        self.groq_key = os.getenv('GROQ_API_KEY', '').strip()
        self.google_key = os.getenv('GOOGLE_API_KEY', '').strip()
        self.openrouter_key = os.getenv('OPENROUTER_API_KEY', '').strip()
        self.groq_model = os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b')
        self.gemini_model = os.getenv('GEMINI_CHAT_MODEL', 'gemini-2.5-flash')
        self.openrouter_model = os.getenv('OPENROUTER_MODEL', 'meta-llama/llama-3.3-70b-instruct:free')
        self._groq_client = None
        self._gemini_client = None

    def configured(self) -> Dict[str, bool]:
        return {
            'groq': bool(self.groq_key),
            'gemini': bool(self.google_key),
            'openrouter': bool(self.openrouter_key),
        }

    def models(self) -> Dict[str, str]:
        return {'groq': self.groq_model, 'gemini': self.gemini_model, 'openrouter': self.openrouter_model}

    @staticmethod
    def _temperature(messages: List[Dict[str, str]]) -> float:
        last = (messages[-1].get('content') or '').lower() if messages else ''
        return 0.7 if any(kw in last for kw in CREATIVE_KEYWORDS) else 0.3

    def stream(self, system: str, messages: List[Dict[str, str]]) -> Generator[str, None, None]:
        providers = [
            ('groq', self._stream_groq),
            ('gemini', self._stream_gemini),
            ('openrouter', self._stream_openrouter),
        ]
        if not any(self.configured().values()):
            providers.append(('offline', self._stream_offline))

        for name, provider in providers:
            yielded = False
            try:
                for token in provider(system, messages):
                    if token:
                        yielded = True
                        yield token
            except ProviderNotConfigured:
                continue
            except Exception:  # noqa: BLE001
                logger.exception('LLM provider %s failed (yielded=%s)', name, yielded)
                if yielded:
                    return  # partial answer already sent; don't append another
                continue
            if yielded:
                return
            logger.warning('LLM provider %s returned an empty response; falling through', name)
        yield UNAVAILABLE_REPLY

    # ------------------------------------------------------------- providers
    def _stream_groq(self, system: str, messages: List[Dict[str, str]]):
        if not self.groq_key:
            raise ProviderNotConfigured('GROQ_API_KEY missing')
        if self._groq_client is None:
            from groq import Groq

            self._groq_client = Groq(api_key=self.groq_key, timeout=PROVIDER_TIMEOUT)
        chat_messages = [{'role': 'system', 'content': system}]
        chat_messages += [{'role': _role(m), 'content': m['content']} for m in messages]
        stream = self._groq_client.chat.completions.create(
            model=self.groq_model,
            messages=chat_messages,
            stream=True,
            temperature=self._temperature(messages),
        )
        for chunk in stream:
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta.content or ''
            if delta:
                yield delta

    def _stream_gemini(self, system: str, messages: List[Dict[str, str]]):
        if not self.google_key:
            raise ProviderNotConfigured('GOOGLE_API_KEY missing')
        from google import genai
        from google.genai import types

        if self._gemini_client is None:
            self._gemini_client = genai.Client(
                api_key=self.google_key,
                http_options=types.HttpOptions(timeout=int(PROVIDER_TIMEOUT * 1000)),
            )
        contents = [
            types.Content(
                role='user' if m.get('role') == 'visitor' else 'model',
                parts=[types.Part(text=m['content'])],
            )
            for m in messages
        ]
        response = self._gemini_client.models.generate_content_stream(
            model=self.gemini_model,
            contents=contents,
            config=types.GenerateContentConfig(
                system_instruction=system,
                temperature=self._temperature(messages),
            ),
        )
        for chunk in response:
            text = getattr(chunk, 'text', None)
            if text:
                yield text

    def _stream_openrouter(self, system: str, messages: List[Dict[str, str]]):
        if not self.openrouter_key:
            raise ProviderNotConfigured('OPENROUTER_API_KEY missing')
        payload = {
            'model': self.openrouter_model,
            'stream': True,
            'temperature': self._temperature(messages),
            'messages': [{'role': 'system', 'content': system}]
            + [{'role': _role(m), 'content': m['content']} for m in messages],
        }
        with httpx.stream(
            'POST',
            'https://openrouter.ai/api/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {self.openrouter_key}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=PROVIDER_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith('data: '):
                    continue
                data = line[6:].strip()
                if data == '[DONE]':
                    break
                obj = json.loads(data)
                if obj.get('error'):
                    raise RuntimeError(f"OpenRouter error: {obj['error']}")
                choices = obj.get('choices') or [{}]
                delta = (choices[0].get('delta') or {}).get('content') or ''
                if delta:
                    yield delta

    def _stream_offline(self, system: str, messages: List[Dict[str, str]]):
        # Used only when NO provider keys are configured: answer extractively with
        # the knowledge passage that best overlaps the question, so the product
        # stays demoable (and retrieval is observable) without an LLM.
        last = messages[-1]['content'] if messages else ''
        yield _best_passage(system, last) or OFFLINE_NO_ANSWER


OFFLINE_NO_ANSWER = "I am sorry, I don't have that specific information yet. I will note it down and follow up."
_WORD = re.compile(r"[a-z0-9']+")
_STOP = frozenset(
    'a an and are as at be by can do does for from have how i if in is it me my of on or our '
    'the there this to was we what when where which who why will with you your'.split()
)


def _best_passage(system: str, question: str) -> str:
    start = system.find('### KNOWLEDGE BASE CONTEXT ###')
    stop = system.find('### STRICT INSTRUCTIONS ###')
    if start < 0:
        return ''
    context = system[start + len('### KNOWLEDGE BASE CONTEXT ###'):stop if stop > start else None]
    terms = {w for w in _WORD.findall(question.lower()) if w not in _STOP}
    if not terms:
        return ''
    best, best_score = '', 0.0
    for passage in re.split(r'\n\s*\n|(?<=[.!?])\s+(?=[A-Z])', context):
        passage = passage.strip()
        if not passage or passage.startswith('No relevant training context'):
            continue
        words = set(_WORD.findall(passage.lower()))
        # Light stemming so "park" matches "parking".
        score = sum(1 for t in terms if t in words or any(w.startswith(t) for w in words if len(t) > 3))
        if score > best_score:
            best, best_score = passage, score
    return best[:600]
