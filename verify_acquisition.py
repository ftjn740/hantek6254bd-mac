"""Acceptance checks for a 6254BD with its SAMPLE signal connected to CH1 and CH2."""
import argparse
import csv
import statistics
import struct
import time
import tkinter as tk
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from bench_driver import BenchDriver, CLOCK, depths_for

from scope import Scope, edges, fmt, measure
from verify_hardware_trigger import wait_frame
from waveform import Waveform


def capture(device, source=0, threshold=153, falling=False):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        shot = device.capture_edge(source, falling, threshold, False)
        if shot is not None:
            assert shot[1]
            return shot[0], shot[2]
        time.sleep(.006)
    raise AssertionError(f"CH{source+1} did not trigger at code {threshold}")


def check_depths(device):
    for inputs in (4, 2, 1):
        for depth in depths_for(inputs):
            wraps = 0
            for scale in (.2, .05, .1):
                # Independent DC CH1 vs AC CH2 also checks lane identities.
                device.apply([scale, .1, .5, .5], ["DC", "AC", "DC", "DC"],
                             500_000, depth, inputs)
                samples, address = capture(device, threshold=128+2.5/scale)
                assert len(samples) == inputs and all(len(ch) == depth for ch in samples)
                assert samples == device.read_frozen(address)
                wraps += address + inputs*depth > 65536
                for part in range(4):
                    first, last = part*depth//4, (part+1)*depth//4
                    for i, channel in enumerate(samples[:2]):
                        lo, hi, frequency, duty = measure(channel[first:last], 500_000)
                        volts = scale if i == 0 else .1
                        assert .17 < (hi-lo)*volts/25 < .24, (inputs, depth, part, i, lo, hi)
                        assert 980 < frequency < 1020 and 45 < duty < 55
                        assert (lo > 120 if i == 0 else lo < 116), (inputs, depth, i, lo)
                trigger = device.trigger_index
                observed = edges(samples[0], 128+2.5/scale)
                assert min(abs(t-trigger) for t in observed) < 10
                assert abs(trigger-depth/2) < 25
            if depth*inputs == 65536:
                assert wraps
            print(f"Depth {inputs} × {depth:5} | 3 gain steps, all quarters and frozen readback OK | wrapped {wraps}/3")


def check_ranges(device):
    for scale in (.05, .1, .2):
        for coupling in (["DC", "AC"], ["AC", "DC"], ["DC", "DC"]):
            device.apply([scale,scale,.5,.5], [*coupling,"DC","DC"], 500_000)
            table = [[], []]
            for _ in range(5):
                frame, _ = capture(device, threshold=128 if coupling[0]=="AC" else 128+2.5/scale)
                for i, data in enumerate(frame[:2]):
                    lo, hi, frequency, _ = measure(data, 500_000)
                    mid = ((lo+hi)/2-128)*scale/25
                    assert abs(mid-(.10 if coupling[i]=="DC" else 0)) < .035
                    assert 990 < frequency < 1010
                    table[i].append((hi-lo)*scale/25*1000)
            print(f"Range {int(scale*1000)} mV/div {'/'.join(coupling)} | CH1/2 platform mV "
                  + " / ".join(f"{min(values):.1f}–{max(values):.1f}" for values in table))


