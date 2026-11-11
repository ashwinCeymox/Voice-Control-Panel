#!/usr/bin/env bash

echo "=== Stopping Native Engines ==="

PID_8880=$(lsof -ti :8880 2>/dev/null || true)
if [ -n "$PID_8880" ]; then
    echo "Stopping local-tts on :8880 (PID $PID_8880)..."
    kill $PID_8880 2>/dev/null || true
    echo "✓ local-tts stopped"
else
    echo "• local-tts not running"
fi

PID_8080=$(lsof -ti :8080 2>/dev/null || true)
if [ -n "$PID_8080" ]; then
    echo "Stopping LocalAI on :8080 (PID $PID_8080)..."
    kill $PID_8080 2>/dev/null || true
    echo "✓ LocalAI stopped"
else
    echo "• LocalAI not running"
fi

echo "=== Done ==="
