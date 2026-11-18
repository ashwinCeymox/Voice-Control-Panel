import httpx

url = "http://127.0.0.1:8000/v1/audio/transcriptions"
headers = {"Authorization": f"va-aweeANQeHVoahdKcKTgXHX8zDBqgD0FJw_OCmLKmWnA"}

with open("audio.wav", "rb") as f:
    resp = httpx.post(
        url,
        headers=headers,
        files={"file": ("audio.wav", f, "audio/wav")},
        data={"model": "whisper-base-en", "temperature": "0"},
        timeout=120,
    )
resp.raise_for_status()
print("Transcript:", resp.json()["text"])


