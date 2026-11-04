# chr-tpms

Register new tyre pressure (TPMS) sensors in a Toyota C-HR from your computer, using a cheap Bluetooth OBD adapter.

New wheels or new sensors? The car will show the TPMS warning light until it learns the new sensor IDs. Workshops charge for this. With `chr-tpms` you can do it yourself in a few minutes:

1. **Read** the sensor IDs the car currently knows.
2. **Write** the IDs of your new sensors.
3. **Verify:** the tool reads them back and only reports success if they match.

> [!WARNING]
> This tool writes to your car's TPMS control unit. It has been tested on **one car with one adapter** (see [Compatibility](#compatibility)). It is careful (see [Safety](#safety)), but you use it at your own risk. Keep your backup files.

## What you need

- A **Toyota C-HR** (tested: 2023, first generation)
- A **Bluetooth Low Energy ELM327 adapter**. Tested: [Veepeak OBDCheck BLE](https://veepeak.com/products/obdcheck-ble)
- A **Mac** with Python 3.11 or newer. Linux and Windows may work but are untested.
- The **IDs of your new sensors**. They are printed on the sensor or its box, usually 7 or 8 hexadecimal characters such as `5D10A01`.

## Install

```sh
git clone https://github.com/roosht3/chr-tpms.git
cd chr-tpms
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
chr-tpms --help
```

On macOS, allow Bluetooth for your terminal app when asked (System Settings → Privacy & Security → Bluetooth).

> [!TIP]
> Do not put the project in an iCloud Drive folder. iCloud breaks Python virtual environments, and you get timeouts or `ModuleNotFoundError: No module named 'chr_tpms'`.

## Usage

**Before you start:** plug the adapter into the OBD port (under the dashboard, driver's side), switch the **ignition on** (the engine can stay off), and **close every other OBD app** on your phone. A Bluetooth adapter only talks to one device at a time.

### 1. Find your adapter

```sh
chr-tpms scan
```

Look for the entry named `VEEPEAK` (or similar) and copy its `device` value:

```json
{
  "device": "1A2B3C4D-5E6F-7A8B-9C0D-1E2F3A4B5C6D",
  "name": "VEEPEAK",
  ...
}
```

On macOS this is a UUID, not a MAC address. Below it is written as `DEVICE`.

### 2. Read the current IDs

```sh
chr-tpms read --device DEVICE --protocol can
```

```json
{
  "protocol": "can",
  "bank": "main",
  "ids": ["01A2B3C4", "01A2B3C5", "01A2B3C6", "01A2B3C7"],
  "backup": "backups/20261104T143718.400304Z.json"
}
```

This only reads, nothing is changed. It saves a backup in `backups/`.

Not sure which protocol your car uses? Run `chr-tpms probe --device DEVICE`. It tries both `can` and `uds` and tells you which one answered.

### 3. Preview the write

Put your **current** IDs after `--expect` and your **new** IDs after `--ids`:

```sh
chr-tpms write --device DEVICE --protocol can \
  --expect 01A2B3C4 01A2B3C5 01A2B3C6 01A2B3C7 \
  --ids    5D10A01  5D10A02  5D10A03  5D10A04
```

Without `--execute` nothing is sent. You only see the request that would be sent.

### 4. Write

Add `--execute` to the same command:

```sh
chr-tpms write --device DEVICE --protocol can \
  --expect 01A2B3C4 01A2B3C5 01A2B3C6 01A2B3C7 \
  --ids    5D10A01  5D10A02  5D10A03  5D10A04 \
  --execute
```

```json
{
  "status": "verified",
  "ids": ["05D10A01", "05D10A02", "05D10A03", "05D10A04"],
  ...
}
```

`"status": "verified"` means the car accepted the new IDs and reading them back matched exactly.

### 5. Finish in the car

1. Inflate all tyres to the pressure on the driver's door sticker.
2. If your car has a TPMS **SET** button (under the steering wheel or in the glovebox), hold it with the ignition on until the light blinks 3 times.
3. Drive for 10–20 minutes. The TPMS light should go out.

## Safety

The tool is deliberately cautious:

- **It checks before it writes.** It reads the car first and refuses to write if the IDs don't match `--expect`. This prevents writing to the wrong car or from stale information.
- **It always makes a backup** before writing (`backups/before-write-*.json`).
- **It writes once.** If anything is unclear (connection lost, no clear answer), it stops and tells you. It never retries a write automatically.
- **It verifies.** Success is only reported after reading the new IDs back.
- **It keeps a log.** Every adapter command and answer is saved in `sessions/`.

To undo a change, run `write` again with your old IDs from the backup file.

> [!NOTE]
> Backups and logs contain your sensor IDs and adapter ID. Remove them before sharing.

## Compatibility

| Car | Adapter | Protocol | Read | Write |
|---|---|---|---|---|
| Toyota C-HR 2023 (1st gen, EU) | Veepeak OBDCheck BLE (ELM327 v1.5) | `can` | ✅ | ✅ |

Other Toyota models with the same TPMS unit may work but haven't been tested. If you try it, please [report your result](../../issues/new?template=compatibility_report.md), even if it failed. That's the most useful contribution you can make.

## Troubleshooting

| Problem | What to do |
|---|---|
| `Adapter not found` | Ignition on? Another app (or your phone) connected to the adapter? Turn your phone's Bluetooth off. Unplug the adapter for 10 seconds. |
| `the adapter stopped responding` | The adapter fell asleep. Unplug it for 10 seconds, plug it back in, and try again. |
| `Neither TPMS profile returned a complete ID set` | Your car may use a protocol this tool doesn't support yet. Please open a compatibility report with the log file from `sessions/`. |
| `Current IDs differ from --expect` | Run `read` again and copy the IDs exactly, in the same order. |
| `Write was attempted but could not be verified` | Do **not** repeat the write right away. Run `read` to see what the car has now. |
| `BLE UART is ambiguous` | Run `chr-tpms inspect --device DEVICE` and pass the UUIDs with `--write-char` and `--notify-char`. |

## Limitations

- Only the main sensor set (4 or 5 sensors). The second set (e.g. winter wheels) is not supported.
- You cannot change the number of sensors.
- It registers IDs in the car. It cannot program blank sensors or assign wheel positions.
- No tyre pressure readout, no clearing of fault codes.

## How it works

The adapter is driven as a raw CAN interface. The TPMS unit sits at CAN ID `750` with extended address `2A`. See [docs/protocol.md](docs/protocol.md) for the request bytes and adapter details.

## Contributing

Compatibility reports, bug fixes and support for more cars are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

[MIT](LICENSE). Not affiliated with or endorsed by Toyota. All trademarks belong to their owners.
