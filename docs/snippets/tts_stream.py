import httpx

BASE_URL = "{{BASE_URL}}"  # for example http://192.168.1.20:8000
API_KEY = "{{API_KEY}}"  # keep it out of source control

url = f"{BASE_URL}/v1/text-to-speech/{{VOICE}}/stream"
payload = {
    "text": "Hello! Welcome to the local voice AI gateway running fully offline.",
    "model_id": "{{MODEL}}",
    "voice_settings": {"speed": 1.0},
}
headers = {"Authorization": f"Bearer {API_KEY}"}

with httpx.stream("POST", url, json=payload, headers=headers, timeout=60) as response:
    response.raise_for_status()
    with open("output.pcm", "wb") as f:
        for chunk in response.iter_bytes():
            f.write(chunk)

print("Saved output.pcm (24 kHz, 16-bit PCM mono)")
# Play command: ffplay -f s16le -ar 24000 -ac 1 output.pcm
