import json
import os
import subprocess
import sys

import pytest

from smartram import autostart, config, rules
from smartram.__main__ import main
from smartram.engine import Engine, active_tree, make_proc
from smartram.widget import fmt_bytes, mood, panel_text

MB = 2**20


class FakeCloser:
    def __init__(self, result=(True, 50 * MB, "")):
        self.result, self.asked = result, []

    def request(self, proc):
        self.asked.append(proc.name)
        return self.result


class World:
    """A fake machine the engine can sample."""

    def __init__(self, tmp_path, percent=80, closer=None):
        self.now = 10000.0
        self.percent = percent
        self.fg = 100
        self.procs = [
            make_proc(1, "systemd", 20 * MB),
            make_proc(100, "firefox", 900 * MB, cpu=5.0),
            make_proc(101, "crashpad_handler", 30 * MB, ppid=100),
            make_proc(200, "GoogleUpdate.exe", 60 * MB),
            make_proc(300, "spotify", 400 * MB),
        ]
        self.closer = closer or FakeCloser()
        self.paths = config.Paths(tmp_path).ensure()
        self.engine = Engine(self.paths, sampler=self.sample, closer=self.closer,
                             foreground=lambda: self.fg, clock=lambda: self.now)

    def sample(self):
        mem = {"total": 8 * 2**30, "available": 2 * 2**30, "percent": self.percent}
        return mem, list(self.procs)

    def tick(self, times=1):
        for _ in range(times):
            self.now += 5
            snap = self.engine.tick()
        return snap

    def remove(self, pid):
        self.procs = [p for p in self.procs if p.pid != pid]

    def reason(self, snap, name):
        return {k["name"]: k["reason"] for k in snap["kept"]}.get(name)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_normalize_and_rules():
    assert rules.normalize(" GoogleUpdate.EXE ") == "googleupdate"
    assert rules.is_junk("googleupdate") and rules.is_junk("chrome_crashpad_handler")
    assert not rules.is_junk("firefox")
    assert rules.is_protected("explorer") and rules.is_protected("python3.12")
    assert rules.is_protected("spotify", {"spotify"}) and not rules.is_protected("spotify")
    assert not rules.JUNK & rules.PROTECTED


def test_config_defaults_and_bad_values(tmp_path):
    cfg, warnings = config.load(tmp_path / "missing.json")
    assert cfg == config.DEFAULTS and not warnings
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"interval_seconds": -1, "mode": "kill", "act_above_percent": 75,
                                "close_list": ["Spotify.exe"], "protect_list": "x",
                                "unknown": 1}))
    cfg, warnings = config.load(path)
    assert cfg["interval_seconds"] == 5 and cfg["mode"] == "auto"
    assert cfg["act_above_percent"] == 75 and cfg["close_list"] == ["spotify"]
    assert len(warnings) == 3 and "unknown" not in cfg
    path.write_text("{broken")
    cfg, warnings = config.load(path)
    assert cfg == config.DEFAULTS and warnings


def test_active_tree_covers_ancestors_and_descendants():
    procs = [make_proc(1, "init", ppid=0), make_proc(2, "shell", ppid=1),
             make_proc(3, "app", ppid=2), make_proc(4, "child", ppid=3),
             make_proc(5, "other", ppid=1)]
    by_pid = {p.pid: p for p in procs}
    assert active_tree(by_pid, 3) == {1, 2, 3, 4}
    assert active_tree(by_pid, 99) == set()


def test_closes_idle_junk_and_counts_saved_ram(world):
    snap = world.tick(2)
    assert world.closer.asked == [] and world.reason(snap, "GoogleUpdate.exe") == "busy"
    snap = world.tick()
    assert world.closer.asked == ["GoogleUpdate.exe"]
    assert snap["closing"] == ["GoogleUpdate.exe"] and snap["saved_total"] == 0
    world.remove(200)
    snap = world.tick()
    assert snap["saved_session"] == 50 * MB and snap["closed_total"] == 1
    assert snap["closed"][0]["name"] == "GoogleUpdate.exe" and snap["closing"] == []
    # Totals survive a restart.
    world.paths.state.write_text(json.dumps(snap))
    assert Engine(world.paths, sampler=world.sample).saved_total == 50 * MB


def test_never_touches_foreground_tree_protected_or_unlisted(world):
    snap = world.tick(5)
    assert world.closer.asked == ["GoogleUpdate.exe"]
    assert world.reason(snap, "crashpad_handler") == "in use"
    assert world.reason(snap, "firefox") == "in use"
    assert world.reason(snap, "spotify") == "not on close list"
    assert world.reason(snap, "systemd") == "protected"
    assert snap["focus"] == "firefox"


def test_close_list_and_protect_list(world):
    config.save(world.paths.config, dict(config.DEFAULTS, close_list=["spotify", "systemd"],
                                         protect_list=["googleupdate"]))
    snap = world.tick(4)
    assert world.closer.asked == ["spotify"]
    assert world.reason(snap, "GoogleUpdate.exe") == "protected"
    assert world.reason(snap, "systemd") == "protected"


def test_busy_small_and_new_processes_are_kept(world):
    world.procs += [make_proc(400, "jusched", 80 * MB, cpu=30.0),
                    make_proc(401, "jucheck", 2 * MB),
                    make_proc(402, "AdobeARM", 80 * MB, create=world.now),
                    make_proc(403, "acrotray", 80 * MB, cpu=None)]
    world.remove(200)
    snap = world.tick(4)
    assert world.closer.asked == []
    assert world.reason(snap, "jusched") == "busy"
    assert world.reason(snap, "jucheck") == "too small"
    assert world.reason(snap, "AdobeARM") == "just started"
    assert world.reason(snap, "acrotray") == "busy"


