# Contributing

Thanks for helping! The most valuable contributions, in order:

1. **Compatibility reports.** Tried it on your car? Tell us how it went, even if it failed.
2. **Bug fixes.**
3. **Support for more cars and adapters.**

## Reporting results from your car

Open a [compatibility report](../../issues/new?template=compatibility_report.md) and include:

- Car model, year, and market (e.g. "C-HR 2021, EU")
- Adapter name and what `Adapter:` showed (e.g. `ELM327 v1.5`)
- The command you ran and its output
- The log file from `sessions/`

> [!IMPORTANT]
> Log and backup files contain your **sensor IDs** and **adapter ID**. Replace them with `XXXXXXXX` before posting. Never post your VIN.

## Development setup

```sh
git clone https://github.com/roosht3/chr-tpms.git
cd chr-tpms
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python -m unittest discover -s tests -v
```

The tests use simulated adapter and ECU replies, so no car or adapter is needed. `chr-tpms plan` also works offline:

```sh
chr-tpms plan --protocol can 1111111 2222222 3333333 4444444
```

## Code layout

| File | What it does |
|---|---|
| `chr_tpms/cli.py` | Command-line interface |
| `chr_tpms/transport.py` | BLE connection, ELM327 commands, ISO-TP framing and flow control |
| `chr_tpms/protocol.py` | Byte-level encoding and decoding, no I/O |
| `chr_tpms/service.py` | Read, backup, and register logic |
| `docs/protocol.md` | What is sent to the car and what it answers |

## Rules for changes that touch the car

This tool writes to a car's control unit, so changes on the write path must stay conservative:

- **Never retry a write automatically.** An unclear outcome is reported, not repeated.
- **Always read and compare before writing**, and always back up first.
- **Reject anything unexpected.** Strange replies, wrong lengths or unknown layouts stop the operation. Don't guess.
- **Base new protocol support on real logs.** Include the log (with IDs removed) in the pull request and add the observed bytes to `docs/protocol.md`.
- **Add a test** with simulated replies for every new behaviour.

## Pull requests

- Keep each pull request focused on one thing.
- Run the tests before opening it.
- Match the existing style: small functions, few dependencies (only `bleak`), plain `unittest`.
- Update the README, `docs/protocol.md`, and `CHANGELOG.md` when behaviour changes.
- Say which car and adapter you tested on, or "not tested on a car".

## Code of conduct

Be kind and constructive. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
