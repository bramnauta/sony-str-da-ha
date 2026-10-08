import unittest

from load import protocol as p


def h(text: str) -> bytes:
    return bytes.fromhex(text)


class FrameTests(unittest.TestCase):
    def test_frames_captured_from_a_real_receiver(self) -> None:
        self.assertEqual(p.build_frame(p.cmd_power(True)), h("02 04 A0 60 00 01 FB"))
        self.assertEqual(p.build_frame(p.Q_INPUT), h("02 03 A0 82 00 DB"))
        self.assertEqual(p.build_frame(p.Q_VOLUME), h("02 04 A0 92 00 01 C9"))
        self.assertEqual(p.build_frame(p.Q_SOUND_MODE), h("02 02 A3 82 D9"))
        self.assertEqual(p.build_frame(p.cmd_mute(True)), h("02 04 A0 53 00 01 08"))
        self.assertEqual(p.build_frame(p.cmd_mute(False)), h("02 04 A0 53 00 00 09"))

    def test_volume_step_commands_match_the_sony_table(self) -> None:
        self.assertEqual(p.cmd_volume_step(True), h("A0 52 00 02 00 01"))
        self.assertEqual(p.cmd_volume_step(False), h("A0 52 00 02 FF FF"))

    def test_parser_splits_ack_and_following_frame(self) -> None:
        items = p.FrameParser().feed(h("FD 02 07 A8 82 00 1A 30 21 00 64"))
        self.assertEqual(items[0], p.Reply.ACK)
        self.assertEqual(items[1], p.Frame(0xA8, h("82 00 1A 30 21 00")))

    def test_parser_handles_two_frames_and_split_reads(self) -> None:
        stream = h("02 06 A8 92 00 01 D1 00 EE 02 06 A8 92 00 01 D0 80 6F")
        parser = p.FrameParser()
        out = parser.feed(stream[:5])
        self.assertEqual(out, [])
        self.assertTrue(parser.has_partial)
        out = parser.feed(stream[5:])
        self.assertEqual([f.data[-2:] for f in out], [h("D1 00"), h("D0 80")])

    def test_parser_resyncs_after_garbage_and_bad_checksum(self) -> None:
        good = h("02 06 A8 92 00 01 D1 00 EE")
        bad = bytearray(good)
        bad[-1] ^= 0xFF
        items = p.FrameParser().feed(b"\x00\x42" + bytes(bad) + good)
        self.assertEqual(len(items), 1)
        self.assertIsInstance(items[0], p.Frame)

    def test_stx_inside_data_is_not_a_frame_start(self) -> None:
        payload = bytes([0xA8, 0x82, 0x00, 0x02, 0x30, 0xFD, 0x00])
        items = p.FrameParser().feed(p.build_frame(payload))
        self.assertEqual(items, [p.Frame(0xA8, payload[1:])])


class DecodeTests(unittest.TestCase):
    def frame(self, text: str) -> p.Frame:
        (item,) = p.FrameParser().feed(h(text))
        return item

    def test_input_status(self) -> None:
        got = p.decode_frame(self.frame("02 07 A8 82 00 1A 30 21 00 64"))
        self.assertEqual(got, {"power": True, "input_code": 0x1A, "muted": False})

    def test_input_status_muted(self) -> None:
        got = p.decode_frame(self.frame("02 07 A8 82 00 1A 30 23 00 62"))
        self.assertTrue(got["muted"])

    def test_volume_in_half_db_steps(self) -> None:
        self.assertEqual(p.decode_frame(self.frame("02 06 A8 92 00 01 D1 00 EE")), {"volume_db": -47.0})
        self.assertEqual(p.decode_frame(self.frame("02 06 A8 92 00 01 D0 80 6F")), {"volume_db": -47.5})
        self.assertEqual(p.decode_frame(self.frame("02 06 A8 92 00 01 CE 80 71")), {"volume_db": -49.5})

    def test_sound_mode_and_model(self) -> None:
        self.assertEqual(p.decode_frame(self.frame("02 04 AB 82 23 00 AC")), {"sound_mode": 0x23})
        model = self.frame("02 10 A8 A0 53 54 52 2D 44 41 33 36 30 30 45 53 20 20 5C")
        self.assertEqual(p.decode_frame(model), {"model": "STR-DA3600ES"})

    def test_zone_2_and_unknown_frames_are_ignored(self) -> None:
        self.assertEqual(p.decode_frame(self.frame("02 07 A8 82 01 32 32 00 00 6A")), {})
        self.assertEqual(p.decode_frame(self.frame("02 04 AD 82 00 01 CC")), {})


class SourceTests(unittest.TestCase):
    def test_parse_sources(self) -> None:
        self.assertEqual(p.parse_sources("HDMI 1=1A\n\n  Apple TV = 0x1b \n"), {0x1A: "HDMI 1", 0x1B: "Apple TV"})

    def test_parse_sources_rejects_garbage(self) -> None:
        for text in ("HDMI", "=1A", "HDMI=ZZ", "HDMI=100"):
            with self.assertRaises(ValueError, msg=text):
                p.parse_sources(text)

    def test_default_sources_exist(self) -> None:
        for code in p.DEFAULT_SOURCE_CODES:
            self.assertIn(code, p.INPUT_NAMES)


if __name__ == "__main__":
    unittest.main()
