import os
from typing import Generator, List, Dict

import httpx


class LLMFallbackChain:
    """Groq (openai/gpt-oss-120b) → Gemini 2.0 Flash → OpenRouter."""

    def __init__(self):
        self.groq_key = os.getenv('GROQ_API_KEY', '')
        self.google_key = os.getenv('GOOGLE_API_KEY', '')
        self.openrouter_key = os.getenv('OPENROUTER_API_KEY', '')

    def stream(self, system: str, messages: List[Dict[str, str]]) -> Generator[str, None, None]:
        providers = [
            self._stream_groq,
            self._stream_gemini,
            self._stream_openrouter,
            self._stream_offline,
        ]
        last_error = None
        for provider in providers:
            try:
                yield from provider(system, messages)
                return
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                continue
        yield f'I am having trouble responding right now. ({last_error})'

    def _stream_groq(self, system: str, messages: List[Dict[str, str]]):
        if not self.groq_key:
            raise RuntimeError('GROQ_API_KEY missing')
        from groq import Groq

        client = Groq(api_key=self.groq_key)
        chat_messages = [{'role': 'system', 'content': system}]
        for m in messages:
            role = 'user' if m.get('role') == 'visitor' else 'assistant'
            chat_messages.append({'role': role, 'content': m['content']})
        
        # DYNAMIC TEMPERATURE (Creative vs Factual)
        creative_keywords = ["write", "draft", "slogan", "creative", "email"]
        is_creative = any(kw in messages[-1]['content'].lower() for kw in creative_keywords)
        temp_value = 0.7 if is_creative else 0.3

        stream = client.chat.completions.create(
            model='openai/gpt-oss-120b', # UPDATED MODEL (Fixes 404 error)
            messages=chat_messages,
            stream=True,
            temperature=temp_value, # Dynamic temperature
        )
        for chunk in stream:
            delta = chunk.choices[0].delta.content or ''
            if delta:
                yield delta

    def _stream_gemini(self, system: str, messages: List[Dict[str, str]]):
        if not self.google_key:
            raise RuntimeError('GOOGLE_API_KEY missing')
        import google.generativeai as genai

        genai.configure(api_key=self.google_key)
        model = genai.GenerativeModel(
            'gemini-2.0-flash',
            system_instruction=system,
        )
        history = []
        for m in messages[:-1]:
            role = 'user' if m.get('role') == 'visitor' else 'model'
            history.append({'role': role, 'parts': [m['content']]})
        chat = model.start_chat(history=history)
        last = messages[-1]['content'] if messages else ''
        response = chat.send_message(last, stream=True)
        for chunk in response:
            if getattr(chunk, 'text', None):
                yield chunk.text

    def _stream_openrouter(self, system: str, messages: List[Dict[str, str]]):
        if not self.openrouter_key:
            raise RuntimeError('OPENROUTER_API_KEY missing')
        payload = {
            'model': 'meta-llama/llama-3.3-70b-instruct:free',
            'stream': True,
            'messages': [{'role': 'system', 'content': system}]
            + [
                {
                    'role': 'user' if m.get('role') == 'visitor' else 'assistant',
                    'content': m['content'],
                }
                for m in messages
            ],
        }
        with httpx.stream(
            'POST',
            'https://openrouter.ai/api/v1/chat/completions',
            headers={
                'Authorization': f'Bearer {self.openrouter_key}',
                'Content-Type': 'application/json',
            },
            json=payload,
            timeout=90.0,
        ) as resp:
            resp.raise_for_status()
            for line in resp.iter_lines():
                if not line.startswith('data: '):
                    continue
                data = line[6:]
                if data == '[DONE]':
                    break
                import json

                obj = json.loads(data)
                delta = obj.get('choices', [{}])[0].get('delta', {}).get('content') or ''
                if delta:
                    yield delta

    def _stream_offline(self, system: str, messages: List[Dict[str, str]]):
        # Dev fallback when no API keys are configured
        last = messages[-1]['content'] if messages else ''
        yield (
            'Thanks for your message. I am running in offline demo mode '
            f'(no LLM API keys configured). You asked: "{last}". '
            'Add GROQ_API_KEY, GOOGLE_API_KEY, or OPENROUTER_API_KEY to enable live replies.'
        )