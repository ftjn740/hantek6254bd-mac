import csv
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import Mock

from bench_driver import BenchDriver, lane_count
from instrument_setup import INPUT_MODES, validate
from scope import Scope
from waveform import Waveform, write_csv


class ChannelTests(unittest.TestCase):
    def test_register_routes(self):
        for group in filter(bool, INPUT_MODES.values()):
            for rate in (1250000, int(1e9/lane_count(group))):
                with self.subTest(group=group, rate=rate):
                    driver=BenchDriver.__new__(BenchDriver)
                    driver.settings=None
                    driver.calibration=bytes(1154)
                    driver.adc=Mock()
                    driver.bwrite=Mock()
                    driver.apply([.1]*4,["DC"]*4,rate,4096,group)
                    registers=dict(call.args for call in driver.adc.call_args_list)
                    mux=[(registers[r]>>shift)&255 for r in (0x3a,0x3b) for shift in (0,8)]
                    for lane,ch in enumerate(group):
                        start=lane*(4//driver.adc_inputs)
                        self.assertEqual(mux[start:start+4//driver.adc_inputs],[16>>ch]*(4//driver.adc_inputs))
                    driver.capture_edge(group[-1],False,128,True)
                    trigger=next(call.args[0] for call in driver.bwrite.call_args_list
                                 if call.args[0][0]==0x12)
                    self.assertEqual(trigger[-1],len(group)-1)

    def test_switches_and_sparse_record(self):
        p=Scope(tk.Tk(), setup_dir=None)
        self.addCleanup(p.close)
        self.addCleanup(p.root.update_idletasks)
        p.show_tools()
        p.root.update_idletasks()
        p.select(0)
        p.visibility()
        self.assertEqual(p.input_mode.get(),"CH2 only")
        self.assertEqual(p.source.get(),"CH2")
        p.select(1)
        p.visibility()
        self.assertEqual(p.input_mode.get(),"None")
        p.acquire()
        self.assertIsNone(p.device)
        self.assertEqual(validate(p.capture_setup())["visible"],[False]*4)
        p.visibility()
        p.select(3)
        p.visibility()
        self.assertEqual(p.input_mode.get(),"CH2 + CH4")
        p.acq_mode.set("Max")
        p.time.set("1 µs")
        p.acquisition_changed()
        p.device=Mock(actual_rate=500000000, trigger_index=None)
        frame=(bytes([130])*8192, bytes([160])*8192)
        p.device.capture_edge.return_value=(frame,False,0)
        p.acquire()
        self.assertEqual(p.device.apply.call_args.args[-3:],(500000000,8192,(1,3)))
        self.assertEqual(p.record.channels,(1,3))
        self.assertIn("CH4",p.details.get())
        self.assertNotIn("CH1",p.details.get())
        setup=p.capture_setup()
        self.assertEqual(validate(setup),setup)
        with tempfile.TemporaryDirectory() as tmp:
            csvpath=Path(tmp)/"w.csv"
            write_csv(csvpath,p.record,p.frame,1)
            with csvpath.open() as f:
                self.assertEqual(next(csv.reader(f)),["time_s","CH2_V","CH4_V","CH2_code","CH4_code"])
            path=Path(tmp)/"w.hk6254"
            p.record.save(path)
            self.assertEqual(Waveform.load(path)[0],p.record)
            p.load_record(path)
            self.assertEqual(p.input_mode.get(),"CH2 + CH4")
            p.select(1)
            p.visibility()
            self.assertNotIn("CH2",p.details.get())
            p.toggle()
            self.assertEqual(p.capture_setup(),setup)

    def test_old_setup(self):
        p=Scope(tk.Tk(),setup_dir=None)
        self.addCleanup(p.close)
        self.addCleanup(p.root.update_idletasks)
        old=dict(p.capture_setup(),version=1,input_mode="CH1–4",visible=[False,True,False,True])
        updated=validate(old)
        self.assertEqual(updated["input_mode"],"CH2 + CH4")
        self.assertEqual(updated["source"],"CH2")
        self.assertEqual(validate(updated),updated)
