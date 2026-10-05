"""Live OpenAI-SDK acceptance: models, free call, paid call, SSE."""

import os

from openai import OpenAI

base = os.environ["RUBAL_BASE"]
platform_key = os.environ["RUBAL_KEY"]
free_model = os.environ["FREE_MODEL"]
paid_model = os.environ["PAID_MODEL"]

client = OpenAI(base_url=base, api_key=platform_key, timeout=90)

models = client.models.list()
print(f"[sdk] models.list -> {len(models.data)} models")
assert any(m.id for m in models.data)

# Free model, non-streaming
free = client.chat.completions.create(
    model=free_model,
    messages=[{"role": "user", "content": "Say hello in three words."}],
    max_tokens=24,
)
print(f"[sdk] free chat -> usage={free.usage.prompt_tokens}/{free.usage.completion_tokens} text={free.choices[0].message.content!r}")

# Paid model, non-streaming (small but non-zero)
paid = client.chat.completions.create(
    model=paid_model,
    messages=[{"role": "user", "content": "Write one short sentence about Saint Petersburg."}],
    max_tokens=200,
)
print(f"[sdk] paid chat -> usage={paid.usage.prompt_tokens}/{paid.usage.completion_tokens} text={(paid.choices[0].message.content or '')[:60]!r}")

# Streaming on the free model
stream = client.chat.completions.create(
    model=free_model,
    messages=[{"role": "user", "content": "Count from one to three."}],
    max_tokens=24,
    stream=True,
)
chunks = 0
text = ""
for chunk in stream:
    chunks += 1
    if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
        text += chunk.choices[0].delta.content
print(f"[sdk] stream -> chunks={chunks} text={text!r}")

# A tools request (passthrough)
tool = client.chat.completions.create(
    model=free_model,
    messages=[{"role": "user", "content": "What is the weather in Yekaterinburg?"}],
    max_tokens=64,
    tools=[
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get weather for a city",
                "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]},
            },
        }
    ],
    tool_choice="auto",
)
print(f"[sdk] tools call -> finish={tool.choices[0].finish_reason} usage={tool.usage.prompt_tokens}/{tool.usage.completion_tokens}")

print("[sdk] OK")
