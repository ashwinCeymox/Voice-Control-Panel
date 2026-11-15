import json
import httpx

BASE_URL = "{{BASE_URL}}"   # for example http://192.168.1.20:8000
API_KEY = "{{API_KEY}}"

payload = {
    "model": "{{MODEL}}",
    "messages": [{"role": "user", "content": "Hello! Introduce yourself briefly."}],
    "stream": True,
}
headers = {"Authorization": f"Bearer {API_KEY}"}

with httpx.stream("POST", f"{BASE_URL}/v1/chat/completions",
                  json=payload, headers=headers, timeout=120) as r:
    r.raise_for_status()
    for line in r.iter_lines():
        if line.startswith("data: ") and line != "data: [DONE]":
            delta = json.loads(line[6:])["choices"][0]["delta"]
            print(delta.get("content", ""), end="", flush=True)
print()
