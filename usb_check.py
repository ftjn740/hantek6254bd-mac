import usb.core
dev = usb.core.find(idVendor=0x04b5, idProduct=0x6cde)
if dev is None:
    raise SystemExit("6254BD not found")
print("GET_CONFIGURATION", list(dev.ctrl_transfer(0x80, 8, 0, 0, 1)))
