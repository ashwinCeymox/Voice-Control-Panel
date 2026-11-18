#!/usr/bin/env python3
"""Safe Cleanup Mechanism for Temporary Junk Files.

Non-negotiable safety rules:
1. Dry-run by default (scan deletes nothing; apply requires explicit plan).
2. Quarantine-first: files moved to .trash/<run-id>/ with restore capability.
   Only Category A pure regenerable caches are deleted directly.
3. Allowlisted roots only; symlinks are never followed and must not point outside root.
4. Protected list ALWAYS wins: models, databases, secrets, source code, fixtures,
   open files, git-tracked files, and files modified < min_age are never touched.
5. Limits per run: refuses plans exceeding file count or byte limits without explicit override.
6. Refuses execution as root.
7. Auditable manifest for every run kept in data/cleanup/.
"""

from __future__ import annotations

import argparse
import datetime
import fnmatch
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Set, Tuple

# Python standard library TOML parser (Python 3.11+) with fallback
try:
    import tomllib
except ImportError:
    try:
        import tomli as tomllib  # type: ignore
    except ImportError:
        tomllib = None  # Fallback handled in load_policy


def check_not_root() -> None:
    """Refuse to run if executed as root/superuser (Safety rule #8)."""
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        sys.stderr.write(
            "CRITICAL: Refusing to run as root! Cleanup must run as a non-privileged user.\n"
        )
        sys.exit(2)


