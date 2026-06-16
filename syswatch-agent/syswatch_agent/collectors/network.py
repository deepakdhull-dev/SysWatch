class NetworkCollector:
    def __init__(self, interface):
        self.interface = interface
        self.prev = None

    def read_proc(self):
        with open("/proc/net/dev", "r") as f:
            lines = f.readlines()
        return lines[2:]

    def collect(self):
        net = self.read_proc()
        res = None
        for line in net:
            parts = line.strip().split(":", 1)
            if parts[0] == self.interface:
                res = parts[1].strip().split()
                break

        if res is None:
            return None

        curr = {
            "bytes_in": int(res[0]),
            "bytes_out": int(res[8]),
            "packets_in": int(res[1]),
            "packets_out": int(res[9]),
            "errors_in": int(res[2]),
            "errors_out": int(res[10]),
        }

        if self.prev is None:
            self.prev = curr
            return None

        delta_bytes_in = max(0, curr["bytes_in"] - self.prev["bytes_in"])
        delta_bytes_out = max(0, curr["bytes_out"] - self.prev["bytes_out"])
        delta_packets_in = max(0, curr["packets_in"] - self.prev["packets_in"])
        delta_packets_out = max(0, curr["packets_out"] - self.prev["packets_out"])
        delta_errors_in = max(0, curr["errors_in"] - self.prev["errors_in"])
        delta_errors_out = max(0, curr["errors_out"] - self.prev["errors_out"])

        self.prev = curr
        return {
            "bytes_in": delta_bytes_in,
            "bytes_out": delta_bytes_out,
            "packets_in": delta_packets_in,
            "packets_out": delta_packets_out,
            "errors_in": delta_errors_in,
            "errors_out": delta_errors_out,
        }
