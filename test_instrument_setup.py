import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from tkinter import ttk
from unittest.mock import patch

from instrument_setup import preset_path, read_setup
from scope import Scope, SAMPLE_RECORD


class SetupTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name)
        (self.directory / "probe-ch2").write_text("10")
        self.start()
        self.addCleanup(lambda: self.panel.close())

    def start(self):
        self.panel = Scope(tk.Tk(), self.directory)
        self.panel.root.update_idletasks()

    def save(self, name, confirm=True):
        with patch("scope.simpledialog.askstring", return_value=name), \
             patch("scope.messagebox.askyesno", return_value=confirm):
            self.panel.save_preset()

    def recall(self, name, action="Recall"):
        self.panel.show_presets()
        window = self.panel.recall_window
        listing, buttons = window.winfo_children()
        index = listing.get(0, "end").index(name)
        listing.selection_clear(0, "end")
        listing.selection_set(index)
        for button in buttons.winfo_children():
            if isinstance(button, ttk.Button) and button["text"] == action:
                button.invoke()
                return
        self.fail(action)

    def test_recall_and_restart(self):
        panel = self.panel
        first = panel.capture_setup()
        self.assertEqual(first["probes"], [1,10,1,1])
        self.save("A")
        second = dict(first, scales=[.2,.5,1.,2.], coupling=["AC","DC","DC","AC"],
                      visible=[False,True,False,True], probes=[10,1,100,1], positions=[1.,2.,3.,4.],
                      ch=1, input_mode="CH2 + CH4", time_div=5e-6, delay=1.5, source="CH2",
                      slope="Falling", mode="Normal", level=.2, acq_mode="Manual",
                      manual_rate=500000000, depth=32768, region="Visible window", average=4,
                      envelope="On", persistence="4 records", cursor="Time", cursor_x=[.1,.8],
                      cursor_y=[.2,.9])
        panel.apply_setup(second)
        self.assertAlmostEqual(panel.selected_time(), second["time_div"], places=16)
        second["time_div"] = panel.selected_time()
        self.save("I²C")
        self.assertEqual(read_setup(preset_path(self.directory,"I²C")), second)
        self.save("A", confirm=False)
        self.assertEqual(read_setup(preset_path(self.directory,"A")), first)
        self.save("A", confirm=True)
        self.assertEqual(read_setup(preset_path(self.directory,"A")), second)
        panel.apply_setup(panel.factory_setup)
        self.assertEqual(panel.probes,[1]*4)
        self.recall("I²C")
        self.assertEqual(panel.state,"Run")
        self.assertEqual(panel.capture_setup(),second)
        panel.single()
        self.recall("A")
        self.assertEqual(panel.state,"Stop")
        panel.show_presets()
        with patch("scope.messagebox.askyesno", return_value=True):
            self.recall("A","Delete…")
        panel.recall_window.destroy()
        self.assertFalse(preset_path(self.directory,"A").exists())
        panel.time.set("2 µs")
        expected = panel.capture_setup()
        panel.load_record(SAMPLE_RECORD)
        panel.fit_record()
        panel.single()
        self.assertEqual(panel.capture_setup(), expected)
        self.assertEqual(panel.state,"Single")
        panel.load_record(SAMPLE_RECORD)
        self.recall("I²C")
        self.assertEqual(panel.state,"Stop")
        panel.time.set("2 µs")
        panel.load_record(SAMPLE_RECORD)
        panel.close()
        self.start()
        self.assertEqual(self.panel.state,"Run")
        self.assertEqual(self.panel.capture_setup(),expected)
        self.assertEqual(read_setup(preset_path(self.directory,"I²C")),second)

    def test_autosave_before_quit(self):
        panel = self.panel
        panel.toggle()
        panel.time.set("50 µs")
        panel.select(1)
        panel.root.after(550, panel.root.quit)
        panel.root.mainloop()
        saved = read_setup(self.directory / "last-setup.json")
        self.assertEqual(saved, panel.capture_setup())
        panel.load_record(SAMPLE_RECORD)
        panel.time.set("2 µs")
        panel.toggle()
        self.assertEqual(panel.state, "Run")
        self.assertEqual(panel.capture_setup(), saved)

    def test_corrupt_setup(self):
        self.save("Damaged")
        path = preset_path(self.directory, "Damaged")
        path.write_text('{"version":1}')
        before = self.panel.capture_setup()
        with patch("scope.messagebox.showerror") as error:
            self.recall("Damaged")
            error.assert_called_once()
        self.assertEqual(self.panel.capture_setup(), before)
        self.panel.close()
        (self.directory / "last-setup.json").write_text("{")
        with patch("scope.messagebox.showwarning") as warn:
            self.start()
            warn.assert_called_once()
        self.assertEqual(self.panel.capture_setup(), self.panel.factory_setup)


if __name__ == "__main__":
    unittest.main()