def check_high_speed(device):
    for inputs in (4, 2, 1):
        depth=65536//inputs
        for rate in (50_000_000,125_000_000,250_000_000,500_000_000,1_000_000_000):
            if rate*inputs > 1e9:
                continue
            device.apply([.05,.2,.5,.5], ["AC","DC","DC","DC"], rate, depth, inputs)
            errors=[]
            for source in range(min(inputs,2)):
                for falling in (False,True):
                    level=(128,144)[source]
                    frame,address=capture(device,source,level,falling)
                    assert frame == device.read_frozen(address)
                    assert min(frame[0])<100 and max(frame[0])>158
                    if inputs>=2:
                        assert 121 < min(frame[1]) < 138 and 148 < max(frame[1]) < 175
                    crossing=edges(frame[source],level,not falling)
                    errors.append(min(abs(t-device.trigger_index) for t in crossing))
            assert max(errors)<18, (inputs,rate,errors)
            # Capture the NEXT SAMPLE rising edge twice, shifting the end of the
            # circular window by 1024 FPGA ticks. The measured pixel-independent
            # sample displacement checks the packing and relative sampling clock.
            positions=[]
            for offset in (0,1024):
                post=125_000+round(depth*CLOCK/rate/2)+offset
                device.bwrite(struct.pack("<BBIHIH",0x10,0,2*125_000,0,post,0))
                readings=[]
                for _ in range(5):
                    frame,_=capture(device, threshold=128)
                    crossing=edges(frame[0],128)
                    readings.append(min(crossing,key=lambda t:abs(t-depth/2)))
                positions.append(statistics.median(readings))
            estimate=(positions[0]-positions[1])*CLOCK/1024
            assert abs(estimate/rate-1)<.03, (inputs,rate,estimate,positions)
            print(f"Clock {inputs} × {rate/1e6:g} MSa/s | shift {positions[0]-positions[1]:.1f} samples"
                  f" | relative rate {estimate/1e6:.3f} MSa/s | trigger error ≤ {max(errors):.1f} samples")


