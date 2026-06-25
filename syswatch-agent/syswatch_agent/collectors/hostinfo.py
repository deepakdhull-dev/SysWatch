import platform
import socket


class HostInfoCollector:
    def cpu_model(self):
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()

        return "Unknown"

    def cpu_threads(self):
        with open("/proc/cpuinfo", "r") as f:
            lines = f.readlines()
        threads = 0
        for line in lines:
            if line.split(":")[0].strip() == "processor":
                threads += 1
        return threads

    def cpu_cores(self):  # may undercount on multi-socket system
        with open("/proc/cpuinfo", "r") as f:
            lines = f.readlines()
        for line in lines:
            parts = line.strip().split(":")
            if parts[0].strip() == "cpu cores":
                return int(parts[1].strip())
        return 0

    def os_name(self):
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip().strip('"')

        return "Unknown"

    def collect(self):
        return {
            "hostname": socket.gethostname(),
            "kernel": platform.release(),
            "os_name": self.os_name(),
            "cpu_model": self.cpu_model(),
            "cpu_cores": self.cpu_cores(),
            "cpu_threads": self.cpu_threads(),
        }
