"""6254BD USB acquisition, ADC routing and frozen circular record readout."""
import struct
import time
from usb_transport import ScopeUSB

# BNC volts/div, analog range group and HMCAD1511 four-channel gain code
RANGES = (.002, .005, .01, .02, .05, .1, .2, .5, 1., 2., 5., 10.)
GAINS = (13, 10, 7, 5, 2, 0, 5, 2, 0, 5, 2, 0)


CLOCK = 125_000_000
DEPTHS = (4096, 8192, 16384, 32768, 65536)


def depths_for(inputs):
    return tuple(depth for depth in DEPTHS if depth * inputs <= 65536)


def sampling_clock(requested, inputs):
    if requested <= 0 or inputs not in (1, 2, 4):
        raise ValueError("Invalid sampling rate or input group")
    if requested <= CLOCK:
        approximate = max(1, round(CLOCK / requested))
        # The FPGA implements register value 1 as divide-by-2.5.
        choices = {1, 2, 3, approximate, max(1, approximate-1), approximate+1}
        divider = min(choices, key=lambda d: abs(CLOCK / (2.5 if d == 2 else d) - requested))
        return CLOCK / (2.5 if divider == 2 else divider), divider, 1
    multiplier = min((v for v in (1, 2, 4, 8) if v * inputs <= 8),
                     key=lambda v: abs(CLOCK*v-requested))
    return CLOCK*multiplier, 1, multiplier


