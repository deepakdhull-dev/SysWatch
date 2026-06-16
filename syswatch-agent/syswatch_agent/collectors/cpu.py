class CPUCollector:
    def __init__(self):
        self.prev_stat = None

    def read_proc(self):
        with open("/proc/stat", "r") as f:
            line = f.readline()
        return [int(v) for v in line.split()[1:]]

    def collect(self):
        curr_stat = self.read_proc()
        if self.prev_stat is None:
            self.prev_stat = curr_stat
            return None

        curr_idle = curr_stat[3] + curr_stat[4]
        prev_idle = self.prev_stat[3] + self.prev_stat[4]
        curr_total = sum(curr_stat)
        prev_total = sum(self.prev_stat)

        idle_delta = curr_idle - prev_idle
        total_delta = curr_total - prev_total
        self.prev_stat = curr_stat
        if total_delta == 0:
            return None

        return round((1 - idle_delta / total_delta) * 100, 2)
