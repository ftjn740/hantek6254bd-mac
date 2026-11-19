# Third-party notices

The project-owned source is licensed under MIT. The following notice covers
the portions adapted from libhantek6xx4 in `usb_transport.py` and `bench_driver.py`,
including USB startup, device commands, and analog range configuration.

Source: [libhantek6xx4 at 8a036d7](https://github.com/pvachon/libhantek6xx4/tree/8a036d71e1ac4f40683b17b6cb5a2178c510f476)

Copyright 2019 Phil Vachon <phil@security-embedded.com>

Redistribution and use in source and binary forms, with or without modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice, this list of conditions and the following disclaimer in the documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

## Runtime dependencies

Install [PyUSB](https://github.com/pyusb/pyusb) and
[libusb](https://github.com/libusb/libusb) separately using the README.
Review the dependencies' own licenses if packaging their binaries.

The acquisition code uses hardware protocol facts and register values. Earlier
development also used [pyhantek](https://github.com/danielkucera/pyhantek)
and consulted [hantek6004](https://github.com/hackhantek/hantek6004).
The source release uses the transport in this repository. It contains no copy
of either upstream tree or the Android SDK.
