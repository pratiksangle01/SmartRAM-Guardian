# Contributing

Thanks for helping. Small, focused pull requests are the easiest to review.

## Set up

```sh
git clone https://github.com/pratiksangle01/SmartRAM-Guardian.git
cd SmartRAM-Guardian
python -m venv .venv
. .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e . pytest
python -m pytest -q
```

Useful while working:

```sh
python -m smartram scan      # what the guardian sees; closes nothing
python -m smartram doctor    # what works on this machine
python -m smartram run       # run in this terminal, Ctrl+C to stop
python tools/footprint.py    # CPU and memory the guardian itself uses
```

## Where things are

| File | What it holds |
| --- | --- |
| `smartram/rules.py` | protected names, background-junk names, installer names |
| `smartram/engine.py` | sampling, the decision to close, pacing |
| `smartram/foreground.py` | finding the app in use on each system |
| `smartram/widget.py` | the dolphin and its panel |
| `smartram/__main__.py` | the `smartram` commands |
| `tools/draw_pet.py` | redraws the dolphin artwork |
| `tools/make_demo.py` | regenerates the README pictures |

## Adding a background-junk name

Add the process name to `JUNK` in `smartram/rules.py`. A name belongs there only if
the process is a helper that holds memory without doing anything the user can see
(an updater, a crash handler, a telemetry agent), closing it loses no work, and it
is not a system component. When in doubt, leave it out: users can always add their
own with `smartram allow`.

Names that must never be closed go in `PROTECTED`.

## Rules for changes

- Safety first: a change must never make the guardian close the app in use, a protected process, or anything during an install.
- Every behaviour change comes with a test in `tests/test_smartram.py`.
- Keep the guardian light. `python tools/footprint.py` should not get worse.
- No new runtime dependencies beyond `psutil`.
