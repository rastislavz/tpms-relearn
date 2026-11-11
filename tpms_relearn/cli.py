import argparse
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .protocol import ProtocolError, validate_ids, write_payload
from .service import detect, read_ids, register, save_backup
from .transport import Elm, SerialElm, Transcript, ToyotaLink, select_characteristics


def positive_int(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def serial_module():
    try:
        import serial
    except ImportError as exc:
        raise ProtocolError("Install the serial dependency first: python -m pip install pyserial") from exc
    return serial


@asynccontextmanager
async def connection(args, transcript):
    if args.transport == "serial":
        if args.write_char or args.notify_char:
            raise ProtocolError("--write-char and --notify-char apply only to BLE")
        serial = serial_module()
        # Opening is kept synchronous so cancellation cannot orphan an open port.
        port = serial.Serial(args.device, baudrate=args.baudrate, timeout=0.1, write_timeout=1)
        try:
            transcript.record(device=args.device, transport="serial", baudrate=args.baudrate, action=args.action)
            elm = SerialElm(port, transcript)
            yield elm
        finally:
            port.close()
        return
    try:
        from bleak import BleakClient, BleakScanner
    except ImportError as exc:
        raise ProtocolError("Install the BLE dependency first: python -m pip install bleak") from exc
    device = await BleakScanner.find_device_by_address(args.device, timeout=10)
    if device is None:
        raise ProtocolError("Adapter not found. Plug it in, turn the ignition on, close other OBD apps, and run scan again.")
    async with BleakClient(device, timeout=15) as client:
        transcript.record(device=device.address, name=device.name, action=args.action)
        if args.action == "inspect":
            emit([{"service": s.uuid, "characteristics": [{"uuid": c.uuid, "properties": c.properties} for c in s.characteristics]} for s in client.services])
            yield None
            return
        tx, rx = select_characteristics(client.services, args.write_char, args.notify_char)
        transcript.record(write_characteristic=tx.uuid, notify_characteristic=rx.uuid)
        elm = Elm(client, tx, rx, transcript)
        await elm.start()
        yield elm


def parser():
    p = argparse.ArgumentParser(prog="tpms-relearn", description="Read and register Toyota TPMS sensor IDs through a Bluetooth ELM327 adapter")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="action", required=True)
    scan = sub.add_parser("scan", help="List nearby BLE devices or local serial ports (no vehicle commands)")
    scan.add_argument("--transport", choices=("ble", "serial"), default="ble")
    plan = sub.add_parser("plan", help="Offline write-payload preview; does not connect")
    plan.add_argument("--protocol", choices=("can", "uds"), required=True)
    plan.add_argument("ids", nargs="+")
    for name in ("inspect", "probe", "read", "write"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--transport", choices=("ble", "serial"), default="ble", help="serial uses a paired Bluetooth Classic SPP port")
        cmd.add_argument("--device", required=True, help="BLE device ID, or serial port such as /dev/cu.OBDII-SPP, COM5, /dev/rfcomm0")
        cmd.add_argument("--baudrate", type=positive_int, default=38400, help="Serial baud rate (default: 38400)")
        cmd.add_argument("--write-char")
        cmd.add_argument("--notify-char")
        cmd.add_argument("--log", type=Path, help="New transcript file; existing files are never overwritten")
        if name in ("read", "write"):
            cmd.add_argument("--protocol", choices=("auto", "can", "uds") if name == "read" else ("can", "uds"), default="auto" if name == "read" else None, required=name == "write")
            cmd.add_argument("--backup", type=Path, help="New backup file")
        if name == "write":
            cmd.add_argument("--expect", nargs="+", required=True, help="Current IDs in their original order, from a successful read")
            cmd.add_argument("--execute", action="store_true", help="Actually register IDs; without this flag only prints an offline plan")
            cmd.add_argument("--ids", nargs="+", required=True, help="New IDs in desired registration order")
    return p


def emit(value):
    print(json.dumps(value, indent=2))


def stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


async def run(args):
    if args.action == "plan" or (args.action == "write" and not args.execute):
        emit({"mode": "offline preview; nothing sent", "protocol": args.protocol, "bank": "main", "ids": validate_ids(args.ids), "payload": write_payload(args.protocol, args.ids).hex(" ").upper()})
        return
    if args.action == "write":
        validate_ids(args.ids)
        validate_ids(args.expect)
    if args.action == "scan":
        if args.transport == "serial":
            serial_module()
            from serial.tools.list_ports import comports
            emit([{"device": p.device, "name": p.description, "hwid": p.hwid} for p in sorted(comports())])
        else:
            from bleak import BleakScanner
            devices = await BleakScanner.discover(timeout=8, return_adv=True)
            emit([{"device": d.address, "name": adv.local_name or d.name, "rssi": adv.rssi, "services": adv.service_uuids} for d, adv in devices.values()])
        return
    log_path = args.log or Path("sessions") / f"{stamp()}.jsonl"
    transcript = Transcript(log_path)
    print(f"Transcript: {log_path}", file=sys.stderr)
    try:
        async with connection(args, transcript) as elm:
            if args.action == "inspect":
                if elm is not None:
                    emit({"device": args.device, "transport": "serial", "baudrate": args.baudrate, "identity": await elm.command("ATI")})
                return
            identity = await elm.initialize()
            print(f"Adapter: {identity}", file=sys.stderr)
            link = ToyotaLink(elm)
            if args.action in ("read", "probe"):
                snapshot = await detect(link) if args.action == "probe" or args.protocol == "auto" else await read_ids(link, args.protocol)
                snapshot["adapter"] = args.device
                backup = getattr(args, "backup", None) or Path("backups") / f"{stamp()}.json"
                save_backup(snapshot, backup)
                emit({**snapshot, "backup": str(backup)})
            else:
                backup = args.backup or Path("backups") / f"before-write-{stamp()}.json"
                emit(await register(link, args.protocol, args.ids, args.expect, backup))
    finally:
        transcript.close()


def main():
    args = parser().parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print("Interrupted. If a write had started, read IDs again to establish its outcome.", file=sys.stderr)
        return 130
    except TimeoutError:
        print("Error: the adapter stopped responding. Unplug it for 10 seconds, plug it back in with the ignition on, and try again. If a write had started, read IDs again first.", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"Error: {exc or type(exc).__name__}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
