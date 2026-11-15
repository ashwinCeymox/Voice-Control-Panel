from openai import OpenAI

# Compatible with OpenAI official SDK by pointing base_url to local gateway
client = OpenAI(
    base_url="{{BASE_URL}}/v1",
    api_key="{{API_KEY}}",
)

response = client.chat.completions.create(
    model="{{MODEL}}",
    messages=[{"role": "user", "content": "Hello! Introduce yourself."}],
    stream=True,
)

for chunk in response:
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
print()
