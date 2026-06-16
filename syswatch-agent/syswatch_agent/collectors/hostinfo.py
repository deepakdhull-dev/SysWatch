import platform
import socket

import psutil


class HostInfoCollector:
    def cpu_model(self):
        with open("/proc/cpuinfo") as f:
            for line in f:
                if line.startswith("model name"):
                    return line.split(":", 1)[1].strip()

        return "Unknown"

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
            "cpu_cores": psutil.cpu_count(logical=False),
            "cpu_threads": psutil.cpu_count(logical=True),
        }
