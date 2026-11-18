# Agent Work Guidelines & Scratch Hygiene

## 1. Scratch and Temporary Files
- **Never write scratch files to the repository root.**
- All temporary scripts, output files, debug dumps, and intermediate test artifacts **MUST** be placed in `tmp/agent/<task-id>/` (which is gitignored).
- Always clean up or delete your scratch directory during your handoff step.

## 2. Models Policy
- Use `GEMINI-3.8-HIGH-flash` for simple to moderate tasks.
- Use `SONNET-4.6` for complex architecture and critical reasoning tasks.

## 3. Preserved & Protected Assets
- Never modify or delete:
  - Database files (`voice_platform.db`, `*.db-wal`, `*.db-shm`)
  - Environment and secrets (`.env*`, `*.pem`, `*.key`)
  - Model weights (`models/**`)
  - Fixtures and benchmark baselines (`tests/fixtures/**`)
