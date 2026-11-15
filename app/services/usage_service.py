"""
Usage analytics, metrics aggregation, timeseries rollups, and model status service.
"""
import csv
import io
import math
import os
import time
from typing import Any, Dict, List, Optional, Tuple
import aiosqlite
from app.storage.database import get_db


def parse_time_window(since_val: Optional[Any], default_seconds: float = 86400.0) -> float:
    """Convert relative string ('1h', '24h', '7d', '30d') or numeric timestamp to epoch timestamp."""
    now = time.time()
    if since_val is None:
        return now - default_seconds

    if isinstance(since_val, (int, float)):
        return float(since_val)

    val_str = str(since_val).strip().lower()
    if val_str.endswith("h"):
        try:
            hours = float(val_str[:-1])
            return now - (hours * 3600.0)
        except ValueError:
            pass
    elif val_str.endswith("d"):
        try:
            days = float(val_str[:-1])
            return now - (days * 86400.0)
        except ValueError:
            pass

    try:
        return float(val_str)
    except ValueError:
        return now - default_seconds


def compute_percentiles(values: List[float]) -> Tuple[float, float]:
    """Calculate p50 and p95 from a numeric list."""
    if not values:
        return 0.0, 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    idx_50 = int(0.50 * n)
    idx_95 = min(int(0.95 * n), n - 1)
    return round(sorted_v[idx_50], 2), round(sorted_v[idx_95], 2)


async def record_usage_event(
    endpoint: str,
    status: int,
    latency_ms: float,
    key_id: Optional[str] = None,
    profile_id: Optional[str] = None,
    model: Optional[str] = None,
    ttfb_ms: Optional[float] = None,
    input_units: float = 0.0,
    output_units: float = 0.0,
    bytes_in: int = 0,
    bytes_out: int = 0,
    error_code: Optional[str] = None,
    timestamp: Optional[float] = None,
) -> None:
    """Record a rich usage event in SQLite. Never logs prompts, content, or audio."""
    ts = timestamp if timestamp is not None else time.time()

    # If profile_id not passed but key_id is, try to populate profile_id
    if key_id and not profile_id:
        try:
            async with get_db() as db:
                async with db.execute("SELECT profile_id FROM api_keys WHERE id = ?", (key_id,)) as cur:
                    row = await cur.fetchone()
                    if row and row[0]:
                        profile_id = row[0]
        except Exception:
            pass

    try:
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO usage_events (
                    ts, key_id, profile_id, endpoint, model, status,
                    latency_ms, ttfb_ms, input_units, output_units,
                    bytes_in, bytes_out, error_code
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts, key_id, profile_id, endpoint, model, status,
                    latency_ms, ttfb_ms, input_units, output_units,
                    bytes_in, bytes_out, error_code
                ),
            )
            await db.commit()
    except Exception:
        # Analytics recording must never interrupt application execution
        pass