def check_front_panel(sample_file):
    root=tk.Tk()
    scope=Scope(root, setup_dir=None)
    root.update_idletasks()
    try:
        scope.show_tools()
        scope.acq_mode.set("Manual")
        for rate in (500_000,1_250_000):
            for depth in (4096,8192):
                scope.manual_rate.set(fmt(rate,"Sa/s"))
                scope.depth.set(depth)
                scope.acquisition_changed()
                wait_frame(scope)
                frequency=scope.stats[0][2]
                assert abs(frequency-1000)<5 and scope.width==round(.002*rate)
                assert scope.record.rate==rate and scope.record.depth==depth
                print(f"200 µs/div | {rate:g} Sa/s | {depth} pts/ch | {rate/frequency:.1f} samples/cycle"
                      f" | span {depth/rate*1000:.4f} ms")
        scope.depth.set(4096)
        scope.manual_rate.set(fmt(5e6,"Sa/s"))
        wait_frame(scope)
        assert scope.depth.get()==scope.record.depth==16384
        scope.depth.set(4096)
        scope.scales[:2]=[10,10]
        scope.time.set("5 ns")
        scope.autoscale()
        wait_frame(scope,timeout=5)
        assert scope.record.trigger_index is not None
        assert abs(scope.div-.0002)<1e-15 and abs(scope.pre/scope.span-.5)<1e-9
        cycles=scope.width/scope.record.rate*scope.stats[0][2]
        heights=[[(code-128)/25+scope.positions[i] for code in scope.stats[i][:2]] for i in range(2)]
        assert 1.95<=cycles<=5.05 and heights[1][1]<heights[0][0]
        assert all(-4.1<y<4.1 for pair in heights for y in pair)
        print(f"Auto Scale | {cycles:.2f} cycles | CH1/2 grid heights {heights}")

        # Send actual Tk pointer events to a point on CH2 in the rendered plot.
        canvas=scope.canvas
        points=canvas.coords(canvas.find_withtag("ch1")[0])
        x,y=map(round,points[16:18])
        canvas.event_generate("<Button-1>",x=x,y=y)
        canvas.event_generate("<B1-Motion>",x=x+2,y=y-25)
        canvas.event_generate("<ButtonRelease-1>",x=x+2,y=y-25)
        assert scope.ch==1 and scope.position.get()>heights[1][0]-.3
        old_scale=scope.scales[1]
        canvas.event_generate("<Option-MouseWheel>",x=x,y=y,delta=-120)
        wait_frame(scope)
        assert scope.record.scales[1]>old_scale
        scope.single()
        wait_frame(scope)
        wave=scope.record
        assert scope.state=="Stop" and wave.trigger_index is not None
        snapshot=wave.raw
        assert list(snapshot)==scope.device.read_frozen(scope.last_address)
        scope.time.set("2 µs")
        scope.horizontal()
        narrow=scope.width
        scope.region.set("Visible window")
        scope.refresh_view()
        assert narrow<wave.depth and scope.measures[0][3]["text"]=="—"
        scope.delay.set(scope.delay_limits[1])
        scope.shift_record()
        assert scope.start+scope.width==wave.depth and abs(scope.width-narrow)<=1
        scope.center_trigger()
        scope.cursor.set("Time")
        scope.draw()
        assert canvas.find_withtag("all") and scope.record.raw==snapshot
        scope.fit_record()
        assert scope.start==0 and scope.width==wave.depth
        scope.region.set("Record")
        scope.refresh_view()
        live_stats=scope.stats.copy()
        with patch("scope.filedialog.asksaveasfilename",return_value=str(sample_file)):
            scope.save_record()
        assert Waveform.load(sample_file)[0]==wave
        with TemporaryDirectory() as tmp:
            csvname=Path(tmp)/"record.csv"
            with patch("scope.filedialog.asksaveasfilename",return_value=str(csvname)):
                scope.save()
            with csvname.open() as source:
                rows=list(csv.reader(source))
            assert len(rows)==wave.depth+1 and float(rows[wave.origin+1][0])==0
            assert bytes(int(row[1+len(snapshot)]) for row in rows[1:])==snapshot[0]
        print("Single, CH2 curve drag + Option-wheel, zoom, end-of-record pan, cursors, CSV OK")
        scope.cursor.set("Off")
        scope.time.set("200 µs")
        scope.toggle()
        scope.persistence.set("4 records")
        for target in (4,16,64):
            scope.average.set(target)
            scope.clear_analysis()
            for number in range(1,target+1):
                wait_frame(scope)
                assert scope.average_count==number, (target,number,scope.average_count)
            assert abs(scope.stats[0][2]-1000)<10
        scope.envelope.set("On")
        scope.draw()
        assert len(scope.history)==4
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/"average.hk6254"
            scope.record.save(path,scope.frame,scope.average_count)
            saved,displayed,count=Waveform.load(path)
            assert saved==scope.record and displayed==scope.frame and count==64
        scope.slope.set("Falling")
        scope.rearm()
        wait_frame(scope)
        assert scope.average_count==1 and not scope.history
        print("4/16/64 triggered averages, averaged record round trip, envelope, persistence, slope reset OK")
        for group in ("CH1 + CH2","CH1 only","CH1–4"):
            scope.input_mode.set(group)
            scope.acquisition_changed()
            wait_frame(scope)
            assert scope.record.depth==4096 and len(scope.record.raw)=={"CH1 only":1,"CH1 + CH2":2,"CH1–4":4}[group]
        for size in ("990x750","1220x770"):
            root.geometry(size)
            root.update_idletasks()
            cells=scope.measures
            dims=lambda row:[(c.winfo_x(),c.winfo_width()) for c in row]
            assert dims(cells[0])==dims(cells[1])
    finally:
        scope.close()

    # Fresh App instance, with USB opening made impossible for this replay test.
    replay_root=tk.Tk()
    with patch("scope.BenchDriver",side_effect=AssertionError("Offline replay tried USB")):
        replay=Scope(replay_root, setup_dir=None)
        try:
            replay.load_record(sample_file)
            replay.tick()
            assert replay.device is None and replay.state=="Stop"
            assert replay.record.raw==snapshot and replay.stats==live_stats
            replay.time.set("20 µs")
            replay.horizontal()
            assert replay.width<replay.record.depth
            print("Offline replay | identical raw bytes, capture settings and all channel measurements")
        finally:
            replay.close()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-file",type=Path)
    parser.add_argument("--panel-only",action="store_true")
    args=parser.parse_args()
    if not args.panel_only:
        device=BenchDriver()
        try:
            check_depths(device)
            check_ranges(device)
            check_high_speed(device)
        finally:
            device.close()
    with TemporaryDirectory() as temp:
        check_front_panel(args.sample_file or Path(temp)/"SAMPLE.hk6254")


if __name__=="__main__":
    main()
