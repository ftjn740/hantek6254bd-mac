import tkinter as tk
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from scope import SAMPLE_RECORD, Scope


class TimeZeroTest(unittest.TestCase):
    def setUp(self):
        guard = patch("scope.BenchDriver", side_effect=AssertionError("Unexpected USB access"))
        guard.start()
        self.addCleanup(guard.stop)
        self.root = tk.Tk()
        self.panel = Scope(self.root, setup_dir=None)
        self.addCleanup(self.panel.close)
        self.canvas = self.panel.canvas
        self.root.update_idletasks()
        self.assertFalse(self.canvas.find_withtag("time_zero"))
        self.panel.load_record(SAMPLE_RECORD)
        self.root.update_idletasks()

    def check_marker(self, fraction):
        marker, = self.canvas.find_withtag("time_zero")
        x1, y1, x2, y2, x3, y3 = self.canvas.coords(marker)
        left, right = 32, self.canvas.winfo_width() - 24
        if fraction < 0 or fraction > 1:
            self.assertEqual(x1, left if fraction < 0 else right)
            self.assertEqual(x2, x3)
            self.assertTrue(x2 > x1 if fraction < 0 else x2 < x1)
            self.assertAlmostEqual(y1, (y2 + y3) / 2)
        else:
            self.assertAlmostEqual(x3, left + fraction * (right - left))
            self.assertAlmostEqual(x3, (x1 + x2) / 2)
            self.assertGreater(y3, max(y1, y2))
        return marker

    def test_triggered_record(self):
        wave = self.panel.record
        self.assertIsNotNone(wave.trigger_index)
        self.panel.fit_record()
        self.check_marker(wave.trigger_index / wave.depth)

    def test_auto_timeout_then_wait_and_stop(self):
        device = Mock(actual_rate=7_812_500, trigger_index=None)
        self.panel.device = device
        device.capture_edge.return_value = (self.panel.record.raw[:2], False, 0)
        self.panel.toggle()
        self.panel.time.set("20 µs")
        self.panel.acquire()
        self.assertIn("Auto timeout", self.panel.foot["text"])
        self.panel.delay.set(0.5)
        self.panel.shift_record()
        self.check_marker(0.45)

        record = self.panel.record
        self.panel.single()
        device.capture_edge.return_value = None
        self.panel.acquire()
        self.assertEqual(self.panel.indicator["text"], "ARMED")
        self.assertIs(self.panel.record, record)
        self.check_marker(0.45)
        self.panel.toggle()
        self.assertEqual(self.panel.state, "Stop")
        self.check_marker(0.45)

    def test_timeout_pan_resize_and_cursors(self):
        wave = replace(self.panel.record, trigger_index=None, rate=7_812_500, time_div=20e-6)
        with TemporaryDirectory() as directory:
            path = Path(directory) / "timeout.hk6254"
            wave.save(path)
            self.panel.load_record(path)
        for delay, fraction in ((0.5, 0.45), (5, 0), (5.1, -0.01), (-5, 1), (-5.1, 1.01)):
            with self.subTest(delay=delay):
                self.panel.delay.set(delay)
                self.panel.shift_record()
                self.check_marker(fraction)
        self.root.geometry("990x750")
        self.root.update_idletasks()
        for mode in ("Time", "Voltage"):
            self.panel.cursor.set(mode)
            self.panel.draw()
            marker = self.check_marker(1.01)
            overlaps = self.canvas.find_overlapping(*self.canvas.bbox(marker))
            self.assertFalse(any(self.canvas.type(item) == "text" for item in overlaps))
        self.panel.center_trigger()
        self.check_marker(0.5)


if __name__ == "__main__":
    unittest.main()