class BenchDriver(ScopeUSB):
    def __init__(self):
        super().__init__()
        try:
            data = self.calibration_data()
            if len(data) != 1154:
                raise OSError("Incomplete factory calibration table")
            self.calibration = struct.unpack("<577H", data)
            if self.calibration[-1] != 0xfbcf:
                raise ValueError("Factory offset table signature mismatch")
            self.initialize_frontend()
        except Exception:
            self.close()
            raise
        self.settings = None
        self.pending = None
        self.trigger_config = None

    def initialize_frontend(self):
        # Establish the power-on FPGA defaults before applying user settings.
        self.bwrite(struct.pack("<HIH", 0x0b, CLOCK*4//5, 3))
        self.adc(0x55, 63)
        window = struct.pack("<BBIHIH", 0x10, 0, 214828, 0, 205600, 0)
        self.bwrite(window)
        self.route_default_adc()
        self.bwrite(struct.pack("<HI", 0x0f, 99))
        self.bwrite(window)
        self.bwrite(bytes([8, 0, 54, 54, 54, 54, 1, 0]))
        time.sleep(.004)
        self.bwrite(bytes([8, 0, 6, 6, 6, 6, 1, 1]))
        time.sleep(.05)
        self.route_default_adc()
        source = bytes([0x12, 0, 0x3d, 0, 0, 0])
        self.bwrite(source)
        self.bwrite(source)
        for channel, offset in zip((0, 1, 2, 4), (0x6201, 0x7064, 0x6980, 0x72d9)):
            self.bwrite(struct.pack("<HH", channel, offset))
        self.bwrite(struct.pack("<H4H16x", 0x1e, 1024, 1024, 1024, 1024))
        self.bwrite(bytes([7, 0] + [132, 132, 124, 124]*4 + [128]*8))
        self.bwrite(struct.pack("<HHH", 0x11, 0, 0))

    def route_default_adc(self):
        for register, value in ((0x3a, 0x0810), (0x3b, 0x0204), (0x0f, 0),
                                (0x31, 0x0204), (0x2a, 0x5550)):
            self.adc(register, value)

    def adc(self, register, value):
        self.bwrite(struct.pack('<BBB H BBB', 8, 0, 0, value, register, 4, 0))
        time.sleep(.003)

    def route(self, inputs):
        registers = {
            4: (63, 0x0810, 0x0204, 0x0204),
            2: (48, 0x1010, 0x0808, 0x0102),
            1: (25, 0x1010, 0x1010, 0x0001),
        }
        fullscale, low, high, clock = registers[inputs]
        self.adc(0x55, fullscale)
        self.adc(0x3a, low)
        self.adc(0x3b, high)
        self.adc(0x0f, 0)
        self.adc(0x31, clock)

    def apply(self, ranges, coupling, rate, depth=4096, inputs=4):
        if inputs not in (1, 2, 4) or depth not in depths_for(inputs):
            raise ValueError("Record does not fit physical memory")
        actual_rate, divider, multiplier = sampling_clock(rate, inputs)
        desired = (tuple(ranges), tuple(coupling), actual_rate, depth, inputs)
        if self.settings == desired:
            return
        adc_inputs = min(4, 8 // multiplier)
        route_changed = getattr(self, "adc_inputs", None) != adc_inputs
        if route_changed:
            self.route(adc_inputs)
        self.adc_inputs = adc_inputs
        self.trigger_config = None
        if route_changed or self.settings is None or self.settings[:2] != desired[:2]:
            bits = []
            gain = 0
            for i, (v, dc) in enumerate(zip(ranges, coupling)):
                index = RANGES.index(v)
                bits.append(2 | (4 if dc == 'DC' else 0) |
                            (32 if v <= 1 else 64) | (8 if v <= .1 else 16))
                gain |= GAINS[index] << (4*i)
            self.bwrite(bytes([8, 0, *bits, 1, 0]))
            time.sleep(.004)
            self.bwrite(bytes([8, 0, *(b & 0x86 for b in bits), 1, 1]))
            time.sleep(.05)
            if adc_inputs == 4:
                self.adc(0x2a, gain)
            else:
                self.adc(0x2b, (gain & 255) | ((gain & 15) << 8) if adc_inputs == 2 else (gain & 15) * 0x111)
            for i, v in enumerate(ranges):
                base = i*144 + (60 if v <= .1 else 96 if v <= 1 else 132) + {1:0,2:2,4:4}[adc_inputs]
                midpoint = round(sum(self.calibration[base:base+2])/2)
                self.bwrite(struct.pack('<BBH', (0,1,2,4)[i], 0, midpoint))
        self.inputs, self.depth = inputs, depth
        # The FPGA counts pre/post capture time in input-clock ticks. A fixed
        # window leaves old samples at the tail when decimation is increased.
        self.actual_rate = actual_rate
        self.multiplier = multiplier
        self.bwrite(struct.pack('<BBI', 0x0f, 0, divider-1))
        ticks_per_sample = CLOCK / actual_rate
        half_ticks = round(depth / 2 * ticks_per_sample)
        margin = round(1024 * ticks_per_sample)
        before = half_ticks + margin + 9228
        after = half_ticks + (margin if depth * inputs < 65536 else 0)
        self.bwrite(struct.pack('<BBIHIH', 0x10, 0, before, 0, after, 0))
        self.settings = desired
        self.pending = None

    def capture_edge(self, source, falling, threshold, auto):
        """Arm once, then poll without restarting the FPGA while waiting."""
        center = min(255, max(0, round(threshold)))
        trigger = (source, falling, center)
        request = (trigger, auto)
        if self.pending != request:
            if trigger != self.trigger_config:
                mask = (1 << self.inputs) - 1
                interleave = self.multiplier.bit_length() - 1
                discard = (8 // self.inputs).bit_length() - 1 - interleave
                self.bwrite([0x12, 0, (mask << 2) | discard, 0, interleave, source])
                self.bwrite([0x11, 0, 0, int(falling), 0, 0])
                hi, lo = min(255, center+3), max(0, center-3)
                self.bwrite([7, 0] + [hi, hi, lo, lo]*4 + [center]*8)
                self.trigger_config = trigger
            # Bit 0 enables auto timeout. Bit 1 enables rolling writes and
            # must remain clear for a frozen single-shot acquisition.
            self.bwrite([3, 0, int(auto), 0])
            self.pending = request
            return None

        self.bwrite([6, 0])
        status = self.bread(512)[0]
        if not status & 2:
            return None
        triggered = bool(status & 1)
        if not triggered and not auto:
            raise IOError(f"Normal acquisition completed without an edge (status {status:#x})")

        self.bwrite([13, 0])
        trigger_addr, end_addr, phase = struct.unpack_from('<HHB', self.bread(512))
        # A timeout has no edge address. A full ring must end at the write pointer.
        length = self.depth*self.inputs
        if length == 65536 or not triggered:
            start_addr = (end_addr-length) & 0xffff
        else:
            start_addr = (trigger_addr-length//2) & 0xffff
        self.trigger_index = None
        if triggered:
            # The address locates a group of samples. Byte 4 supplies its phase.
            # SAMPLE edge checks on this 6254BD put pipeline delay near 142 ns.
            lanes = 8 // self.inputs
            coarse = ((trigger_addr-start_addr) & 0xffff) // self.inputs
            self.trigger_index = coarse + lanes + (7-phase) % lanes - round(142e-9*self.actual_rate)
            if not 0 <= self.trigger_index < self.depth:
                raise IOError("Hardware trigger address outside frozen record")
        channels = self.read_frozen(start_addr)
        self.pending = None
        return channels, triggered, start_addr

    def read_frozen(self, address):
        self.bwrite(struct.pack('<BBH', 0x0e, 0, address))
        length = self.depth*self.inputs
        self.bwrite([5, 0, 0, length//512])
        self.ping()
        data = self.bread(length)
        if len(data) != length:
            raise IOError("Incomplete USB record")
        return [bytes(data[i::self.inputs]) for i in range(self.inputs)]
