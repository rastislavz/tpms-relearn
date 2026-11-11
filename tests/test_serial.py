import asyncio
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tpms_relearn.cli import connection, parser, run
from tpms_relearn.protocol import ProtocolError
from tpms_relearn.transport import SerialElm


class Logger:
    def __init__(self):
        self.records = []

    def record(self, **data):
        self.records.append(data)


class Port:
    def __init__(self, replies=(), partial=False):
        self.replies = list(replies)
        self.writes = []
        self.partial = partial
        self.closed = False
        self.timeout = 0.001

    @property
    def in_waiting(self):
        return 0

    def read(self, size):
        if self.replies:
            return self.replies.pop(0)
        time.sleep(min(self.timeout, 0.001))
        return b""

    def write(self, data):
        self.writes.append(data)
        return len(data) - int(self.partial)

    def close(self):
        self.closed = True


class SerialTests(unittest.IsolatedAsyncioTestCase):
    async def test_fragmented_response_and_echo(self):
        port = Port([b"ATI\rELM", b"327 v1.5\r", b">"])
        log = Logger()
        elm = SerialElm(port, log)
        self.assertEqual(await elm.command("ATI"), "ELM327 v1.5")
        self.assertEqual(port.writes, [b"ATI\r"])
        self.assertEqual(log.records[-1]["rx"], "ATI\rELM327 v1.5\r")

    async def test_timeout_disables_further_commands(self):
        port = Port([b"partial"])
        log = Logger()
        elm = SerialElm(port, log)
        with self.assertRaises(TimeoutError):
            await elm.command("ATI", timeout=0.01)
        with self.assertRaisesRegex(ProtocolError, "desynchronized"):
            await elm.command("ATI")
        self.assertEqual(len(port.writes), 1)
        self.assertEqual(log.records[-1]["incomplete_rx"], "partial")

    async def test_partial_write_is_not_retried(self):
        port = Port(partial=True)
        elm = SerialElm(port, Logger())
        with self.assertRaisesRegex(ProtocolError, "Incomplete serial write"):
            await elm.command("ATI")
        self.assertTrue(elm.broken)
        self.assertEqual(len(port.writes), 1)

    async def test_disconnect_is_not_retried(self):
        port = Port()
        port.read = lambda size: (_ for _ in ()).throw(OSError("Disconnected"))
        elm = SerialElm(port, Logger())
        with self.assertRaises(OSError):
            await elm.command("ATI")
        self.assertTrue(elm.broken)
        self.assertEqual(len(port.writes), 1)

    async def test_cancellation_drains_worker(self):
        port = Port()
        elm = SerialElm(port, Logger())
        task = asyncio.create_task(elm.command("ATI", timeout=0.05))
        await asyncio.sleep(0.01)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(elm.broken)
        count = len(port.writes)
        await asyncio.sleep(0.01)
        self.assertEqual(len(port.writes), count)

    async def test_connection_closes_after_failure(self):
        port = Port()
        args = parser().parse_args(["probe", "--transport", "serial", "--device", "COM5"])
        with patch("tpms_relearn.cli.serial_module", return_value=SimpleNamespace(Serial=lambda *a, **k: port)):
            with self.assertRaisesRegex(RuntimeError, "failed"):
                async with connection(args, Logger()):
                    raise RuntimeError("failed")
        self.assertTrue(port.closed)

    async def test_offline_write_never_opens_port(self):
        args = parser().parse_args(["write", "--transport", "serial", "--device", "COM5", "--protocol", "can", "--expect", "1111111", "2222222", "3333333", "4444444", "--ids", "5555555", "6666666", "7777777", "8888888"])
        with patch("tpms_relearn.cli.serial_module", side_effect=AssertionError("Opened serial")), patch("tpms_relearn.cli.emit") as emit:
            await run(args)
        self.assertIn("nothing sent", emit.call_args.args[0]["mode"])

    async def test_serial_scan_does_not_open_ports(self):
        import serial
        ports = [SimpleNamespace(device="COM5", description="OBDII", hwid="n/a")]
        args = parser().parse_args(["scan", "--transport", "serial"])
        with patch("serial.tools.list_ports.comports", return_value=ports), patch.object(serial, "Serial", side_effect=AssertionError("Opened port")), patch("tpms_relearn.cli.emit") as emit:
            await run(args)
        self.assertEqual(emit.call_args.args[0][0]["device"], "COM5")


@unittest.skipIf(__import__("sys").platform == "win32", "POSIX pseudo-terminal test")
class SerialIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_pyserial_stream(self):
        import os
        import pty
        import serial
        import threading

        master, slave = pty.openpty()
        port = serial.Serial(os.ttyname(slave), timeout=0.1, write_timeout=1)
        received = []

        def adapter():
            request = bytearray()
            while b"\r" not in request:
                request.extend(os.read(master, 64))
            received.append(bytes(request))
            os.write(master, b"ATI\rELM")
            os.write(master, b"327 v1.5\r>")

        worker = threading.Thread(target=adapter, daemon=True)
        worker.start()
        try:
            self.assertEqual(await SerialElm(port, Logger()).command("ATI", timeout=1), "ELM327 v1.5")
            self.assertEqual(received, [b"ATI\r"])
        finally:
            port.close()
            worker.join(timeout=1)
            os.close(master)
            os.close(slave)