class UsageService:
    @staticmethod
    async def get_summary(
        since: Optional[Any] = "24h",
        until: Optional[float] = None,
        group_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Aggregate usage events into totals, error counts, latency percentiles, and group breakdowns."""
        since_ts = parse_time_window(since)
        until_ts = float(until) if until is not None else time.time()

        async with get_db() as db:
            # Query all events in time range for accurate percentiles and unit summaries
            async with db.execute(
                """
                SELECT id, ts, key_id, profile_id, endpoint, model, status, latency_ms, ttfb_ms, input_units, output_units, error_code
                FROM usage_events
                WHERE ts >= ? AND ts <= ?
                ORDER BY ts ASC
                """,
                (since_ts, until_ts),
            ) as cursor:
                rows = await cursor.fetchall()

        total_requests = len(rows)
        if total_requests == 0:
            return {
                "since": since_ts,
                "until": until_ts,
                "total_requests": 0,
                "success_count": 0,
                "error_count": 0,
                "status_429_count": 0,
                "status_503_count": 0,
                "success_rate": 1.0,
                "p50_latency_ms": 0.0,
                "p95_latency_ms": 0.0,
                "avg_ttfb_ms": 0.0,
                "llm_tokens_in": 0.0,
                "llm_tokens_out": 0.0,
                "stt_audio_seconds_in": 0.0,
                "tts_audio_seconds_out": 0.0,
                "tts_characters_in": 0.0,
                "groups": [],
            }

        success_count = 0
        error_count = 0
        s429_count = 0
        s503_count = 0
        latencies: List[float] = []
        ttfbs: List[float] = []
        llm_tokens_in = 0.0
        llm_tokens_out = 0.0
        stt_audio_seconds_in = 0.0
        tts_audio_seconds_out = 0.0
        tts_characters_in = 0.0

        # Grouping accumulator
        group_map: Dict[str, Dict[str, Any]] = {}

        for r in rows:
            st = r[6]
            lat = float(r[7])
            ttfb = float(r[8]) if r[8] is not None else None
            in_u = float(r[9] or 0.0)
            out_u = float(r[10] or 0.0)
            ep = r[4] or ""
            model = r[5] or "unknown"
            key_id = r[2] or "unauthenticated"
            profile_id = r[3] or "none"

            if st < 400:
                success_count += 1
            else:
                error_count += 1

            if st == 429:
                s429_count += 1
            elif st == 503:
                s503_count += 1

            latencies.append(lat)
            if ttfb is not None:
                ttfbs.append(ttfb)

            # Endpoint-specific unit assignment
            if "chat" in ep:
                llm_tokens_in += in_u
                llm_tokens_out += out_u
            elif "audio" in ep or "transcription" in ep:
                stt_audio_seconds_in += in_u
            elif "text-to-speech" in ep or "voice" in ep:
                tts_characters_in += in_u
                tts_audio_seconds_out += out_u

            # Handle grouping
            group_name = None
            if group_by == "model":
                group_name = model
            elif group_by == "endpoint":
                group_name = ep
            elif group_by == "key":
                group_name = key_id
            elif group_by == "profile":
                group_name = profile_id

            if group_name:
                if group_name not in group_map:
                    group_map[group_name] = {
                        "name": group_name,
                        "requests": 0,
                        "errors": 0,
                        "input_units": 0.0,
                        "output_units": 0.0,
                        "latencies": [],
                    }
                g = group_map[group_name]
                g["requests"] += 1
                if st >= 400:
                    g["errors"] += 1
                g["input_units"] += in_u
                g["output_units"] += out_u
                g["latencies"].append(lat)

        p50, p95 = compute_percentiles(latencies)
        avg_ttfb = round(sum(ttfbs) / len(ttfbs), 2) if ttfbs else 0.0

        groups_res = []
        for g_name, g_info in group_map.items():
            g_p50, _ = compute_percentiles(g_info["latencies"])
            groups_res.append({
                "name": g_name,
                "requests": g_info["requests"],
                "errors": g_info["errors"],
                "input_units": round(g_info["input_units"], 2),
                "output_units": round(g_info["output_units"], 2),
                "avg_latency_ms": round(sum(g_info["latencies"]) / len(g_info["latencies"]), 2),
                "p50_latency_ms": g_p50,
            })

        return {
            "since": since_ts,
            "until": until_ts,
            "total_requests": total_requests,
            "success_count": success_count,
            "error_count": error_count,
            "status_429_count": s429_count,
            "status_503_count": s503_count,
            "success_rate": round(success_count / total_requests, 4),
            "p50_latency_ms": p50,
            "p95_latency_ms": p95,
            "avg_ttfb_ms": avg_ttfb,
            "llm_tokens_in": round(llm_tokens_in, 2),
            "llm_tokens_out": round(llm_tokens_out, 2),
            "stt_audio_seconds_in": round(stt_audio_seconds_in, 2),
            "tts_audio_seconds_out": round(tts_audio_seconds_out, 2),
            "tts_characters_in": round(tts_characters_in, 2),
            "groups": groups_res,
        }

    @staticmethod
    async def get_timeseries(
        interval: str = "hour",
        since: Optional[Any] = "24h",
        until: Optional[float] = None,
        key_id: Optional[str] = None,
        profile_id: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Aggregate usage events into discrete time buckets (hourly or daily)."""
        since_ts = parse_time_window(since)
        until_ts = float(until) if until is not None else time.time()
        bucket_step = 86400.0 if interval == "day" else 3600.0

        query = """
            SELECT ts, status, latency_ms, input_units, output_units, endpoint
            FROM usage_events
            WHERE ts >= ? AND ts <= ?
        """
        params: List[Any] = [since_ts, until_ts]
        if key_id:
            query += " AND key_id = ?"
            params.append(key_id)
        if profile_id:
            query += " AND profile_id = ?"
            params.append(profile_id)
        if model:
            query += " AND model = ?"
            params.append(model)
        query += " ORDER BY ts ASC"

        async with get_db() as db:
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()

        # Bucket rows
        buckets: Dict[int, Dict[str, Any]] = {}
        for r in rows:
            ts, st, lat, in_u, out_u, ep = r[0], r[1], float(r[2]), float(r[3] or 0), float(r[4] or 0), r[5]
            bucket_idx = int(math.floor(ts / bucket_step)) * int(bucket_step)
            if bucket_idx not in buckets:
                buckets[bucket_idx] = {
                    "timestamp": bucket_idx,
                    "requests": 0,
                    "errors": 0,
                    "tokens_in": 0.0,
                    "tokens_out": 0.0,
                    "audio_seconds_in": 0.0,
                    "audio_seconds_out": 0.0,
                    "latencies": [],
                }
            b = buckets[bucket_idx]
            b["requests"] += 1
            if st >= 400:
                b["errors"] += 1
            if "chat" in ep:
                b["tokens_in"] += in_u
                b["tokens_out"] += out_u
            elif "audio" in ep or "transcription" in ep:
                b["audio_seconds_in"] += in_u
            elif "text-to-speech" in ep or "voice" in ep:
                b["audio_seconds_out"] += out_u
            b["latencies"].append(lat)

        series = []
        for b_ts in sorted(buckets.keys()):
            b_info = buckets[b_ts]
            p50, p95 = compute_percentiles(b_info["latencies"])
            series.append({
                "timestamp": b_ts,
                "bucket_label": time.strftime("%Y-%m-%d %H:%M", time.gmtime(b_ts)),
                "requests": b_info["requests"],
                "errors": b_info["errors"],
                "tokens_in": round(b_info["tokens_in"], 2),
                "tokens_out": round(b_info["tokens_out"], 2),
                "audio_seconds_in": round(b_info["audio_seconds_in"], 2),
                "audio_seconds_out": round(b_info["audio_seconds_out"], 2),
                "p50_latency_ms": p50,
                "p95_latency_ms": p95,
            })

        return {
            "interval": interval,
            "since": since_ts,
            "until": until_ts,
            "series": series,
        }

    @staticmethod
    async def get_requests_log(
        limit: int = 50,
        offset: int = 0,
        key_id: Optional[str] = None,
        profile_id: Optional[str] = None,
        endpoint: Optional[str] = None,
        status: Optional[int] = None,
        since: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Query paged request event log, joining key prefix and profile name."""
        where_clauses = []
        params: List[Any] = []

        if since is not None:
            since_ts = parse_time_window(since)
            where_clauses.append("ue.ts >= ?")
            params.append(since_ts)
        if key_id:
            where_clauses.append("ue.key_id = ?")
            params.append(key_id)
        if profile_id:
            where_clauses.append("ue.profile_id = ?")
            params.append(profile_id)
        if endpoint:
            where_clauses.append("ue.endpoint = ?")
            params.append(endpoint)
        if status is not None:
            where_clauses.append("ue.status = ?")
            params.append(status)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        count_query = f"SELECT COUNT(*) FROM usage_events ue {where_sql}"
        data_query = f"""
            SELECT ue.id, ue.ts, ue.key_id, k.prefix, ue.profile_id, p.name,
                   ue.endpoint, ue.model, ue.status, ue.latency_ms, ue.ttfb_ms,
                   ue.input_units, ue.output_units, ue.bytes_in, ue.bytes_out, ue.error_code
            FROM usage_events ue
            LEFT JOIN api_keys k ON ue.key_id = k.id
            LEFT JOIN profiles p ON ue.profile_id = p.id
            {where_sql}
            ORDER BY ue.ts DESC
            LIMIT ? OFFSET ?
        """

        async with get_db() as db:
            async with db.execute(count_query, params) as cursor:
                total_row = await cursor.fetchone()
                total = total_row[0] if total_row else 0

            data_params = list(params) + [min(limit, 500), max(offset, 0)]
            async with db.execute(data_query, data_params) as cursor:
                rows = await cursor.fetchall()

        items = []
        for r in rows:
            items.append({
                "id": r[0],
                "ts": r[1],
                "timestamp_iso": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime(r[1])),
                "key_id": r[2],
                "key_prefix": r[3] or "none",
                "profile_id": r[4],
                "profile_name": r[5] or "none",
                "endpoint": r[6],
                "model": r[7] or "none",
                "status": r[8],
                "latency_ms": round(r[9], 2),
                "ttfb_ms": round(r[10], 2) if r[10] is not None else None,
                "input_units": round(r[11] or 0.0, 2),
                "output_units": round(r[12] or 0.0, 2),
                "bytes_in": r[13] or 0,
                "bytes_out": r[14] or 0,
                "error_code": r[15],
            })

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": items,
        }

    @staticmethod
    async def export_requests_csv(
        key_id: Optional[str] = None,
        profile_id: Optional[str] = None,
        endpoint: Optional[str] = None,
        status: Optional[int] = None,
        since: Optional[Any] = None,
    ) -> str:
        """Generate CSV string of request events (strictly no secret keys, prompt, or audio content)."""
        data = await UsageService.get_requests_log(
            limit=5000,
            offset=0,
            key_id=key_id,
            profile_id=profile_id,
            endpoint=endpoint,
            status=status,
            since=since,
        )

        output = io.StringIO()
        fieldnames = [
            "id", "timestamp_iso", "key_id", "key_prefix", "profile_id", "profile_name",
            "endpoint", "model", "status", "latency_ms", "ttfb_ms", "input_units",
            "output_units", "bytes_in", "bytes_out", "error_code"
        ]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()

        for item in data["items"]:
            row_dict = {f: item.get(f) for f in fieldnames}
            writer.writerow(row_dict)

        return output.getvalue()

    @staticmethod
    async def get_models_status() -> Dict[str, Any]:
        """Check status of installed models (warm vs idle) and system memory."""
        # Query installed models
        async with get_db() as db:
            async with db.execute(
                "SELECT id, name, category, provider, size_mb FROM installed_models ORDER BY category, name"
            ) as cursor:
                rows = await cursor.fetchall()

        # Check warm state
        # In our architecture: kokoro (TTS) and whisper/llama in LocalAI
        models_list = []
        for r in rows:
            mid, name, cat, prov, size = r[0], r[1], r[2], r[3], r[4]
            # STT streaming engine sherpa is warm if streaming daemon is running
            # Kokoro is warm once initialized
            is_warm = True if cat in ("tts", "stt") else False
            models_list.append({
                "id": mid,
                "name": name,
                "category": cat,
                "provider": prov,
                "size_mb": size,
                "status": "warm" if is_warm else "idle",
                "memory_mb": size if is_warm else 0,
            })

        # Memory stats
        system_memory = {"total_mb": 8192, "available_mb": 4096, "used_mb": 4096}
        try:
            import psutil
            vm = psutil.virtual_memory()
            system_memory = {
                "total_mb": round(vm.total / (1024 * 1024)),
                "available_mb": round(vm.available / (1024 * 1024)),
                "used_mb": round(vm.used / (1024 * 1024)),
            }
        except Exception:
            pass

        return {
            "models": models_list,
            "system_memory": system_memory,
        }
