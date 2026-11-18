#!/usr/bin/env python3
"""Offline checks: python3 -m unittest discover -s contrib -p test_helpers.py."""

import builtins
from pathlib import Path
import runpy
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from artnet import create_artdmx_packet, create_packet


class ArtNetPacketTest(unittest.TestCase):
    def test_header_matches_payload_and_padding(self):
        for size in (0, 1, 2, 255, 256, 511, 512):
            with self.subTest(size=size):
                data = bytearray(i % 256 for i in range(size))
                original = data[:]
                packet = create_artdmx_packet(data, universe=0x1234, sequence=9)
                length = int.from_bytes(packet[16:18], "big")
                self.assertEqual(packet[:8], b"Art-Net\0")
                self.assertEqual(packet[8:12], b"\0\x50\0\x0e")
                self.assertEqual(packet[12], 9)
                self.assertEqual(packet[14:16], b"\x34\x12")
                self.assertEqual(length, len(packet) - 18)
                self.assertEqual(length % 2, 0)
                self.assertGreaterEqual(length, 2)
                self.assertLessEqual(length, 512)
                self.assertEqual(packet[18:18 + size], data)
                self.assertEqual(packet[18 + size:], bytes(length - size))
                self.assertEqual(data, original)

    def test_oversized_payload_is_rejected(self):
        for size in (513, 600):
            with self.subTest(size=size), self.assertRaises(ValueError):
                create_artdmx_packet(bytes(size))

    def test_matrix_packet_remains_compatible(self):
        packet = create_packet([(0, 0, 255), (15, 15, 128)])
        self.assertEqual(len(packet), 18 + 256)
        self.assertEqual(packet[16:18], b"\x01\0")
        self.assertEqual(packet[18], 255)
        self.assertEqual(packet[-1], 128)


class OptionalDiscoveryTest(unittest.TestCase):
    def test_missing_zeroconf_disables_only_mdns(self):
        original_import = builtins.__import__

        def without_zeroconf(name, *args, **kwargs):
            if name == "zeroconf":
                raise ModuleNotFoundError("zeroconf intentionally unavailable")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=without_zeroconf):
            module = runpy.run_path(str(ROOT / "discover.py"))
        self.assertFalse(module["ZEROCONF_AVAILABLE"])
        self.assertEqual(module["discover_mdns"](timeout=0), [])

    def test_cli_help_works_without_optional_packages(self):
        for script in ("discover.py", "artnet.py"):
            with self.subTest(script=script):
                result = subprocess.run(
                    [sys.executable, "-B", "-S", str(ROOT / script), "--help"],
                    capture_output=True, text=True, timeout=10,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout)


if __name__ == "__main__":
    unittest.main()
