"""Host system resources and memory fit scoring API."""

import platform
import subprocess
import shutil
from typing import Dict
from fastapi import APIRouter, Depends
from app.core.security import require_api_key

router = APIRouter(prefix="/v1/system", tags=["System & Resources"])
system_router = router


def get_mac_memory_info() -> Dict[str, float]:
    """Get host total and available memory in MB on macOS / Linux."""
    total_mb = 8192.0
    avail_mb = 4096.0

    try:
        if platform.system() == "Darwin":
            out = (
                subprocess.check_output(["sysctl", "-n", "hw.memsize"]).decode().strip()
            )
            total_mb = int(out) / (1024 * 1024)

            # Read vm_stat for free/inactive pages
            vm = subprocess.check_output(["vm_stat"]).decode()
            page_size = 4096
            free_pages = 0
            inactive_pages = 0
            for line in vm.splitlines():
                if "Pages free:" in line:
                    free_pages = int(line.split(":")[1].strip().rstrip("."))
                elif "Pages inactive:" in line:
                    inactive_pages = int(line.split(":")[1].strip().rstrip("."))
            avail_mb = ((free_pages + inactive_pages) * page_size) / (1024 * 1024)
        else:
            # Linux fallback
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        total_mb = int(line.split()[1]) / 1024
                    elif line.startswith("MemAvailable:"):
                        avail_mb = int(line.split()[1]) / 1024
    except Exception:
        pass

    return {
        "total_mb": round(total_mb, 1),
        "available_mb": round(avail_mb, 1),
        "used_mb": round(total_mb - avail_mb, 1),
    }


def compute_fit_score(req_ram_mb: int, avail_ram_mb: float) -> int:
    """Compute 0-100 fit score based on R3 piecewise mathematical formula."""
    safe_budget_mb = min(avail_ram_mb, 4500.0)

    if req_ram_mb <= safe_budget_mb * 0.55:
        # Well within budget
        score = 100 - (req_ram_mb / safe_budget_mb) * 15
    elif req_ram_mb <= safe_budget_mb * 0.85:
        # Moderate fit
        score = (
            85 - ((req_ram_mb - safe_budget_mb * 0.55) / (safe_budget_mb * 0.30)) * 25
        )
    elif req_ram_mb <= safe_budget_mb:
        # Tight fit
        score = (
            60 - ((req_ram_mb - safe_budget_mb * 0.85) / (safe_budget_mb * 0.15)) * 30
        )
    else:
        # Exceeds budget (risk of paging)
        overflow = req_ram_mb - safe_budget_mb
        score = max(0, 30 - int((overflow / 1000.0) * 20))

    return max(0, min(100, int(score)))


@router.get("/resources")
async def get_system_resources(user: str = Depends(require_api_key)):
    mem = get_mac_memory_info()
    # Disk info
    total_disk, used_disk, free_disk = shutil.disk_usage("/")
    free_disk_gb = round(free_disk / (1024**3), 2)
    total_disk_gb = round(total_disk / (1024**3), 2)

    has_metal = platform.system() == "Darwin" and platform.machine() == "arm64"

    return {
        "memory": {
            "total_mb": mem["total_mb"],
            "available_mb": mem["available_mb"],
            "used_mb": mem["used_mb"],
            "safe_ai_ceiling_mb": 4500.0,
        },
        "disk": {
            "total_gb": total_disk_gb,
            "free_gb": free_disk_gb,
        },
        "hardware": {
            "platform": platform.system(),
            "architecture": platform.machine(),
            "has_metal_gpu": has_metal,
            "target": "MacBook Air M2 8GB",
        },
    }
