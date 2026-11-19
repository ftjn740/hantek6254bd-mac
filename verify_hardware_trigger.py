"""Live acceptance check. Connect SAMPLE to both CH1 and CH2 and close the GUI first."""
import hashlib
import time
import tkinter as tk

from scope import Scope, edges


def wait_frame(panel, timeout=2):
    previous = panel.count
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        panel.acquire()
        if panel.count > previous:
            return
        time.sleep(.008)
    raise AssertionError("No new hardware capture")


def fingerprint(frame):
    return hashlib.sha256(b"".join(bytes(ch) for ch in frame)).digest()


def check():
    root = tk.Tk()
    root.withdraw()
    panel = Scope(root)
    try:
        panel.mode.set("Normal")
        for ch in (0, 1):
            panel.source.set(f"CH{ch+1}")
            for slope in ("Rising", "Falling"):
                panel.slope.set(slope)
                errors = []
                for _ in range(8):
                    wait_frame(panel)
                    crossings = edges(panel.frame[ch], 153, slope == "Rising")
                    # Only used for independent verification of the FPGA pointer.
                    delta = min(abs(t-panel.record.trigger_index) for t in crossings)
                    assert delta < 8, (slope, delta)
                    errors.append(delta)
                print(f"CH{ch+1} {slope}: 8/8, max pointer error {max(errors):.2f} samples")

        # Same physical input on CH1/2. Attenuate CH2 with its own hardware
        # range so only CH1 crosses ADC code 153. Select each source in turn.
        panel.scales[1] = .5
        panel.source.set("CH2")
        panel.level.set(.5)
        panel.single()
        old_count = panel.count
        deadline = time.monotonic()+1.2
        while time.monotonic()<deadline:
            panel.acquire()
            assert panel.count == old_count and panel.state == "Single"
            time.sleep(.015)
        assert panel.indicator["text"]=="ARMED"
        print("CH2 out-of-range Single: armed 1.2 s, zero completed records")

        panel.source.set("CH1")
        panel.level.set(.1)
        wait_frame(panel)
        assert panel.state=="Stop" and panel.count==old_count+1
        digest=fingerprint(panel.frame)
        for _ in range(3):
            time.sleep(.2)
            assert fingerprint(panel.device.read_frozen(panel.last_address))==digest
            panel.tick()
            assert panel.state=="Stop" and panel.count==old_count+1
        print("CH1 Single: one record, frozen device memory identical on 3 delayed reads")

        panel.source.set("CH2")
        panel.level.set(.5)
        panel.mode.set("Auto")
        panel.toggle()
        wait_frame(panel)
        assert panel.indicator["text"]=="AUTO"
        print("CH2 out-of-range Auto: completed without edge, correctly labeled AUTO")
        panel.scales[1]=.1
        panel.level.set(.1)
        panel.single()
        wait_frame(panel)
        assert panel.state=="Stop"
        crossings=edges(panel.frame[1],153,False)
        assert min(abs(t-panel.record.trigger_index) for t in crossings)<8
        print("CH2 Single falling: captured selected edge and stopped")
    finally:
        panel.close()


if __name__ == "__main__":
    check()
