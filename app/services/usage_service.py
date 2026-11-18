"""
Usage Analytics & Cost Attribution Engine.
Strict Privacy Constraint: Zero content, zero transcripts, zero prompts, zero secrets stored.
Tracks only operational telemetry (timestamps, latency, token/second units, status codes, payload bytes).
"""

import time
import math
import io
import csv
from typing import Optional, List, Dict, Any, Tuple
from app.storage.database import get_db
from app.services.metrics import metrics_collector


def parse_time_window(window_str: Optional[Any]) -> float:
    """Convert human strings like '1h', '24h', '7d', '30d' or timestamps into an epoch float cutoff."""
    now = time.time()
    if window_str is None:
        return now - 86400.0  # default 24h
    if isinstance(window_str, (int, float)):
        return float(window_str)
    s = str(window_str).strip().lower()
    if s.endswith("h"):
        try:
            return now - float(s[:-1]) * 3600.0
        except ValueError:
            pass
    elif s.endswith("d"):
        try:
            return now - float(s[:-1]) * 86400.0
        except ValueError:
            pass
    elif s.endswith("m"):
        try:
            return now - float(s[:-1]) * 60.0
        except ValueError:
            pass
    try:
        val = float(s)
        # If value is small, treat as hours
        if val < 100000:
            return now - val * 3600.0
        return val
    except ValueError:
        return now - 86400.0