@pytest.mark.parametrize("setup,note", [
    (lambda w: w.paths.pause.write_text("x"), "paused"),
    (lambda w: config.save(w.paths.config, dict(config.DEFAULTS, mode="observe")), "observe only"),
    (lambda w: w.procs.append(make_proc(500, "apt-get", MB)), "waiting: apt-get is running"),
    (lambda w: setattr(w, "percent", 30), "RAM is fine"),
])
def test_gates_block_closing(world, setup, note):
    setup(world)
    snap = world.tick(5)
    assert world.closer.asked == []
    assert snap["note"] == note and snap["would_close"] == ["GoogleUpdate.exe"]


def test_process_that_ignores_close_is_left_running_then_retried(world):
    snap = world.tick(3)
    assert snap["closing"] == ["GoogleUpdate.exe"]
    world.now += 60
    snap = world.tick()
    assert snap["closing"] == [] and snap["saved_total"] == 0
    assert world.reason(snap, "GoogleUpdate.exe") == "retry later"
    world.now += 11 * 60
    world.tick()
    assert world.closer.asked == ["GoogleUpdate.exe"] * 2


def test_respawning_junk_is_given_up_on(world):
    for pid in (200, 201, 202, 203):
        world.procs = [p for p in world.procs if p.norm != "googleupdate"]
        world.procs.append(make_proc(pid, "GoogleUpdate.exe", 60 * MB))
        world.now += 11 * 60
        snap = world.tick(4)
    assert len(world.closer.asked) == 3
    assert world.reason(snap, "GoogleUpdate.exe") == "keeps coming back"


def test_same_named_instances_close_together(world):
    world.procs.append(make_proc(201, "GoogleUpdate.exe", 60 * MB))
    world.tick(3)
    assert world.closer.asked == ["GoogleUpdate.exe"] * 2


def test_no_permission_is_reported_and_not_retried(tmp_path):
    world = World(tmp_path, closer=FakeCloser((False, 0, "no permission")))
    snap = world.tick(6)
    assert world.closer.asked == ["GoogleUpdate.exe"]
    assert world.reason(snap, "GoogleUpdate.exe") == "no permission"


def test_own_widget_in_foreground_keeps_previous_focus(world):
    world.tick()
    world.fg = os.getpid()
    snap = world.tick()
    assert snap["focus"] == "firefox"
    world.fg = None
    assert world.tick()["focus"] == "firefox"


def test_panel_text_and_mood(world):
    world.tick(3)
    world.remove(200)
    text = panel_text(world.tick())
    assert text["ram"] == "RAM 80%   6.0 GB of 8.0 GB used"
    assert text["saved"].startswith("Saved 50 MB") and text["closed_title"] == "Closed (1)"
    assert "GoogleUpdate.exe" in text["closed"] and "firefox" in text["kept"]
    assert text["focus"] == "Working in: firefox"
    assert panel_text({})["closed"] == "nothing yet"
    assert fmt_bytes(1536 * MB) == "1.5 GB"
    assert mood({"mem": {"percent": 95}}, 60) == "high"
    assert mood({"mem": {"percent": 70}}, 60) == "busy"
    assert mood({"mem": {"percent": 10}}, 60) == "ok"
    assert mood({"paused": True}, 60) == "paused"


def test_cli_lists_scan_and_status(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("SMARTRAM_HOME", str(tmp_path))
    assert main(["allow", "Spotify.exe"]) == 0
    assert main(["protect", "slack"]) == 0
    assert main(["allow", "explorer.exe"]) == 2
    cfg, _ = config.load(tmp_path / "config.json")
    assert cfg["close_list"] == ["spotify"] and cfg["protect_list"] == ["slack"]
    assert main(["protect", "spotify"]) == 0 and main(["forget", "slack"]) == 0
    cfg, _ = config.load(tmp_path / "config.json")
    assert cfg["close_list"] == [] and cfg["protect_list"] == ["spotify"]
    assert main(["pause"]) == 0 and (tmp_path / "guardian.pause").exists()
    assert main(["resume"]) == 0 and not (tmp_path / "guardian.pause").exists()
    capsys.readouterr()
    assert main(["scan"]) == 0
    snap = json.loads(capsys.readouterr().out)
    assert snap["mem"]["total"] > 0 and snap["kept"] and snap["closed_total"] == 0
    assert main(["status"]) == 3
    assert "not running" in capsys.readouterr().out


def test_background_start_status_stop(tmp_path):
    env = dict(os.environ, SMARTRAM_HOME=str(tmp_path))
    run = lambda *a: subprocess.run([sys.executable, "-m", "smartram"] + list(a), env=env,
                                    stdout=subprocess.PIPE, universal_newlines=True, timeout=60)
    try:
        assert run("start", "--no-widget").returncode == 0
        assert run("start", "--no-widget").stdout.strip() == "Already running."
        status = run("status")
        assert status.returncode == 0 and "running (pid" in status.stdout
    finally:
        assert run("stop").returncode == 0
    assert run("status").returncode == 3
    assert not (tmp_path / "guardian.pid").exists()


@pytest.mark.skipif(sys.platform == "win32", reason="registry is covered by the CI smoke test")
def test_autostart_files(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    assert not autostart.is_enabled()
    path = autostart.enable()
    assert autostart.is_enabled() and "smartram" in open(path, "rb").read().decode()
    autostart.disable()
    assert not autostart.is_enabled()
