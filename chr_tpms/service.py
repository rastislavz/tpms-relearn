import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .protocol import ProtocolError, positive, validate_ids, write_payload


async def read_ids(link, protocol):
    if protocol == "uds":
        request = bytes.fromhex("222002")
        data = positive(await link.request(request), request)
        if not data:
            raise ProtocolError("Missing sensor count")
        count = data[0] & 0x0F
    elif protocol == "can":
        request = bytes.fromhex("2106")
        data = positive(await link.request(request), request)
        if not data:
            raise ProtocolError("Missing sensor count")
        count = (data[0] & 0xE0) >> 5
    else:
        raise ValueError("Unknown protocol")
    if count not in (4, 5):
        raise ProtocolError(f"Unexpected sensor count {count}; refusing to infer a layout")
    ids = []
    for i in range(count):
        request = bytes([0x22]) + (0x1023 + i).to_bytes(2, "big") if protocol == "uds" else bytes([0x21, 0x0F + i])
        data = positive(await link.request(request), request)
        # A 2023 C-HR returns exactly four ID bytes after 61 xx; accept only
        # that observed layout.
        if len(data) != 4:
            raise ProtocolError(f"Unexpected sensor {i + 1} response length {len(data)}")
        offset = 0
        # Native reader uses mask 0FFFFFFF on these four bytes.
        value = int.from_bytes(data[offset:offset + 4], "big") & 0x0FFFFFFF
        ids.append(f"{value:08X}")
    return {"protocol": protocol, "bank": "main", "ids": validate_ids(ids)}


async def detect(link):
    errors = {}
    for protocol in ("uds", "can"):
        try:
            return await read_ids(link, protocol)
        except (ProtocolError, ValueError) as exc:
            errors[protocol] = str(exc)
    raise ProtocolError(f"Neither TPMS profile returned a complete ID set: {errors}. Save the transcript for analysis; K-Line is not implemented.")


def save_backup(snapshot, destination):
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        json.dump({"format": "chr-tpms-backup-v1", "created": datetime.now(timezone.utc).isoformat(), **snapshot}, file, indent=2)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())


async def register(link, protocol, ids, expected, backup_path):
    """One write attempt only; no automatic retry or rollback on uncertainty."""
    ids = validate_ids(ids)
    before = await read_ids(link, protocol)
    if before["ids"] != validate_ids(expected):
        raise ProtocolError("Current IDs differ from --expect. No write sent; read and review the car again.")
    if len(ids) != len(before["ids"]):
        raise ProtocolError("Changing the number of registered sensors is not supported")
    if ids == before["ids"]:
        return {"status": "unchanged", **before}
    save_backup(before, backup_path)
    session = bytes.fromhex("1003" if protocol == "uds" else "105F")
    # Check only the 50 service byte: a 2023 C-HR answers 10 5F with 50 01.
    positive(await link.request(session), session[:1])
    payload = write_payload(protocol, ids)
    try:
        response = await link.request(payload)
        # Write responses echo the service and identifier, not the new IDs.
        positive(response, payload[:3] if protocol == "uds" else payload[:2])
        after = await read_ids(link, protocol)
        if after["ids"] != ids:
            raise ProtocolError(f"Read-back differs: {after['ids']}")
    except Exception as exc:
        raise ProtocolError(f"Write was attempted but could not be verified: {exc}. Backup: {backup_path}. Do not repeat automatically; read IDs again or check with another TPMS tool.") from exc
    return {"status": "verified", "backup": str(backup_path), **after}