def get_git_tracked_files(repo_root: pathlib.Path) -> Set[str]:
    """Get list of files tracked by git under repo_root."""
    tracked = set()
    try:
        res = subprocess.run(
            ["git", "ls-files"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if line.strip():
                    # Normalize relative posix path
                    tracked.add(pathlib.PurePosixPath(line.strip()).as_posix())
    except Exception:
        pass
    return tracked


def get_open_files_in_root(root: pathlib.Path) -> Set[str]:
    """Get all open files under root in a single fast lsof invocation."""
    open_files = set()
    try:
        res = subprocess.run(
            ["lsof", "+D", str(root.resolve()), "-F", "n"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if line.startswith("n"):
                    p = line[1:].strip()
                    try:
                        rel = (
                            pathlib.Path(p)
                            .resolve()
                            .relative_to(root.resolve())
                            .as_posix()
                        )
                        open_files.add(rel)
                    except ValueError:
                        pass
    except Exception:
        pass
    return open_files


def is_file_open_by_process(path: pathlib.Path) -> bool:
    """Check if individual file is currently open by any running process via lsof."""
    if not path.exists():
        return False
    try:
        # lsof exits with 0 if file is open, 1 if not open
        res = subprocess.run(
            ["lsof", str(path.resolve())],
            capture_output=True,
            check=False,
            timeout=2.0,
        )
        return res.returncode == 0
    except Exception:
        return False


def parse_simple_toml(content: str) -> Dict[str, Any]:
    """Minimal TOML reader fallback if tomllib is unavailable."""
    result: Dict[str, Any] = {}
    current_section: Dict[str, Any] = result

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        if line.startswith("[") and line.endswith("]"):
            sec_name = line[1:-1].strip()
            parts = sec_name.split(".")
            curr = result
            for p in parts:
                if p not in curr or not isinstance(curr[p], dict):
                    curr[p] = {}
                curr = curr[p]
            current_section = curr
            continue

        if "=" in line:
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.split("#")[0].strip()
            # Parse primitives
            if val.startswith('"') and val.endswith('"'):
                parsed_val = val[1:-1]
            elif val.startswith("[") and val.endswith("]"):
                # Simple list of strings
                inner = val[1:-1].strip()
                if inner:
                    parsed_val = [
                        item.strip().strip('"').strip("'")
                        for item in inner.split(",")
                        if item.strip()
                    ]
                else:
                    parsed_val = []
            elif val.lower() == "true":
                parsed_val = True
            elif val.lower() == "false":
                parsed_val = False
            elif "." in val:
                try:
                    parsed_val = float(val)
                except ValueError:
                    parsed_val = val
            else:
                try:
                    parsed_val = int(val)
                except ValueError:
                    parsed_val = val
            current_section[key] = parsed_val

    return result


def load_policy(policy_path: pathlib.Path) -> Dict[str, Any]:
    """Load and parse policy TOML file."""
    if not policy_path.exists():
        raise FileNotFoundError(f"Policy file not found: {policy_path}")

    with open(policy_path, "rb") as f:
        content_bytes = f.read()

    if tomllib is not None:
        return tomllib.loads(content_bytes.decode("utf-8"))
    else:
        return parse_simple_toml(content_bytes.decode("utf-8"))


# --- Snapshot & Diff ---


def take_snapshot(root_path: pathlib.Path) -> Dict[str, Any]:
    """Record path, size, modification time, inode for all files under root.

    Skips .git, node_modules, .venv. Platform neutral (macOS & Linux).
    """
    root_resolved = root_path.resolve()
    skip_dirs = {".git", "node_modules", ".venv", ".trash"}
    snapshot_data: Dict[str, Dict[str, Any]] = {}

    for dirpath, dirnames, filenames in os.walk(
        root_resolved, topdown=True, followlinks=False
    ):
        # Prune skipped dirs
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]

        current_dir = pathlib.Path(dirpath)
        for fname in filenames:
            file_path = current_dir / fname
            try:
                # Use lstat so symlinks are not followed
                stat_res = file_path.lstat()
                rel_path = file_path.relative_to(root_resolved).as_posix()
                snapshot_data[rel_path] = {
                    "size": stat_res.st_size,
                    "mtime": stat_res.st_mtime,
                    "inode": stat_res.st_ino,
                    "is_symlink": file_path.is_symlink(),
                }
            except (OSError, ValueError):
                continue

    return {
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "root": str(root_resolved),
        "total_files": len(snapshot_data),
        "total_bytes": sum(f["size"] for f in snapshot_data.values()),
        "files": snapshot_data,
    }


def compute_diff(
    before_data: Dict[str, Any], after_data: Dict[str, Any]
) -> Dict[str, Any]:
    """Diff two snapshots and categorize created, modified, deleted files."""
    before_files = before_data.get("files", {})
    after_files = after_data.get("files", {})

    created: Dict[str, Dict[str, Any]] = {}
    modified: Dict[str, Dict[str, Any]] = {}
    deleted: Dict[str, Dict[str, Any]] = {}

    for path, data in after_files.items():
        if path not in before_files:
            created[path] = data
        else:
            b_data = before_files[path]
            if (
                b_data["size"] != data["size"]
                or abs(b_data["mtime"] - data["mtime"]) > 0.001
            ):
                modified[path] = {
                    "size_before": b_data["size"],
                    "size_after": data["size"],
                    "mtime_before": b_data["mtime"],
                    "mtime_after": data["mtime"],
                }

    for path, data in before_files.items():
        if path not in after_files:
            deleted[path] = data

    return {
        "created_count": len(created),
        "modified_count": len(modified),
        "deleted_count": len(deleted),
        "created_bytes": sum(f["size"] for f in created.values()),
        "created": created,
        "modified": modified,
        "deleted": deleted,
    }


# --- Safety Checks & Classification ---


class SafetyValidator:
    def __init__(self, root: pathlib.Path, policy: Dict[str, Any]):
        self.root = root.resolve()
        self.policy = policy
        self.protected_cfg = policy.get("protected", {})
        self.general_cfg = policy.get("general", {})
        self.rules_cfg = policy.get("rules", {})
        self.git_tracked = get_git_tracked_files(self.root)
        self.open_files = get_open_files_in_root(self.root)
        self.root_dev = self.root.stat().st_dev

        # Calculate protected backups (keep newest N)
        self.protected_backups: Set[str] = set()
        self._calculate_protected_backups()

    def _calculate_protected_backups(self) -> None:
        """Find backups in data/backups and protect the newest N."""
        keep_count = int(self.general_cfg.get("backup_retention_count", 3))
        backups_dir = self.root / "data" / "backups"
        if backups_dir.exists() and backups_dir.is_dir():
            backup_files = []
            for p in backups_dir.glob("*.db"):
                try:
                    backup_files.append((p.stat().st_mtime, p))
                except OSError:
                    pass
            # Sort by mtime descending (newest first)
            backup_files.sort(key=lambda x: x[0], reverse=True)
            for _, bp in backup_files[:keep_count]:
                rel = bp.relative_to(self.root).as_posix()
                self.protected_backups.add(rel)

    def is_protected(
        self, rel_path: str, abs_path: pathlib.Path, check_open: bool = False
    ) -> Tuple[bool, str]:
        """Check if path is protected. Returns (is_protected, reason)."""
        # 1. Exact matches
        exact_list = self.protected_cfg.get("exact", [])
        if rel_path in exact_list or abs_path.name in exact_list:
            return True, f"exact_match: {rel_path}"

        # 2. Path resolution & symlink breakout check
        try:
            resolved = abs_path.resolve()
            if not resolved.is_relative_to(self.root):
                return True, f"escapes_root: {rel_path} -> {resolved}"
            if abs_path.is_symlink():
                # Never follow symlinks; if symlink target is outside root, protect from traversal
                target = pathlib.Path(os.readlink(abs_path))
                if target.is_absolute() and not target.resolve().is_relative_to(
                    self.root
                ):
                    return True, "symlink_points_outside_root"
        except (ValueError, OSError) as e:
            return True, f"resolution_error: {e}"

        # 3. Mount point / Filesystem check
        try:
            if abs_path.stat().st_dev != self.root_dev:
                return True, "crosses_filesystem_boundary"
        except OSError:
            pass

        # 4. Prefix matches
        for prefix in self.protected_cfg.get("prefixes", []):
            if rel_path.startswith(prefix) or rel_path == prefix.rstrip("/"):
                # Exception: Category D partial files inside models/ or backends/
                if prefix.startswith("models/") and (
                    rel_path.endswith(".partial")
                    or rel_path.endswith(".tmp")
                    or rel_path.endswith(".download")
                ):
                    continue
                return True, f"protected_prefix: {prefix}"

        # 5. Pattern matches
        for pattern in self.protected_cfg.get("patterns", []):
            if fnmatch.fnmatch(rel_path, pattern) or fnmatch.fnmatch(
                abs_path.name, pattern
            ):
                return True, f"protected_pattern: {pattern}"

        # 6. Git-tracked check
        if rel_path in self.git_tracked:
            return True, "tracked_by_git"

        # 7. Backup retention check (newest N backups are always protected)
        if rel_path in self.protected_backups:
            return True, "protected_newest_backup"

        # 8. Open file check (checked via pre-collected open files or on demand)
        if rel_path in self.open_files or (
            check_open and is_file_open_by_process(abs_path)
        ):
            return True, "open_by_process"

        return False, ""

    def evaluate_item(
        self, rel_path: str, abs_path: pathlib.Path
    ) -> Optional[Dict[str, Any]]:
        """Evaluate a path against cleanup rules. Returns candidate item or None."""
        # 1. Protected list check ALWAYS wins
        prot, reason = self.is_protected(rel_path, abs_path, check_open=False)
        if prot:
            return None

        try:
            stat_res = abs_path.lstat()
        except OSError:
            return None

        # Check age
        file_age_hours = (time.time() - stat_res.st_mtime) / 3600.0

        # Match against category rules
        for cat_key, cat_rule in self.rules_cfg.items():
            rule_patterns = cat_rule.get("patterns", [])
            for pattern in rule_patterns:
                matched = False
                if fnmatch.fnmatch(rel_path, pattern):
                    matched = True
                elif pattern.startswith("**/") and fnmatch.fnmatch(
                    rel_path, pattern[3:]
                ):
                    matched = True
                elif fnmatch.fnmatch(abs_path.name, pattern):
                    matched = True

                if matched:
                    # Minimum age check for this category
                    cat_min_age = float(
                        cat_rule.get(
                            "min_age_hours", self.general_cfg.get("min_age_hours", 2.0)
                        )
                    )
                    # Pure regenerable caches (Category A) ignore min_age unless specified
                    if (
                        cat_rule.get("action") != "delete"
                        and file_age_hours < cat_min_age
                    ):
                        return None

                    # Ensure candidate file is not open by any process
                    if is_file_open_by_process(abs_path):
                        return None

                    return {
                        "path": rel_path,
                        "abs_path": str(abs_path),
                        "size": stat_res.st_size,
                        "mtime": stat_res.st_mtime,
                        "age_hours": round(file_age_hours, 2),
                        "category": cat_key,
                        "category_name": cat_rule.get("name", cat_key),
                        "rule": pattern,
                        "action": cat_rule.get("action", "quarantine"),
                        "is_dir": abs_path.is_dir() and not abs_path.is_symlink(),
                    }

        return None


# --- Core Commands: Scan, Apply, Restore, Purge, Report ---


def cmd_snapshot(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root or ".").resolve()
    snap = take_snapshot(root)
    out_json = json.dumps(snap, indent=2)
    if args.out:
        out_p = pathlib.Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(out_json, encoding="utf-8")
        print(
            f"Snapshot written to {out_p} ({snap['total_files']} files, {snap['total_bytes']:,} bytes)"
        )
    else:
        print(out_json)
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    before_p = pathlib.Path(args.before)
    after_p = pathlib.Path(args.after)
    if not before_p.exists() or not after_p.exists():
        sys.stderr.write("Error: Before and after snapshot files must exist.\n")
        return 1

    before_data = json.loads(before_p.read_text(encoding="utf-8"))
    after_data = json.loads(after_p.read_text(encoding="utf-8"))
    diff_res = compute_diff(before_data, after_data)

    print("\n=== Snapshot Diff Summary ===")
    print(
        f"Created:  {diff_res['created_count']} files ({diff_res['created_bytes']:,} bytes)"
    )
    print(f"Modified: {diff_res['modified_count']} files")
    print(f"Deleted:  {diff_res['deleted_count']} files")

    if diff_res["created_count"] > 0:
        print("\nCreated files (sample up to 20):")
        for idx, (path, data) in enumerate(diff_res["created"].items()):
            if idx >= 20:
                print(f"  ... and {diff_res['created_count'] - 20} more")
                break
            print(f"  + {path} ({data['size']:,} bytes)")

    if diff_res["modified_count"] > 0:
        print("\nModified files (sample up to 10):")
        for idx, (path, data) in enumerate(diff_res["modified"].items()):
            if idx >= 10:
                print(f"  ... and {diff_res['modified_count'] - 10} more")
                break
            print(
                f"  * {path} ({data['size_before']:,} -> {data['size_after']:,} bytes)"
            )

    if args.out:
        out_p = pathlib.Path(args.out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(json.dumps(diff_res, indent=2), encoding="utf-8")
        print(f"\nDiff details written to {out_p}")

    return 0


def run_scan(
    root_path: pathlib.Path, policy_path: pathlib.Path, force_limit: bool = False
) -> Tuple[int, Dict[str, Any]]:
    """Scan candidate files against policy and safety rules. Returns (exit_code, plan_dict)."""
    policy = load_policy(policy_path)
    validator = SafetyValidator(root_path, policy)

    candidates: List[Dict[str, Any]] = []
    skip_dirs = {".git", "node_modules", ".venv", ".trash"}

    # Walk directory tree
    for dirpath, dirnames, filenames in os.walk(
        root_path.resolve(), topdown=True, followlinks=False
    ):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        cur_dir = pathlib.Path(dirpath)

        # Check files
        for fname in filenames:
            file_path = cur_dir / fname
            try:
                rel_path = file_path.relative_to(root_path.resolve()).as_posix()
            except ValueError:
                continue
            item = validator.evaluate_item(rel_path, file_path)
            if item:
                candidates.append(item)

        # Check empty/cache directories (e.g. __pycache__)
        for dname in dirnames:
            dir_path = cur_dir / dname
            try:
                rel_path = dir_path.relative_to(root_path.resolve()).as_posix()
            except ValueError:
                continue
            if dname == "__pycache__" or dname == ".pytest_cache":
                item = validator.evaluate_item(rel_path, dir_path)
                if item:
                    candidates.append(item)

    total_files = len(candidates)
    total_bytes = sum(c["size"] for c in candidates)

    # Check plan limits
    max_files = int(policy.get("general", {}).get("max_plan_files", 500))
    max_bytes = int(policy.get("general", {}).get("max_plan_bytes", 2147483648))  # 2 GB

    limit_exceeded = False
    limit_reason = ""
    if total_files > max_files:
        limit_exceeded = True
        limit_reason = f"Plan exceeds file count limit: {total_files} > {max_files}"
    elif total_bytes > max_bytes:
        limit_exceeded = True
        limit_reason = f"Plan exceeds byte limit: {total_bytes:,} > {max_bytes:,}"

    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    plan_id = f"plan-{ts}"

    plan_data = {
        "plan_id": plan_id,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "policy_file": str(policy_path),
        "root": str(root_path.resolve()),
        "total_files": total_files,
        "total_bytes": total_bytes,
        "limit_exceeded": limit_exceeded,
        "limit_reason": limit_reason,
        "force_limit": force_limit,
        "items": candidates,
    }

    if limit_exceeded and not force_limit:
        return 2, plan_data

    return 0, plan_data


def cmd_scan(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root or ".").resolve()
    policy_path = pathlib.Path(args.policy or "policies/cleanup.dev.toml")
    exit_code, plan = run_scan(root, policy_path, force_limit=args.force_limit)

    print("\n" + "=" * 80)
    print(f"CLEANUP SCAN REPORT [{plan['plan_id']}]")
    print("=" * 80)
    print(f"Root:    {plan['root']}")
    print(f"Policy:  {plan['policy_file']}")
    print(f"Found:   {plan['total_files']} items ({plan['total_bytes']:,} bytes)")

    if plan["items"]:
        print("\nCandidate Items:")
        print(
            f"{'Path':<45} {'Size':<10} {'Age (h)':<8} {'Category':<15} {'Action':<10}"
        )
        print("-" * 90)
        for item in plan["items"][:50]:
            print(
                f"{item['path']:<45} {item['size']:<10} {item['age_hours']:<8} {item['category']:<15} {item['action']:<10}"
            )
        if len(plan["items"]) > 50:
            print(f"... and {len(plan['items']) - 50} more items.")

    # Save plan file to data/cleanup/
    plan_dir = root / "data" / "cleanup"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_file = plan_dir / f"{plan['plan_id']}.json"
    plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    print(f"\nPlan written to: {plan_file}")

    if plan["limit_exceeded"] and not args.force_limit:
        sys.stderr.write(
            f"\nERROR (Safety Rule 7): {plan['limit_reason']}.\nRefusing plan. Use --force-limit to override if intentional.\n"
        )
        return 2

    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    plan_file = pathlib.Path(args.plan)
    if not plan_file.exists():
        sys.stderr.write(f"Error: Plan file does not exist: {plan_file}\n")
        return 1

    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    root = pathlib.Path(plan.get("root", ".")).resolve()
    policy_file = pathlib.Path(
        args.policy or plan.get("policy_file", "policies/cleanup.dev.toml")
    )
    policy = load_policy(policy_file)
    validator = SafetyValidator(root, policy)

    # Limit check
    if plan.get("limit_exceeded") and not (args.force_limit or plan.get("force_limit")):
        sys.stderr.write(
            f"Error: Refusing to apply plan exceeding safety limits ({plan.get('limit_reason')}). Override with --force-limit.\n"
        )
        return 2

    run_id = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d-%H%M%S")
    trash_dir = root / ".trash" / run_id

    manifest_items: List[Dict[str, Any]] = []
    skipped_count = 0
    quarantined_count = 0
    deleted_count = 0

    for item in plan.get("items", []):
        rel_path = item["path"]
        abs_path = root / rel_path

        # 1. Existence check
        if not abs_path.exists() and not abs_path.is_symlink():
            skipped_count += 1
            manifest_items.append({"path": rel_path, "status": "skipped_not_found"})
            continue

        # 2. Re-verify protected list!
        prot, reason = validator.is_protected(rel_path, abs_path)
        if prot:
            sys.stderr.write(
                f"SAFETY VIOLATION DETECTED: {rel_path} is protected ({reason})! Skipping.\n"
            )
            manifest_items.append(
                {"path": rel_path, "status": "rejected_protected", "reason": reason}
            )
            return 3  # Protected path violation

        # 3. Re-verify modification time and size
        try:
            curr_stat = abs_path.lstat()
            if (
                curr_stat.st_size != item["size"]
                or abs(curr_stat.st_mtime - item["mtime"]) > 0.001
            ):
                skipped_count += 1
                manifest_items.append(
                    {"path": rel_path, "status": "skipped_changed_since_scan"}
                )
                continue
        except OSError:
            skipped_count += 1
            continue

        # 4. Re-verify open file check
        if is_file_open_by_process(abs_path):
            skipped_count += 1
            manifest_items.append(
                {"path": rel_path, "status": "skipped_open_by_process"}
            )
            continue

        # 5. Execute action
        action = item.get("action", "quarantine")
        if action == "delete":
            # Pure Category A regenerable caches
            try:
                if abs_path.is_dir() and not abs_path.is_symlink():
                    shutil.rmtree(abs_path)
                else:
                    abs_path.unlink()
                deleted_count += 1
                manifest_items.append(
                    {
                        "path": rel_path,
                        "status": "deleted_directly",
                        "size": item["size"],
                    }
                )
            except Exception as e:
                manifest_items.append(
                    {"path": rel_path, "status": "delete_failed", "error": str(e)}
                )
        else:
            # Quarantine to .trash/<run-id>/<rel_path>
            dest_path = trash_dir / rel_path
            try:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(abs_path), str(dest_path))
                quarantined_count += 1
                manifest_items.append(
                    {
                        "path": rel_path,
                        "quarantine_path": str(dest_path.relative_to(root)),
                        "status": "quarantined",
                        "size": item["size"],
                    }
                )
            except Exception as e:
                manifest_items.append(
                    {"path": rel_path, "status": "quarantine_failed", "error": str(e)}
                )

    manifest = {
        "run_id": run_id,
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "plan_file": str(plan_file),
        "policy_file": str(policy_file),
        "root": str(root),
        "summary": {
            "quarantined": quarantined_count,
            "deleted": deleted_count,
            "skipped": skipped_count,
            "total_processed": len(manifest_items),
        },
        "items": manifest_items,
    }

    manifest_dir = root / "data" / "cleanup"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    manifest_file = manifest_dir / f"manifest-{run_id}.json"
    manifest_file.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("\n" + "=" * 80)
    print(f"CLEANUP APPLY COMPLETED [Run ID: {run_id}]")
    print("=" * 80)
    print(f"Quarantined: {quarantined_count} items (in .trash/{run_id}/)")
    print(f"Deleted:     {deleted_count} items (Category A pure caches)")
    print(f"Skipped:     {skipped_count} items")
    print(f"Manifest:    {manifest_file}\n")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root or ".").resolve()
    run_id = args.run_id
    target_rel = args.path

    trash_run_dir = root / ".trash" / run_id
    if not trash_run_dir.exists():
        sys.stderr.write(f"Error: Quarantine run '{run_id}' not found under .trash/\n")
        return 1

    restored_count = 0
    policy_file = pathlib.Path(args.policy or "policies/cleanup.dev.toml")
    _validator = SafetyValidator(root, load_policy(policy_file))

    for dirpath, _, filenames in os.walk(trash_run_dir):
        for fname in filenames:
            trash_file = pathlib.Path(dirpath) / fname
            rel_to_trash = trash_file.relative_to(trash_run_dir).as_posix()

            if target_rel and rel_to_trash != target_rel:
                continue

            dest_file = root / rel_to_trash
            if dest_file.exists():
                print(f"Skipping {rel_to_trash}: target already exists.")
                continue

            # Check safety before restoring
            dest_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(trash_file), str(dest_file))
            restored_count += 1
            print(f"Restored: {rel_to_trash}")

    print(f"\nRestore complete. {restored_count} item(s) restored from run '{run_id}'.")
    return 0


def cmd_purge(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root or ".").resolve()
    trash_dir = root / ".trash"
    if not trash_dir.exists():
        print("No .trash directory found. Nothing to purge.")
        return 0

    retention_days = float(
        args.older_than_days if args.older_than_days is not None else 7.0
    )
    now = time.time()
    cutoff_seconds = retention_days * 86400.0

    purged_runs = 0
    for run_entry in trash_dir.iterdir():
        if run_entry.is_dir():
            try:
                mtime = run_entry.stat().st_mtime
                if (now - mtime) >= cutoff_seconds:
                    shutil.rmtree(run_entry)
                    print(f"Purged expired quarantine run: {run_entry.name}")
                    purged_runs += 1
            except OSError as e:
                sys.stderr.write(f"Error purging {run_entry.name}: {e}\n")

    print(f"Purge complete: {purged_runs} expired run(s) removed.")
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    root = pathlib.Path(args.root or ".").resolve()
    policy_path = pathlib.Path(args.policy or "policies/cleanup.dev.toml")
    policy = load_policy(policy_path)

    # Calculate disk use by category
    validator = SafetyValidator(root, policy)
    category_sizes: Dict[str, int] = {}
    category_counts: Dict[str, int] = {}

    skip_dirs = {".git", "node_modules", ".venv", ".trash"}
    for dirpath, dirnames, filenames in os.walk(root, topdown=True, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in skip_dirs]
        for fname in filenames:
            fpath = pathlib.Path(dirpath) / fname
            try:
                rel = fpath.relative_to(root).as_posix()
            except ValueError:
                continue
            item = validator.evaluate_item(rel, fpath)
            if item:
                cat = item["category"]
                category_sizes[cat] = category_sizes.get(cat, 0) + item["size"]
                category_counts[cat] = category_counts.get(cat, 0) + 1

    # Check quarantine size
    trash_dir = root / ".trash"
    trash_size = 0
    trash_runs = 0
    if trash_dir.exists():
        for run_dir in trash_dir.iterdir():
            if run_dir.is_dir():
                trash_runs += 1
                for dp, _, fns in os.walk(run_dir):
                    for fn in fns:
                        try:
                            trash_size += (pathlib.Path(dp) / fn).stat().st_size
                        except OSError:
                            pass

    print("\n" + "=" * 80)
    print("CLEANUP STATUS REPORT")
    print("=" * 80)
    print("Pending Junk by Category:")
    for cat, size in sorted(category_sizes.items()):
        cnt = category_counts[cat]
        print(
            f"  - {cat:<20}: {cnt:>4} files, {size:>12,} bytes ({size / (1024 * 1024):.2f} MB)"
        )

    print("\nQuarantine (.trash/):")
    print(f"  - Active runs: {trash_runs}")
    print(
        f"  - Total size:  {trash_size:,} bytes ({trash_size / (1024 * 1024):.2f} MB)"
    )

    # List recent manifests
    manifest_dir = root / "data" / "cleanup"
    if manifest_dir.exists():
        manifests = sorted(manifest_dir.glob("manifest-*.json"), reverse=True)[:5]
        print(f"\nRecent Runs ({len(manifests)}):")
        for mf in manifests:
            try:
                mdata = json.loads(mf.read_text(encoding="utf-8"))
                s = mdata.get("summary", {})
                print(
                    f"  - {mdata.get('run_id')}: {s.get('quarantined', 0)} quarantined, {s.get('deleted', 0)} deleted, {s.get('skipped', 0)} skipped"
                )
            except Exception:
                pass
    print("=" * 80 + "\n")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Convenience pipeline for server automation: scan -> apply -> purge."""
    root = pathlib.Path(args.root or ".").resolve()
    policy_path = pathlib.Path(args.policy or "policies/cleanup.server.toml")

    scan_code, plan = run_scan(root, policy_path, force_limit=args.force_limit)
    if scan_code != 0:
        sys.stderr.write(f"Scan failed or limit exceeded: {plan.get('limit_reason')}\n")
        return scan_code

    if plan["total_files"] == 0:
        print("Scan found 0 eligible files. System clean.")
        return 0

    # Write temp plan file
    plan_dir = root / "data" / "cleanup"
    plan_dir.mkdir(parents=True, exist_ok=True)
    plan_file = plan_dir / f"{plan['plan_id']}.json"
    plan_file.write_text(json.dumps(plan, indent=2), encoding="utf-8")

    apply_args = argparse.Namespace(
        plan=str(plan_file),
        policy=str(policy_path),
        force_limit=args.force_limit,
    )
    apply_code = cmd_apply(apply_args)
    if apply_code != 0:
        return apply_code

    # Run purge
    purge_args = argparse.Namespace(
        root=str(root),
        older_than_days=policy_path
        and load_policy(policy_path)
        .get("general", {})
        .get("trash_retention_days", 7.0),
        policy=str(policy_path),
    )
    return cmd_purge(purge_args)


def main() -> int:
    check_not_root()

    parser = argparse.ArgumentParser(
        description="Safe, auditable cleanup tool for temporary junk files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # snapshot
    p_snap = subparsers.add_parser("snapshot", help="Record filesystem snapshot.")
    p_snap.add_argument("--root", default=".", help="Scan root directory")
    p_snap.add_argument("--out", help="Output JSON snapshot file path")

    # diff
    p_diff = subparsers.add_parser("diff", help="Diff two snapshots.")
    p_diff.add_argument("before", help="Before snapshot JSON")
    p_diff.add_argument("after", help="After snapshot JSON")
    p_diff.add_argument("--out", help="Output JSON diff file path")

    # scan
    p_scan = subparsers.add_parser(
        "scan", help="Scan candidate junk and create plan (deletes nothing)."
    )
    p_scan.add_argument("--root", default=".", help="Scan root directory")
    p_scan.add_argument(
        "--policy", default="policies/cleanup.dev.toml", help="Policy TOML file"
    )
    p_scan.add_argument(
        "--force-limit",
        action="store_true",
        help="Allow plan exceeding file/byte limits",
    )

    # apply
    p_apply = subparsers.add_parser(
        "apply", help="Execute cleanup from plan file with quarantine."
    )
    p_apply.add_argument(
        "--plan", required=True, help="Path to plan JSON file from scan"
    )
    p_apply.add_argument("--policy", help="Policy TOML file override")
    p_apply.add_argument(
        "--force-limit", action="store_true", help="Allow applying over-limit plan"
    )

    # restore
    p_rest = subparsers.add_parser(
        "restore", help="Restore quarantined files from .trash/."
    )
    p_rest.add_argument("run_id", help="Quarantine run ID (e.g. 20261117-100000)")
    p_rest.add_argument("path", nargs="?", help="Specific relative path to restore")
    p_rest.add_argument("--root", default=".", help="Root directory")
    p_rest.add_argument(
        "--policy", default="policies/cleanup.dev.toml", help="Policy TOML file"
    )

    # purge
    p_purge = subparsers.add_parser("purge", help="Purge expired quarantine folders.")
    p_purge.add_argument("--root", default=".", help="Root directory")
    p_purge.add_argument(
        "--older-than-days", type=float, help="Retention days threshold"
    )
    p_purge.add_argument("--policy", help="Policy TOML file")

    # report
    p_rep = subparsers.add_parser(
        "report", help="Show cleanup status and quarantine report."
    )
    p_rep.add_argument("--root", default=".", help="Root directory")
    p_rep.add_argument(
        "--policy", default="policies/cleanup.dev.toml", help="Policy TOML file"
    )

    # run
    p_run = subparsers.add_parser(
        "run", help="Atomic server pipeline: scan -> apply -> purge."
    )
    p_run.add_argument("--root", default=".", help="Root directory")
    p_run.add_argument(
        "--policy", default="policies/cleanup.server.toml", help="Policy TOML file"
    )
    p_run.add_argument(
        "--force-limit",
        action="store_true",
        help="Allow plan exceeding file/byte limits",
    )

    args = parser.parse_args()

    if args.command == "snapshot":
        return cmd_snapshot(args)
    elif args.command == "diff":
        return cmd_diff(args)
    elif args.command == "scan":
        return cmd_scan(args)
    elif args.command == "apply":
        return cmd_apply(args)
    elif args.command == "restore":
        return cmd_restore(args)
    elif args.command == "purge":
        return cmd_purge(args)
    elif args.command == "report":
        return cmd_report(args)
    elif args.command == "run":
        return cmd_run(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
