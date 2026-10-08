# Sony STR-DA ES receiver for Home Assistant

> **Disclaimer**
>
> This project is entirely *vibe coded*: written with an AI assistant, tested only against one receiver (an STR-DA3600ES) and one Home Assistant installation, and not reviewed line by line.
>
> - **Use it at your own risk.** It sends commands to your receiver (power, volume, source). The author accepts no liability for damage to equipment, speakers, ears or anything else.
> - **There is no support.** No issue triage, no help with setup, no promised fixes or updates.
> - **Pull requests are welcome.** Fixes, other models and more tests are appreciated.

Custom integration for Sony's 2009/2010 ES receivers (tested on an **STR-DA3600ES**), controlled over the network with Sony's serial (RS-232C) protocol wrapped in TCP on port **6001**. Receivers from 2012 onwards use port 33335 with a similar protocol; those are untested.

It adds one `media_player` (main zone) with:

- power on/off, mute, volume (slider and up/down), source and sound mode;
- live updates: the receiver pushes volume, mute and input changes, so changes on the front panel or the remote show up at once (a poll every 30 s is a safety net);
- a volume slider that never goes above a configurable maximum (default -20 dB).

## Install

1. On the receiver: **Settings > Network > External Control = ON**. With it off, the port still accepts connections but refuses every command (the setup form tells you).
2. Copy `custom_components/sony_str_da` to `<config>/custom_components/` of Home Assistant, or add this repository to HACS as a custom repository (type *Integration*). Restart Home Assistant.
3. *Settings > Devices & services > Add integration > Sony STR-DA ES receiver*, enter the IP address (port 6001).

Give the receiver a fixed IP address.

## Sources

Sony's protocol table lists the classic inputs (Tuner, Phono, CD, Video 1-5, TV, DBS, DVD, ...) but not the HDMI inputs. The default source list has the classic ones. To add the others:

1. Select the input on the receiver.
2. Read its code from the `input_code` attribute of the media player (*Developer tools > States*).
3. *Configure* on the integration > *Extra sources*, one per line as `Name=Code`, for example `Apple TV=1A`.

The same screen sets the volume range of the slider.

## Limits

- Main zone only (zone 2/3 have the same commands, but are not wired up).
- The receiver has no power query. Power is derived from the input query: an answer means *on*, a refusal (NACK) means *off*, which is what Sony's spec describes for standby but is **not verified** on a real receiver in standby yet. Turning on from standby also needs the receiver to keep its network connection in standby, and not to be switched off at the mains; when the connection is gone the entity is *unavailable*.
- The receiver has no command that sets an absolute volume, so the slider steps in 0.5 dB commands (about 100 ms each).
- The receiver may only accept a limited number of simultaneous connections. This integration keeps one open.

## Protocol

Frame `02 BC PDC CMD data... CS`, `BC` = bytes after BC up to the last data byte, `CS` = two's complement of the sum from BC. `FD` = ACK, `FE` = NACK. Queries are the set command plus `0x40` (input `42` -> `82`, volume `52` -> `92`). See `custom_components/sony_str_da/protocol.py`.

## Tests

```
python -m unittest discover -s tests -t tests
```

The tests run without Home Assistant installed (the HA-specific files `__init__.py`, `config_flow.py` and `media_player.py` are not covered by them).
