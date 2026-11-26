#!/usr/bin/env bash
# scripts/tts-facts.sh
# Read-only pod diagnostic for local-tts (Kokoro) threading, idle CPU, synthesis cost, and concurrency.
# Refuses to run when ENVIRONMENT=production. Never prints secret values, never kills or restarts anything.

set -uo pipefail

ENV_FILE="${ENV_FILE:-.env}"
DATA_DIR="${DATA_DIR:-data}"
OUT_DIR="${DATA_DIR}/investigation"
OUT_JSON="${OUT_DIR}/tts_facts.json"
TTS_PORT="${TTS_PORT:-8880}"
IDLE_SAMPLE_S="${IDLE_SAMPLE_S:-20}"

env_mode="${ENVIRONMENT:-}"
if [ -z "${env_mode}" ] && [ -f "${ENV_FILE}" ]; then
    env_mode="$(grep -m1 '^ENVIRONMENT=' "${ENV_FILE}" 2>/dev/null | cut -d= -f2- | tr -d '"' | tr -d "'" || true)"
fi
if [ "${env_mode}" = "production" ]; then
    echo "ERROR: Refusing to run scripts/tts-facts.sh when ENVIRONMENT=production." >&2
    exit 1
fi

PID_FILE=""
for cand in ".run/local-tts.pid" ".pids/local-tts.pid"; do
    if [ -f "${cand}" ]; then
        PID_FILE="${cand}"
        break
    fi
done

TTS_PID=""
if [ -n "${PID_FILE}" ]; then
    TTS_PID="$(cat "${PID_FILE}" 2>/dev/null | tr -d '[:space:]' || true)"
fi

mkdir -p "${OUT_DIR}"

python3 - "${TTS_PID}" "${TTS_PORT}" "${IDLE_SAMPLE_S}" "${OUT_JSON}" "${ENV_FILE}" <<'PYEOF'
import json
import os
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

tts_pid_raw, tts_port, idle_s_raw, out_json, env_file = sys.argv[1:6]
tts_pid = int(tts_pid_raw) if tts_pid_raw.isdigit() else None
idle_s = float(idle_s_raw)
clk_tck = float(os.sysconf(os.sysconf_names.get("SC_CLK_TCK", "SC_CLK_TCK")) if hasattr(os, "sysconf") else 100)

report = {
    "pid": tts_pid,
    "threads": None,
    "cpus_allowed_list": None,
    "thread_name_histogram": [],
    "environ_key_names": [],
    "idle_cpu": None,
    "package_versions": {},
    "single_synthesis_runs": [],
    "concurrency_runs": [],
}

def read_proc_stat_ticks(pid):
    if pid is None:
        return None
    p = Path(f"/proc/{pid}/stat")
    if not p.is_file():
        return None
    txt = p.read_text(errors="replace")
    rp = txt.rfind(")")
    fields = txt[rp + 2:].split()
    return int(fields[11]) + int(fields[12])

if tts_pid is not None:
    st_path = Path(f"/proc/{tts_pid}/status")
    if st_path.is_file():
        for ln in st_path.read_text(errors="replace").splitlines():
            if ln.startswith("Threads:"):
                report["threads"] = int(ln.split(":", 1)[1].strip())
            elif ln.startswith("Cpus_allowed_list:"):
                report["cpus_allowed_list"] = ln.split(":", 1)[1].strip()

    try:
        ps_out = subprocess.check_output(["ps", "-Lo", "comm", "-p", str(tts_pid)], text=True, stderr=subprocess.DEVNULL)
        counts = {}
        for line in ps_out.splitlines()[1:]:
            name = line.strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
        report["thread_name_histogram"] = [{"name": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: (-x[1], x[0]))]
    except Exception:
        pass

    env_path = Path(f"/proc/{tts_pid}/environ")
    if env_path.is_file():
        raw_env = env_path.read_bytes().split(b"\x00")
        keys = sorted({item.split(b"=", 1)[0].decode("utf-8", "replace") for item in raw_env if b"=" in item})
        report["environ_key_names"] = keys

    # Idle CPU over IDLE_SAMPLE_S
    t0_ticks = read_proc_stat_ticks(tts_pid)
    if t0_ticks is not None and idle_s > 0:
        t0 = time.perf_counter()
        time.sleep(idle_s)
        t1_ticks = read_proc_stat_ticks(tts_pid)
        dt = max(1e-6, time.perf_counter() - t0)
        if t1_ticks is not None:
            cpu_s = (t1_ticks - t0_ticks) / clk_tck
            report["idle_cpu"] = {
                "window_s": round(dt, 3),
                "cpu_seconds": round(cpu_s, 4),
                "avg_cores": round(cpu_s / dt, 4),
            }

