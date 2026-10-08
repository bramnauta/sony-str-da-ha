"""Asynchronous TCP client for Sony STR-DA ES receivers (no Home Assistant imports)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, replace

from .protocol import (
    Q_INPUT,
    Q_MODEL,
    Q_SOUND_MODE,
    Q_VOLUME,
    REPLY_INPUT,
    REPLY_MODEL,
    REPLY_SOUND_MODE,
    REPLY_VOLUME,
    Frame,
    FrameParser,
    Reply,
    build_frame,
    cmd_input,
    cmd_mute,
    cmd_power,
    cmd_sound_mode,
    cmd_volume_step,
    decode_frame,
    reply_key,
)

_LOGGER = logging.getLogger(__name__)

DEFAULT_PORT = 6001
CONNECT_TIMEOUT = 5.0
REPLY_TIMEOUT = 2.0
PARTIAL_FRAME_TIMEOUT = 1.0
MIN_INTERVAL = 0.1  # the receiver accepts a command every 100 ms
POLL_INTERVAL = 30.0
MAX_BACKOFF = 60.0
MAX_VOLUME_STEPS = 160


class ReceiverError(Exception):
    """Base class for receiver errors."""


class CannotConnect(ReceiverError):
    """The receiver does not answer on the control port."""


class ExternalControlDisabled(ReceiverError):
    """The port is open but every command is refused (External Control off)."""


class CommandRejected(ReceiverError):
    """The receiver answered NACK."""


class NotConnected(ReceiverError):
    """There is no connection to the receiver at the moment."""


@dataclass(frozen=True)
class ReceiverState:
    power: bool | None = None
    input_code: int | None = None
    muted: bool | None = None
    volume_db: float | None = None
    sound_mode: int | None = None
    model: str | None = None


@dataclass
class _Pending:
    reply: tuple[int, int] | None
    future: asyncio.Future[Reply | Frame]


async def async_probe(host: str, port: int = DEFAULT_PORT) -> str:
    """Return the model name, or raise CannotConnect / ExternalControlDisabled."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), CONNECT_TIMEOUT
        )
    except (OSError, TimeoutError) as err:
        raise CannotConnect(str(err)) from err
    try:
        writer.write(build_frame(Q_MODEL))
        await writer.drain()
        parser = FrameParser()
        async with asyncio.timeout(REPLY_TIMEOUT * 2):
            while True:
                data = await reader.read(256)
                if not data:
                    raise CannotConnect("connection closed")
                for item in parser.feed(data):
                    if item is Reply.NACK:
                        raise ExternalControlDisabled
                    if isinstance(item, Frame) and reply_key(item) == REPLY_MODEL:
                        return decode_frame(item).get("model") or "STR-DA"
    except TimeoutError as err:
        raise CannotConnect("no answer") from err
    except OSError as err:
        raise CannotConnect(str(err)) from err
    finally:
        writer.close()