def compute_percentiles(values: List[float]) -> Tuple[float, float]:
    """Calculate p50 and p95 latencies."""
    if not values:
        return 0.0, 0.0
    sorted_v = sorted(values)
    n = len(sorted_v)
    idx_50 = min(int(0.50 * n), n - 1)
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
    actor_type: Optional[str] = None,
    key_name: Optional[str] = None,
    profile_name: Optional[str] = None,
) -> None:
    """Record a rich usage event in SQLite. Never logs prompts, content, or audio."""
    ts = timestamp if timestamp is not None else time.time()

    resolved_actor = actor_type or ("key" if key_id else "system")
    resolved_key_name = key_name
    resolved_profile_name = profile_name

    # If key_id is present, snapshot key name and profile name
    if key_id and (not resolved_key_name or not profile_id):
        try:
            async with get_db() as db:
                async with db.execute(
                    "SELECT k.name, k.profile_id, p.name FROM api_keys k LEFT JOIN profiles p ON k.profile_id = p.id WHERE k.id = ?",
                    (key_id,),
                ) as cur:
                    row = await cur.fetchone()
                    if row:
                        if not resolved_key_name:
                            resolved_key_name = row[0]
                        if not profile_id and row[1]:
                            profile_id = row[1]
                        if not resolved_profile_name and row[2]:
                            resolved_profile_name = row[2]
        except Exception:
            pass

    # Clean label fallbacks for non-key or system/admin requests
    if not key_id:
        if "chat" in endpoint or "text-to-speech" in endpoint:
            if "Try" in str(error_code or ""):
                resolved_actor = "admin"
                resolved_key_name = "Admin (Try it)"
            else:
                resolved_actor = "system"
                resolved_key_name = (
                    "Admin (UI)" if "admin" in endpoint else "System: voice pipeline"
                )
        else:
            resolved_actor = "system"
            resolved_key_name = (
                "System: health check"
                if "health" in endpoint
                else "System (unattributed)"
            )

    try:
        async with get_db() as db:
            await db.execute(
                """
                INSERT INTO usage_events (
                    ts, key_id, profile_id, endpoint, model, status,
                    latency_ms, ttfb_ms, input_units, output_units,
                    bytes_in, bytes_out, error_code, actor_type, key_name, profile_name
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    key_id,
                    profile_id,
                    endpoint,
                    model,
                    status,
                    latency_ms,
                    ttfb_ms,
                    input_units,
                    output_units,
                    bytes_in,
                    bytes_out,
                    error_code,
                    resolved_actor,
                    resolved_key_name,
                    resolved_profile_name,
                ),
            )
            await db.commit()
    except Exception:
        # Fallback for schemas without actor columns
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
                        ts,
                        key_id,
                        profile_id,
                        endpoint,
                        model,
                        status,
                        latency_ms,
                        ttfb_ms,
                        input_units,
                        output_units,
                        bytes_in,
                        bytes_out,
                        error_code,
                    ),
                )
                await db.commit()
        except Exception:
            pass


class UsageService:
    @staticmethod
    async def get_summary(
        since: Optional[Any] = "24h",
        until: Optional[float] = None,
        key_id: Optional[str] = None,
        profile_id: Optional[str] = None,
        model: Optional[str] = None,
        endpoint: Optional[str] = None,
        group_by: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Compute aggregated summary metrics with latency percentiles and breakdown groups."""
        since_ts = parse_time_window(since)
        until_ts = float(until) if until is not None else time.time()

        where_clauses = ["ts >= ?", "ts <= ?"]
        params: List[Any] = [since_ts, until_ts]

        if key_id:
            where_clauses.append("key_id = ?")
            params.append(key_id)
        if profile_id:
            where_clauses.append("profile_id = ?")
            params.append(profile_id)
        if model:
            where_clauses.append("model = ?")
            params.append(model)
        if endpoint:
            where_clauses.append("endpoint = ?")
            params.append(endpoint)

        where_sql = f"WHERE {' AND '.join(where_clauses)}"

        query = f"""
            SELECT ts, status, latency_ms, ttfb_ms, input_units, output_units, endpoint, model, key_id, profile_id
            FROM usage_events
            {where_sql}
            ORDER BY ts ASC
        """

        async with get_db() as db:
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()

        total_requests = len(rows)
        error_count = 0
        status_429_count = 0
        status_503_count = 0
        latencies: List[float] = []
        ttfbs: List[float] = []
        llm_tokens_in = 0.0
        llm_tokens_out = 0.0
        stt_audio_seconds_in = 0.0
        tts_audio_seconds_out = 0.0
        tts_characters_in = 0.0

        groups: Dict[str, Dict[str, Any]] = {}

        for r in rows:
            _ts, st, lat, ttfb, in_u, out_u, ep, mod, k_id, p_id = (
                r[0],
                r[1],
                float(r[2]),
                r[3],
                float(r[4] or 0),
                float(r[5] or 0),
                r[6],
                r[7],
                r[8],
                r[9],
            )
            latencies.append(lat)
            if ttfb is not None:
                ttfbs.append(float(ttfb))

            if st >= 400:
                error_count += 1
            if st == 429:
                status_429_count += 1
            elif st == 503:
                status_503_count += 1

            if "chat" in ep:
                llm_tokens_in += in_u
                llm_tokens_out += out_u
            elif "audio" in ep or "transcription" in ep:
                stt_audio_seconds_in += in_u
            elif "text-to-speech" in ep or "voice" in ep:
                tts_characters_in += in_u
                tts_audio_seconds_out += out_u

            # Handle grouping
            if group_by:
                g_key = "unknown"
                if group_by == "model":
                    g_key = mod or "unspecified"
                elif group_by == "endpoint":
                    g_key = ep
                elif group_by == "key":
                    g_key = k_id or "unauthenticated"
                elif group_by == "profile":
                    g_key = p_id or "default"

                if g_key not in groups:
                    groups[g_key] = {
                        "name": g_key,
                        "requests": 0,
                        "errors": 0,
                        "latencies": [],
                        "tokens_in": 0.0,
                        "tokens_out": 0.0,
                        "audio_seconds_in": 0.0,
                        "audio_seconds_out": 0.0,
                    }
                g = groups[g_key]
                g["requests"] += 1
                if st >= 400:
                    g["errors"] += 1
                g["latencies"].append(lat)
                if "chat" in ep:
                    g["tokens_in"] += in_u
                    g["tokens_out"] += out_u
                elif "audio" in ep or "transcription" in ep:
                    g["audio_seconds_in"] += in_u
                elif "text-to-speech" in ep or "voice" in ep:
                    g["audio_seconds_out"] += out_u

        p50, p95 = compute_percentiles(latencies)
        ttfb_p50, ttfb_p95 = compute_percentiles(ttfbs)
        error_rate = round(
            (error_count / total_requests * 100.0) if total_requests > 0 else 0.0, 2
        )

        groups_res = []
        for g_name, g_info in groups.items():
            gp50, gp95 = compute_percentiles(g_info["latencies"])
            groups_res.append(
                {
                    "name": g_name,
                    "requests": g_info["requests"],
                    "errors": g_info["errors"],
                    "error_rate": round(
                        (g_info["errors"] / g_info["requests"] * 100.0)
                        if g_info["requests"] > 0
                        else 0.0,
                        2,
                    ),
                    "p50_latency_ms": gp50,
                    "p95_latency_ms": gp95,
                    "tokens_in": round(g_info["tokens_in"], 2),
                    "tokens_out": round(g_info["tokens_out"], 2),
                    "audio_seconds_in": round(g_info["audio_seconds_in"], 2),
                    "audio_seconds_out": round(g_info["audio_seconds_out"], 2),
                }
            )

        success_count = total_requests - error_count
        success_rate = (success_count / total_requests) if total_requests > 0 else 1.0

        return {
            "since": since_ts,
            "until": until_ts,
            "total_requests": total_requests,
            "success_count": success_count,
            "error_count": error_count,
            "status_429_count": status_429_count,
            "status_503_count": status_503_count,
            "success_rate": round(success_rate, 4),
            "error_rate": error_rate,
            "p50_latency_ms": p50,
            "p95_latency_ms": p95,
            "latency_p50_ms": p50,
            "latency_p95_ms": p95,
            "ttfb_p50_ms": ttfb_p50,
            "ttfb_p95_ms": ttfb_p95,
            "avg_ttfb_ms": ttfb_p50 or 0.0,
            "llm_tokens_in": round(llm_tokens_in, 2),
            "llm_tokens_out": round(llm_tokens_out, 2),
            "stt_audio_seconds_in": round(stt_audio_seconds_in, 2),
            "tts_audio_seconds_out": round(tts_audio_seconds_out, 2),
            "tts_characters_in": round(tts_characters_in, 2),
            "stt_fallbacks": metrics_collector.get_stt_fallbacks(),
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
        """Aggregate usage events into discrete time buckets with zero-filled gaps."""
        since_ts = parse_time_window(since)
        until_ts = float(until) if until is not None else time.time()
        bucket_step = 86400.0 if interval == "day" else 3600.0

        # Continuous zero-fill pre-population
        buckets: Dict[int, Dict[str, Any]] = {}
        cur_bucket = int(math.floor(since_ts / bucket_step)) * int(bucket_step)
        end_bucket = int(math.floor(until_ts / bucket_step)) * int(bucket_step)
        if end_bucket >= cur_bucket and (end_bucket - cur_bucket) / bucket_step <= 1000:
            while cur_bucket <= end_bucket:
                buckets[cur_bucket] = {
                    "timestamp": cur_bucket,
                    "requests": 0,
                    "errors": 0,
                    "tokens_in": 0.0,
                    "tokens_out": 0.0,
                    "audio_seconds_in": 0.0,
                    "audio_seconds_out": 0.0,
                    "latencies": [],
                }
                cur_bucket += int(bucket_step)

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

        for r in rows:
            ts, st, lat, in_u, out_u, ep = (
                r[0],
                r[1],
                float(r[2]),
                float(r[3] or 0),
                float(r[4] or 0),
                r[5],
            )
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
            series.append(
                {
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
                }
            )

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
        actor_type: Optional[str] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Query request events with actor attribution, key name snapshots, and zero 'None' values."""
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
        if actor_type:
            where_clauses.append("ue.actor_type = ?")
            params.append(actor_type)
        if model:
            where_clauses.append("ue.model = ?")
            params.append(model)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        count_query = f"SELECT COUNT(*) FROM usage_events ue {where_sql}"
        data_query = f"""
            SELECT ue.id, ue.ts, ue.key_id,
                   COALESCE(ue.key_name, k.name, k.prefix, 'System (unattributed)'),
                   ue.profile_id,
                   COALESCE(ue.profile_name, p.name, '—'),
                   ue.endpoint, ue.model, ue.status, ue.latency_ms, ue.ttfb_ms,
                   ue.input_units, ue.output_units, ue.bytes_in, ue.bytes_out, ue.error_code,
                   COALESCE(ue.actor_type, CASE WHEN ue.key_id IS NOT NULL THEN 'key' ELSE 'system' END)
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
            k_name = (
                r[3] if r[3] and r[3].lower() != "none" else "System (unattributed)"
            )
            p_name = r[5] if r[5] and r[5].lower() != "none" else "—"
            mod_name = r[7] if r[7] and r[7].lower() != "none" else "—"
            items.append(
                {
                    "id": r[0],
                    "ts": r[1],
                    "timestamp_iso": time.strftime(
                        "%Y-%m-%d %H:%M:%SZ", time.gmtime(r[1])
                    ),
                    "key_id": r[2],
                    "key_name": k_name,
                    "key_prefix": k_name,
                    "profile_id": r[4],
                    "profile_name": p_name,
                    "endpoint": r[6],
                    "model": mod_name,
                    "status": r[8],
                    "latency_ms": round(r[9], 2),
                    "ttfb_ms": round(r[10], 2) if r[10] is not None else None,
                    "input_units": round(r[11] or 0.0, 2),
                    "output_units": round(r[12] or 0.0, 2),
                    "bytes_in": r[13] or 0,
                    "bytes_out": r[14] or 0,
                    "error_code": r[15],
                    "actor_type": r[16] or "key",
                }
            )

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "items": items,
        }

    @staticmethod
    async def get_model_activity(
        since: Optional[Any] = "24h",
        until: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Compute model activity breakdown with request counts, percentiles, and key attribution."""
        since_ts = parse_time_window(since)
        until_ts = float(until) if until is not None else time.time()

        query = """
            SELECT ue.model, ue.status, ue.latency_ms, ue.ttfb_ms, ue.ts,
                   COALESCE(ue.key_name, k.name, 'System')
            FROM usage_events ue
            LEFT JOIN api_keys k ON ue.key_id = k.id
            WHERE ue.ts >= ? AND ue.ts <= ? AND ue.model IS NOT NULL AND ue.model != ''
            ORDER BY ue.ts DESC
        """
        async with get_db() as db:
            async with db.execute(query, [since_ts, until_ts]) as cursor:
                rows = await cursor.fetchall()

        from app.services.residency_manager import residency_manager

        residency_status = await residency_manager.get_residency_status()
        res_by_id = {m["id"].lower(): m for m in residency_status.get("models", [])}

        groups: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            mod, st, lat, ttfb, ts, key_label = (
                r[0],
                r[1],
                float(r[2]),
                r[3],
                r[4],
                r[5],
            )
            if mod not in groups:
                res_info = res_by_id.get(mod.lower(), {})
                groups[mod] = {
                    "model_id": mod,
                    "display_name": res_info.get("display_name", mod),
                    "engine": res_info.get("engine", "localai"),
                    "state": res_info.get("state", "idle"),
                    "memory_mb": res_info.get("memory_mb", 0),
                    "requests": 0,
                    "errors": 0,
                    "latencies": [],
                    "ttfb_list": [],
                    "last_used_ts": ts,
                    "keys": {},
                }
            g = groups[mod]
            g["requests"] += 1
            if st >= 400:
                g["errors"] += 1
            g["latencies"].append(lat)
            if ttfb is not None:
                g["ttfb_list"].append(float(ttfb))
            if ts > g["last_used_ts"]:
                g["last_used_ts"] = ts
            g["keys"][key_label] = g["keys"].get(key_label, 0) + 1

        result = []
        for mod, g in groups.items():
            p50, p95 = compute_percentiles(g["latencies"])
            p50_ttfb, _ = (
                compute_percentiles(g["ttfb_list"]) if g["ttfb_list"] else (None, None)
            )
            top_keys = [
                {"name": k, "requests": cnt}
                for k, cnt in sorted(
                    g["keys"].items(), key=lambda x: x[1], reverse=True
                )[:5]
            ]
            result.append(
                {
                    "model_id": g["model_id"],
                    "display_name": g["display_name"],
                    "engine": g["engine"],
                    "state": g["state"],
                    "memory_mb": g["memory_mb"],
                    "requests": g["requests"],
                    "errors": g["errors"],
                    "p50_latency_ms": p50,
                    "p95_latency_ms": p95,
                    "ttfb_ms": p50_ttfb,
                    "last_used_ts": g["last_used_ts"],
                    "last_used_iso": time.strftime(
                        "%Y-%m-%d %H:%M:%SZ", time.gmtime(g["last_used_ts"])
                    ),
                    "top_keys": top_keys,
                }
            )

        result.sort(key=lambda x: x["requests"], reverse=True)
        return result

    @staticmethod
    async def export_requests_csv(
        key_id: Optional[str] = None,
        profile_id: Optional[str] = None,
        endpoint: Optional[str] = None,
        status: Optional[int] = None,
        since: Optional[Any] = None,
        actor_type: Optional[str] = None,
        model: Optional[str] = None,
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
            actor_type=actor_type,
            model=model,
        )

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(
            [
                "id",
                "timestamp_iso",
                "key_id",
                "key_name",
                "profile_id",
                "profile_name",
                "actor_type",
                "endpoint",
                "model",
                "status",
                "latency_ms",
                "ttfb_ms",
                "input_units",
                "output_units",
                "bytes_in",
                "bytes_out",
                "error_code",
            ]
        )

        for item in data.get("items", []):
            writer.writerow(
                [
                    item["id"],
                    item["timestamp_iso"],
                    item.get("key_id") or "",
                    item.get("key_name", "System"),
                    item.get("profile_id") or "",
                    item.get("profile_name", "—"),
                    item.get("actor_type", "key"),
                    item["endpoint"],
                    item["model"],
                    item["status"],
                    item["latency_ms"],
                    item["ttfb_ms"] if item["ttfb_ms"] is not None else "",
                    item["input_units"],
                    item["output_units"],
                    item["bytes_in"],
                    item["bytes_out"],
                    item["error_code"] or "",
                ]
            )

        return output.getvalue()

    @staticmethod
    async def get_system_memory() -> Dict[str, Any]:
        """Query real operating system virtual memory."""
        system_memory = {
            "total_mb": 8192,
            "available_mb": 4096,
            "used_mb": 4096,
            "percent": 50.0,
        }
        try:
            import psutil

            vm = psutil.virtual_memory()
            system_memory = {
                "total_mb": round(vm.total / (1024 * 1024)),
                "available_mb": round(vm.available / (1024 * 1024)),
                "used_mb": round(vm.used / (1024 * 1024)),
                "percent": round(vm.percent, 1),
            }
        except Exception:
            try:
                from app.api.system import get_mac_memory_info

                mem = get_mac_memory_info()
                system_memory = {
                    "total_mb": round(mem.get("total_mb", 8192)),
                    "available_mb": round(mem.get("available_mb", 4096)),
                    "used_mb": round(mem.get("used_mb", 4096)),
                    "percent": round(
                        (mem.get("used_mb", 4096) / max(mem.get("total_mb", 8192), 1))
                        * 100,
                        1,
                    ),
                }
            except Exception:
                pass
        return system_memory

    @staticmethod
    async def get_models_status() -> Dict[str, Any]:
        """Check status of installed models (warm vs idle) using residency manager and real system memory."""
        from app.services.residency_manager import residency_manager

        residency_status = await residency_manager.get_residency_status()
        system_memory = await UsageService.get_system_memory()

        res_by_id = {m["id"].lower(): m for m in residency_status.get("models", [])}

        async with get_db() as db:
            async with db.execute(
                "SELECT id, name, category, provider, size_mb FROM installed_models ORDER BY category, name"
            ) as cursor:
                rows = await cursor.fetchall()

        models_list = []
        for r in rows:
            mid, name, cat, prov, size = r[0], r[1], r[2], r[3], r[4]
            norm_key = mid.split(":")[0].lower()
            res_info = res_by_id.get(mid.lower()) or res_by_id.get(norm_key)

            is_warm = res_info is not None and res_info.get("loaded", False)
            state = res_info.get("state", "idle") if is_warm else "idle"
            mem_mb = res_info.get("memory_mb", size) if is_warm else 0

            models_list.append(
                {
                    "id": mid,
                    "name": name,
                    "category": cat,
                    "provider": prov,
                    "size_mb": size,
                    "status": "warm" if is_warm else "idle",
                    "state": state,
                    "memory_mb": mem_mb,
                    "memory_type": res_info.get("memory_type", "estimated")
                    if is_warm
                    else "none",
                    "last_used_ts": res_info.get("last_used_ts") if res_info else None,
                    "protected": res_info.get("protected", False)
                    if res_info
                    else False,
                }
            )

        return {
            "models": models_list,
            "resident_models": residency_status.get("models", []),
            "system_memory": system_memory,
            "auto_flush_enabled": residency_status.get("auto_flush_enabled", False),
        }