# Package versions from local-tts venv if present
for py_cand in (
    Path("../local-tts/.venv/bin/python"),
    Path("local-tts/.venv/bin/python"),
    Path(".tools/local-tts/.venv/bin/python"),
):
    if py_cand.is_file():
        code = (
            "import importlib.metadata as m, json; "
            "pkgs=['torch','onnxruntime','kokoro','numpy','phonemizer']; "
            "out={}; "
            "[out.__setitem__(p, m.version(p)) for p in pkgs if __import__('importlib').util.find_spec(p) or True]"
        )
        try:
            ver_out = subprocess.check_output(
                [
                    str(py_cand),
                    "-c",
                    "import importlib.metadata as m, json\n"
                    "out={}\n"
                    "for p in ['torch','onnxruntime','kokoro','numpy','phonemizer']:\n"
                    "  try: out[p]=m.version(p)\n"
                    "  except Exception: out[p]='not-installed'\n"
                    "print(json.dumps(out))",
                ],
                text=True,
                stderr=subprocess.DEVNULL,
            )
            report["package_versions"] = json.loads(ver_out.strip())
        except Exception:
            pass
        break

# Synthesis & concurrency probes (only if :8880 is reachable)
SENTENCE = "The capital of France is Paris, a major European city for culture."
URL = f"http://127.0.0.1:{tts_port}/v1/text-to-speech/af_bella/stream"
PAYLOAD = json.dumps({"text": SENTENCE, "model_id": "kokoro"}).encode("utf-8")

def one_synth():
    req = urllib.request.Request(URL, data=PAYLOAD, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=30.0) as resp:
        return resp.read()

tts_reachable = False
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{tts_port}/v1/models", timeout=2.0) as r:
        tts_reachable = (r.status == 200)
except Exception:
    tts_reachable = False

if tts_reachable:
    for idx in range(3):
        c0 = read_proc_stat_ticks(tts_pid)
        w0 = time.perf_counter()
        audio = one_synth()
        wall = time.perf_counter() - w0
        c1 = read_proc_stat_ticks(tts_pid)
        cpu_s = ((c1 - c0) / clk_tck) if (c0 is not None and c1 is not None) else None
        audio_s = len(audio) / float(24000 * 2)
        report["single_synthesis_runs"].append({
            "run": idx + 1,
            "wall_s": round(wall, 4),
            "audio_bytes": len(audio),
            "audio_s": round(audio_s, 4),
            "cpu_seconds": round(cpu_s, 4) if cpu_s is not None else None,
            "cpu_per_audio_s": round(cpu_s / audio_s, 4) if (cpu_s is not None and audio_s > 0) else None,
        })

    for n in (1, 2, 4):
        c0 = read_proc_stat_ticks(tts_pid)
        w0 = time.perf_counter()
        threads = [threading.Thread(target=one_synth) for _ in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        wall = time.perf_counter() - w0
        c1 = read_proc_stat_ticks(tts_pid)
        cpu_s = ((c1 - c0) / clk_tck) if (c0 is not None and c1 is not None) else None
        report["concurrency_runs"].append({
            "concurrency": n,
            "wall_s": round(wall, 4),
            "cpu_seconds": round(cpu_s, 4) if cpu_s is not None else None,
        })

Path(out_json).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

print("=== TTS FACTS SUMMARY ===")
print(f"PID: {report['pid']} | Threads: {report['threads']} | Cpus_allowed_list: {report['cpus_allowed_list']}")
print(f"Environ keys count: {len(report['environ_key_names'])}")
print(f"Package versions: {report['package_versions']}")
print(f"Idle CPU: {report['idle_cpu']}")
print(f"Single synth runs: {len(report['single_synthesis_runs'])}")
for r in report["single_synthesis_runs"]:
    print(f"  run={r['run']} wall={r['wall_s']}s audio={r['audio_s']}s cpu={r['cpu_seconds']}s cpu/audio={r['cpu_per_audio_s']}")
print(f"Concurrency runs: {len(report['concurrency_runs'])}")
for c in report["concurrency_runs"]:
    print(f"  n={c['concurrency']} wall={c['wall_s']}s cpu={c['cpu_seconds']}s")
print(f"Saved: {out_json}")
PYEOF