class SonyReceiverClient:
    """Keeps one connection open, tracks state from pushed frames and polls."""

    def __init__(self, host: str, port: int = DEFAULT_PORT) -> None:
        self.host = host
        self.port = port
        self.state = ReceiverState()
        self._available = False
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()
        self._pending: _Pending | None = None
        self._next_send = 0.0
        self._listeners: list[Callable[[], None]] = []
        self._volume_task: asyncio.Task[None] | None = None

    # --- state --------------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._available

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def remove() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return remove

    def _notify(self) -> None:
        for listener in list(self._listeners):
            listener()

    def _set_available(self, available: bool) -> None:
        if self._available != available:
            self._available = available
            self._notify()

    def _update(self, **fields: object) -> None:
        new = replace(self.state, **fields)  # type: ignore[arg-type]
        if new != self.state:
            self.state = new
            self._notify()

    # --- connection ----------------------------------------------------------

    async def run(self) -> None:
        """Connect, keep the state fresh and reconnect forever (cancel to stop)."""
        delay = 1.0
        while True:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self.host, self.port), CONNECT_TIMEOUT
                )
            except (OSError, TimeoutError):
                self._set_available(False)
                await asyncio.sleep(delay)
                delay = min(delay * 2, MAX_BACKOFF)
                continue
            delay = 1.0
            self._writer = writer
            tasks = [
                asyncio.create_task(self._read_loop(reader)),
                asyncio.create_task(self._poll_loop()),
            ]
            self._set_available(True)
            try:
                await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            finally:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                if self._volume_task:
                    self._volume_task.cancel()
                self._writer = None
                writer.close()
                if (pending := self._pending) and not pending.future.done():
                    pending.future.set_exception(NotConnected("connection lost"))
            _LOGGER.debug("Connection to %s lost, reconnecting", self.host)
            self._set_available(False)
            await asyncio.sleep(1.0)

    async def _read_loop(self, reader: asyncio.StreamReader) -> None:
        parser = FrameParser()
        while True:
            try:
                if parser.has_partial:
                    data = await asyncio.wait_for(reader.read(256), PARTIAL_FRAME_TIMEOUT)
                else:
                    data = await reader.read(256)
            except TimeoutError:
                parser.reset()
                continue
            if not data:
                raise ConnectionError("receiver closed the connection")
            for item in parser.feed(data):
                self._handle(item)

    def _handle(self, item: Reply | Frame) -> None:
        pending = self._pending
        if isinstance(item, Reply):
            if pending and not pending.future.done() and (
                item is Reply.NACK or pending.reply is None
            ):
                pending.future.set_result(item)
            return
        updates = decode_frame(item)
        if updates:
            self._update(**updates)
        if (
            pending
            and not pending.future.done()
            and pending.reply is not None
            and reply_key(item) == pending.reply
        ):
            pending.future.set_result(item)

    async def _poll_loop(self) -> None:
        await self.async_refresh()
        while True:
            await asyncio.sleep(POLL_INTERVAL)
            await self.async_refresh()

    # --- requests --------------------------------------------------------------

    async def _request(
        self, payload: bytes, reply: tuple[int, int] | None = None
    ) -> Reply | Frame:
        writer = self._writer
        if writer is None:
            raise NotConnected
        loop = asyncio.get_running_loop()
        async with self._lock:
            if (wait := self._next_send - loop.time()) > 0:
                await asyncio.sleep(wait)
            future: asyncio.Future[Reply | Frame] = loop.create_future()
            self._pending = _Pending(reply, future)
            try:
                writer.write(build_frame(payload))
                await writer.drain()
                return await asyncio.wait_for(future, REPLY_TIMEOUT)
            finally:
                self._pending = None
                self._next_send = loop.time() + MIN_INTERVAL

    async def _command(self, payload: bytes) -> None:
        try:
            result = await self._request(payload)
        except TimeoutError as err:
            raise NotConnected("no answer from the receiver") from err
        if result is Reply.NACK:
            raise CommandRejected

    async def _query(self, payload: bytes, reply: tuple[int, int]) -> Reply | Frame | None:
        try:
            return await self._request(payload, reply)
        except TimeoutError:
            _LOGGER.debug("No answer from %s to query %s", self.host, payload.hex())
            return None

    async def async_refresh(self) -> None:
        """Query power (by way of the input), volume and sound mode."""
        result = await self._query(Q_INPUT, REPLY_INPUT)
        if result is Reply.NACK:
            self._update(power=False)
            return
        if result is None:
            return
        if self.state.model is None:
            await self._query(Q_MODEL, REPLY_MODEL)
        await self._query(Q_VOLUME, REPLY_VOLUME)
        await self._query(Q_SOUND_MODE, REPLY_SOUND_MODE)

    # --- commands --------------------------------------------------------------

    async def async_set_power(self, on: bool) -> None:
        await self._command(cmd_power(on))
        self._update(power=on)

    async def async_set_mute(self, muted: bool) -> None:
        await self._command(cmd_mute(muted))
        self._update(muted=muted)

    async def async_set_input(self, code: int) -> None:
        await self._command(cmd_input(code))
        self._update(input_code=code)

    async def async_set_sound_mode(self, code: int) -> None:
        await self._command(cmd_sound_mode(code))
        self._update(sound_mode=code)

    async def async_volume_step(self, up: bool, count: int = 1) -> None:
        for _ in range(count):
            await self._command(cmd_volume_step(up))

    def set_volume_db(self, target: float) -> None:
        """Step to a volume (the receiver has no direct-volume command we know)."""
        if self._volume_task and not self._volume_task.done():
            self._volume_task.cancel()
        self._volume_task = asyncio.get_running_loop().create_task(self._step_to(target))

    async def _step_to(self, target: float) -> None:
        current = self.state.volume_db
        if current is None:
            return
        steps = round((target - current) * 2)
        try:
            await self.async_volume_step(steps > 0, min(abs(steps), MAX_VOLUME_STEPS))
        except ReceiverError as err:
            _LOGGER.debug("Volume change to %s dB stopped: %s", target, err)
