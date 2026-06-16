import shutil


class DiskCollector:
    def collect(self):
        usage = shutil.disk_usage("/")
        total_gb = round(usage.total / (1024**3), 1)
        used_gb = round(usage.used / (1024**3), 1)

        return {
            "mount_point": "/",
            "used": used_gb,
            "total": total_gb,
            "pct": round(usage.used / usage.total * 100, 1),
        }
