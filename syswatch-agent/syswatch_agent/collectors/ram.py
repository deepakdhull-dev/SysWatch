class RAMCollector:
    def read_proc(self):
        with open("/proc/meminfo", "r") as f:
            lines = f.readlines()
        mem = {}
        for line in lines:
            parts = line.split(":")
            key = parts[0].strip()
            value = int(parts[1].strip().split()[0])
            mem[key] = value
        return mem

    def collect(self):
        mem = self.read_proc()
        res = {}
        res["mem_total"] = round(mem["MemTotal"] / (1024 * 1024), 1)
        res["mem_used"] = round(
            (mem["MemTotal"] - mem["MemAvailable"]) / (1024 * 1024), 1
        )
        res["mem_pct"] = round(
            (mem["MemTotal"] - mem["MemAvailable"]) / mem["MemTotal"] * 100, 2
        )
        res["swap_total"] = round(mem["SwapTotal"] / (1024 * 1024), 1)
        res["swap_pct"] = 0
        res["swap_used"] = round(
            (mem["SwapTotal"] - mem["SwapFree"]) / (1024 * 1024), 1
        )
        if mem["SwapTotal"] > 0:
            res["swap_pct"] = round(
                (mem["SwapTotal"] - mem["SwapFree"]) / mem["SwapTotal"] * 100, 2
            )
        return res
