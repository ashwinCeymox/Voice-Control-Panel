import httpx

BASE_URL = "{{BASE_URL}}"  # for example http://192.168.1.20:8000
API_KEY = "{{API_KEY}}"
headers = {"Authorization": f"Bearer {API_KEY}"}

with open("audio.wav", "rb") as f:
    r = httpx.post(
        f"{BASE_URL}/v1/audio/transcriptions",
        headers=headers,
        files={"file": ("audio.wav", f, "audio/wav")},
        data={"model": "{{MODEL}}", "temperature": "0"},
        timeout=120,
    )
r.raise_for_status()
print("Transcript:", r.json()["text"])
