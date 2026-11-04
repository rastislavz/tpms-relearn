"""Pure codecs, independently implemented from the inspected native routines."""
import re


class ProtocolError(RuntimeError):
    pass


class NegativeResponse(ProtocolError):
    def __init__(self, service, code):
        self.service, self.code = service, code
        super().__init__(f"ECU rejected service {service:02X}: NRC {code:02X}")


def sensor_id(value):
    value = value.upper()
    if not re.fullmatch(r"(?:[0-9A-F]{7}|0[0-9A-F]{7})", value):
        raise ValueError(f"Invalid Toyota sensor ID {value!r}: use 7 hex digits (or 8 starting with 0)")
    return value.zfill(8)


def validate_ids(values):
    ids = [sensor_id(v) for v in values]
    if len(ids) not in (4, 5):
        raise ValueError("Exactly 4 or 5 IDs are required")
    if len(set(ids)) != len(ids) or "00000000" in ids or "0FFFFFFF" in ids:
        raise ValueError("IDs must be distinct and cannot be empty/sentinel IDs")
    return ids


def write_payload(protocol, values):
    ids = validate_ids(values)
    raw = [bytes.fromhex(v) for v in ids]
    if protocol == "uds":
        return b"\x2e\x20\x03" + bytes([len(ids)]) + b"".join(raw) + (b"\0" * 4 if len(ids) == 4 else b"")
    if protocol == "can":
        # Main bank only. The secondary-bank flag would occupy bit 7 of byte 2.
        return b"\x3b\x0e\x00" + bytes([len(ids)]) + b"".join(b"\0\0" + v for v in raw) + (bytes.fromhex("00000FFFFFFF") if len(ids) == 4 else b"")
    raise ValueError("Protocol must be can or uds")


def positive(payload, request):
    if payload[:1] == b"\x7f":
        if len(payload) != 3 or payload[1] != request[0]:
            raise ProtocolError("Malformed or unrelated negative response")
        raise NegativeResponse(payload[1], payload[2])
    prefix = bytes([request[0] + 0x40]) + request[1:]
    if not payload.startswith(prefix):
        raise ProtocolError(f"Expected {prefix.hex().upper()}, got {payload.hex().upper()}")
    return payload[len(prefix):]


def frames(payload, address=0x2A):
    if not 1 <= len(payload) <= 4095:
        raise ValueError("ISO-TP payload length out of range")
    if len(payload) <= 6:
        return [bytes([address, len(payload)]) + payload]
    result = [bytes([address, 0x10 | (len(payload) >> 8), len(payload) & 255]) + payload[:5]]
    for i, start in enumerate(range(5, len(payload), 6), 1):
        result.append(bytes([address, 0x20 | (i & 15)]) + payload[start:start + 6])
    return result


def parse_frames(text, rx_id=0x758, address=0x2A):
    result = []
    for line in text.replace("\r", "\n").splitlines():
        line = "".join(line.split()).upper()
        if not line or line in {"OK", "NODATA"} or line.startswith("SEARCHING"):
            continue
        if not re.fullmatch(r"[0-9A-F]+", line) or len(line) < 7 or len(line) % 2 != 1:
            raise ProtocolError(f"Unexpected ELM output: {line}")
        if int(line[:3], 16) != rx_id:
            raise ProtocolError(f"Unexpected CAN sender: {line[:3]}")
        data = bytes.fromhex(line[3:])
        if not 2 <= len(data) <= 8 or data[0] != address:
            raise ProtocolError(f"Unexpected extended-address CAN frame: {line}")
        result.append(data[1:])
    return result


def reassemble(parts):
    messages, buffer, expected, sequence = [], None, 0, 1
    for part in parts:
        kind = part[0] >> 4
        if kind == 0:
            size = part[0] & 15
            if buffer is not None or not 1 <= size <= 6 or len(part) < size + 1:
                raise ProtocolError("Invalid ISO-TP single frame")
            messages.append(part[1:1 + size])
        elif kind == 1:
            if buffer is not None or len(part) != 7:
                raise ProtocolError("Invalid ISO-TP first frame")
            expected = ((part[0] & 15) << 8) | part[1]
            if expected <= 6:
                raise ProtocolError("Invalid ISO-TP length")
            buffer, sequence = bytearray(part[2:]), 1
        elif kind == 2:
            if buffer is None or part[0] & 15 != sequence or len(part) < 2:
                raise ProtocolError("Missing or out-of-order ISO-TP frame")
            if len(buffer) + len(part) - 1 < expected and len(part) != 7:
                raise ProtocolError("Short intermediate ISO-TP frame")
            buffer.extend(part[1:])
            sequence = (sequence + 1) & 15
            if len(buffer) >= expected:
                messages.append(bytes(buffer[:expected]))
                buffer = None
        else:
            raise ProtocolError("Unexpected ISO-TP frame type")
    if buffer is not None:
        raise ProtocolError("Incomplete ISO-TP response; no IDs accepted")
    return messages
