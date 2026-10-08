"""Client tests against a fake receiver on a local TCP port."""

import asyncio
import unittest

from load import client as c
from load import protocol as p

INPUT_REPLY = bytes.fromhex("02 07 A8 82 00 1A 30 21 00 64")
MODEL_REPLY = bytes.fromhex("02 10 A8 A0 53 54 52 2D 44 41 33 36 30 30 45 53 20 20 5C")
VOLUME_REPLY = bytes.fromhex("02 06 A8 92 00 01 D1 00 EE")
SOUND_REPLY = bytes.fromhex("02 04 AB 82 23 00 AC")


class FakeReceiver:
    """Answers like the real thing; `external_control` False refuses everything."""

    def __init__(self) -> None:
        self.external_control = True
        self.powered = True
        self.received: list[bytes] = []
        self.writers: list[asyncio.StreamWriter] = []
        self.server: asyncio.Server | None = None

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        assert self.server
        self.server.close()
        for writer in self.writers:
            writer.close()
        await self.server.wait_closed()

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.writers.append(writer)
        parser = p.FrameParser()
        while data := await reader.read(256):
            for item in parser.feed(data):
                if isinstance(item, p.Frame):
                    self.received.append(bytes([item.pdc]) + item.data)
                    writer.write(self._answer(item))
            await writer.drain()

    def _answer(self, frame: p.Frame) -> bytes:
        command = bytes([frame.pdc]) + frame.data
        if not self.external_control:
            return bytes([p.Reply.NACK.value])
        if command == p.cmd_power(True):
            self.powered = True
            return bytes([p.Reply.ACK.value]) + INPUT_REPLY
        if command == p.cmd_power(False):
            self.powered = False
            return bytes([p.Reply.ACK.value])
        if command == p.Q_INPUT:
            return INPUT_REPLY if self.powered else bytes([p.Reply.NACK.value])
        if command == p.Q_MODEL:
            return MODEL_REPLY
        if command == p.Q_VOLUME:
            return VOLUME_REPLY
        if command == p.Q_SOUND_MODE:
            return SOUND_REPLY
        return bytes([p.Reply.ACK.value])

    def push(self, raw: bytes) -> None:
        for writer in self.writers:
            writer.write(raw)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.receiver = FakeReceiver()
        self.port = await self.receiver.start()

    async def asyncTearDown(self) -> None:
        await self.receiver.stop()

    async def start_client(self) -> tuple[c.SonyReceiverClient, asyncio.Task[None]]:
        client = c.SonyReceiverClient("127.0.0.1", self.port)
        task = asyncio.create_task(client.run())
        await self.wait_for(lambda: client.state.sound_mode is not None)
        return client, task

    async def wait_for(self, condition, timeout: float = 3.0) -> None:
        async with asyncio.timeout(timeout):
            while not condition():
                await asyncio.sleep(0.02)

    async def test_probe_returns_model(self) -> None:
        self.assertEqual(await c.async_probe("127.0.0.1", self.port), "STR-DA3600ES")

    async def test_probe_detects_external_control_off(self) -> None:
        self.receiver.external_control = False
        with self.assertRaises(c.ExternalControlDisabled):
            await c.async_probe("127.0.0.1", self.port)

    async def test_probe_cannot_connect(self) -> None:
        await self.receiver.stop()
        with self.assertRaises(c.CannotConnect):
            await c.async_probe("127.0.0.1", self.port)
        self.port = await self.receiver.start()  # for asyncTearDown

    async def test_initial_state_is_read(self) -> None:
        client, task = await self.start_client()
        self.assertTrue(client.available)
        self.assertEqual(
            client.state,
            c.ReceiverState(True, 0x1A, False, -47.0, 0x23, "STR-DA3600ES"),
        )
        task.cancel()

    async def test_pushed_frames_update_state_and_notify(self) -> None:
        client, task = await self.start_client()
        calls: list[int] = []
        client.add_listener(lambda: calls.append(1))
        self.receiver.push(bytes.fromhex("02 06 A8 92 00 01 D0 80 6F"))
        self.receiver.push(bytes.fromhex("02 07 A8 82 00 1A 30 23 00 62"))
        await self.wait_for(lambda: client.state.muted)
        self.assertEqual(client.state.volume_db, -47.5)
        self.assertGreaterEqual(len(calls), 2)
        task.cancel()

    async def test_commands_are_sent_and_acked(self) -> None:
        client, task = await self.start_client()
        await client.async_set_input(0x10)
        await client.async_set_sound_mode(0x04)
        await client.async_set_mute(True)
        self.assertIn(p.cmd_input(0x10), self.receiver.received)
        self.assertIn(p.cmd_sound_mode(0x04), self.receiver.received)
        self.assertEqual(client.state.input_code, 0x10)
        self.assertTrue(client.state.muted)
        task.cancel()

    async def test_power_off_then_query_nack_means_off(self) -> None:
        client, task = await self.start_client()
        await client.async_set_power(False)
        self.assertFalse(client.state.power)
        await client.async_refresh()
        self.assertFalse(client.state.power)
        await client.async_set_power(True)
        self.assertTrue(client.state.power)
        task.cancel()

    async def test_volume_goes_to_target_in_half_db_steps(self) -> None:
        client, task = await self.start_client()
        self.receiver.received.clear()
        client.set_volume_db(-45.5)  # from -47.0: three steps up
        await self.wait_for(lambda: len(self.receiver.received) == 3)
        self.assertEqual(set(self.receiver.received), {p.cmd_volume_step(True)})
        task.cancel()

    async def test_rejected_command_raises(self) -> None:
        client, task = await self.start_client()
        self.receiver.external_control = False
        with self.assertRaises(c.CommandRejected):
            await client.async_set_mute(True)
        task.cancel()

    async def test_reconnects_after_connection_loss(self) -> None:
        client, task = await self.start_client()
        for writer in self.receiver.writers:
            writer.close()
        await self.wait_for(lambda: not client.available)
        await self.wait_for(lambda: client.available, timeout=5)
        task.cancel()


if __name__ == "__main__":
    unittest.main()
