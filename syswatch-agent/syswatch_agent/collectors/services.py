import subprocess


class ServiceCollector:
    def __init__(self, services: list[str]):
        self.services = services

    def get_status(self, name):
        try:
            result = subprocess.run(
                ["systemctl", "is-active", name],
                capture_output=True,
                text=True,
                timeout=2.0,
            )
            return result.stdout.strip() == "active"
        except subprocess.TimeoutExpired:
            return False
        except FileNotFoundError:
            return False
        except subprocess.SubprocessError:
            return False

    def collect(self):
        return {name: self.get_status(name) for name in self.services}
