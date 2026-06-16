class LoadCollector:
    def collect(self):
        with open("/proc/loadavg") as f:
            load_1m, load_5m, load_15m = map(float, f.readline().split()[:3])

        return {
            "load_1m": load_1m,
            "load_5m": load_5m,
            "load_15m": load_15m,
        }
