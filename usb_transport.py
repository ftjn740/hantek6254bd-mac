"""USB transport and startup adapted from BSD-2-Clause libhantek6xx4."""

import struct
import sys
import time
from pathlib import Path

import usb.backend.libusb1
import usb.core
import usb.util


def usb_backend():
    if getattr(sys, "frozen", False):
        library = Path(sys._MEIPASS) / "libusb-1.0.0.dylib"
        backend = usb.backend.libusb1.get_backend(find_library=lambda _: str(library))
        if backend is None:
            raise OSError("The app could not load its bundled libusb")
        return backend
    return usb.backend.libusb1.get_backend()


class ScopeUSB:
    OUT = 0x02
    IN = 0x86
    TIMEOUT_MS = 1000

    def __init__(self):
        device = usb.core.find(idVendor=0x04b5, idProduct=0x6cde, backend=usb_backend())
        if device is None:
            raise ValueError("Hantek 6254BD not found")
        self.dev = device
        try:
            if device.get_active_configuration().bConfigurationValue != 1:
                device.set_configuration(1)
            usb.util.claim_interface(device, 0)
            # The tested 6254BD expects a vendor wake before its FPGA reset.
            try:
                device.ctrl_transfer(0x40, 0xea, 0, 0, bytes(10), self.TIMEOUT_MS)
            except usb.core.USBError as error:
                if error.errno != 32 and error.backend_error_code != -9:
                    raise
            for _ in range(3):
                self.bwrite(bytes([12, 0]))
            self.ping()
            self.bread(512)
            self.dev.ctrl_transfer(0xc0, 0xa2, 0x1580, 0, 71, self.TIMEOUT_MS)
            self.begin_command()
            self.dev.ctrl_transfer(0xc0, 0xa2, 0x15e0, 0, 8, self.TIMEOUT_MS)
            self.wake()
        except Exception:
            usb.util.dispose_resources(device)
            raise

    def close(self):
        usb.util.dispose_resources(self.dev)

    def ping(self):
        ready = self.dev.ctrl_transfer(0xc0, 0xb2, 0, 0, 10, self.TIMEOUT_MS)
        if not ready or ready[0] != 1:
            raise OSError("The 6254BD requires a USB high-speed link")

    def begin_command(self):
        start = bytes([15, 3, 3, 3, 0, 0, 0, 0, 0, 0])
        self.dev.ctrl_transfer(0x40, 0xb3, 0, 0, start, self.TIMEOUT_MS)
        self.ping()

    def bwrite(self, command):
        self.begin_command()
        if self.dev.write(self.OUT, command, self.TIMEOUT_MS) != len(command):
            raise OSError("Incomplete USB command")

    def bread(self, size):
        return self.dev.read(self.IN, size, self.TIMEOUT_MS)

    def wake(self):
        # Program the ADC interface and reference-clock PLL in board order.
        registers = (
            (4, 0x12, 0x4777, .002),
            (4, 0x33, 0x0003, .002),
            (2, 0x30, 0x0065, .002),
            (2, 0x0f, 0xf128, .015),
            (2, 0x01, 0x3812, 0),
        )
        for chip, address, value, delay in registers:
            self.bwrite(struct.pack("<BBBHBBB", 8, 0, 0, value, address, chip, 0))
            time.sleep(delay)

    def calibration_data(self):
        return self.dev.ctrl_transfer(0xc0, 0xa2, 0x1600, 0, 1154, self.TIMEOUT_MS)
