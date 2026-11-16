"""Measure what the guardian itself costs: CPU and memory over a period.

usage: python tools/footprint.py [seconds] [max_cpu_percent] [max_rss_mb] [-- run args]

Starts a private guardian in observe mode (its own data folder, nothing shared
with an installed one, nothing closed), lets it settle, then reports CPU as a
percentage of one core and resident memory. Exits 1 when a limit is exceeded.
"""
import json
import os
import subprocess
import sys
import tempfile
import time

import psutil

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main(argv):
    extra = argv[argv.index("--") + 1:] if "--" in argv else []
    numbers = [float(a) for a in (argv[:argv.index("--")] if "--" in argv else argv)]
    seconds, max_cpu, max_rss = (numbers + [60.0, 0.0, 0.0][len(numbers):])[:3]
    home = tempfile.mkdtemp(prefix="smartram-footprint-")
    # Observe mode does the same work but closes nothing on the machine being measured.
    with open(os.path.join(home, "config.json"), "w") as f:
        json.dump({"mode": "observe"}, f)
    env = dict(os.environ, SMARTRAM_HOME=home, PYTHONPATH=REPO)
    guard = subprocess.Popen([sys.executable, "-m", "smartram", "run"] + extra, env=env,
                             cwd=REPO, stdout=subprocess.DEVNULL)
    try:
        time.sleep(10)
        proc = psutil.Process(guard.pid)

        def used():
            t = proc.cpu_times()
            return (t.user + t.system + getattr(t, "children_user", 0.0)
                    + getattr(t, "children_system", 0.0))

        cpu0, wall0 = used(), time.time()
        time.sleep(seconds)
        cpu = (used() - cpu0) / (time.time() - wall0) * 100.0
        rss = proc.memory_info().rss / 2**20
    finally:
        guard.terminate()
    print("footprint: %.2f%% of one core, %.0f MB, %d processes on this machine, %s%s"
          % (cpu, rss, len(psutil.pids()), sys.platform, " " + " ".join(extra) if extra else ""))
    if (max_cpu and cpu > max_cpu) or (max_rss and rss > max_rss):
        print("over the limit (%.1f%% CPU, %.0f MB)" % (max_cpu, max_rss))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
