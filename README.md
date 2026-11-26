# Hantek 6254BD for Mac

A community oscilloscope application for the Hantek 6254BD. Capture over USB
with FPGA edge triggering, inspect and measure CH1–CH4 records, export CSV, and
save or reopen raw `.hk6254` waveforms. The hardware acceptance record covers
one 6254BD on Apple Silicon macOS. [中文使用与验收](6254BD-使用与验收.md)

![6254BD waveform display](6254BD-live.png)

## Download the Mac app · 下载 Mac 应用

On Apple Silicon with macOS 15 or later, download the `macos-arm64.zip`
from [Releases](https://github.com/ftjn740/hantek6254bd-mac/releases),
unzip it, and open **Hantek 6254BD.app** in Finder. Python, Tk, and libusb
are included. Use **Acquire… → Open SAMPLE** for offline replay.

预览包使用临时签名，下载后首次打开可能被 macOS 拦截。核对下载页的
`SHA256SUMS` 后，可在「系统设置 → 隐私与安全性」中选择「仍要打开」。
[Apple 打开来源受限应用的说明](https://support.apple.com/guide/mac-help/mh40616/mac)

## Install from source · 从源码安装

Use Homebrew on Apple Silicon macOS. The project uses Python 3.13, Tk 9
(touchpad scroll events), PyUSB 1.3.1 and libusb. Run these commands from your
clone or unpacked source release.

```sh
brew install python@3.13 python-tk@3.13 libusb
python3.13 -m venv .scope-env
.scope-env/bin/python -m pip install -r requirements.txt
```

## Run · 开始使用

- **Offline replay, 无设备回放**　Run
  `./Hantek6254BD.command SAMPLE-CH1-CH2.hk6254`. The included trace is a
  captured SAMPLE square wave.
- **Live capture, 实时采集**　Connect the 6254BD over a high-speed USB link.
  Run `./Hantek6254BD.command` or double-click it in Finder. The front panel
  starts in Run mode. Select a channel under **VERTICAL** and use **Input ON/OFF**
  to include its BNC in the capture. **Acquire… → Rate control → Max** tracks
  the highest per-channel rate for the enabled inputs. **Single** captures one hardware-triggered
  record. **Save CSV** and **Save record…** export the displayed frame.
- **Hardware checks, 硬件复验**　Close the live window, connect SAMPLE to CH1
  and CH2, and run both checks below.

```sh
.scope-env/bin/python verify_hardware_trigger.py
.scope-env/bin/python verify_acquisition.py
```

The tested maxima per channel are 250 MSa/s and 16,384 points in four-channel
mode, 500 MSa/s and 32,768 points in dual-channel mode, and 1 GSa/s and
65,536 points in single-channel mode. These checks cover waveform capture and
relative timing on the SAMPLE signal. Absolute gain, analog bandwidth and ADC
interleaving accuracy require external references.

## Build a release · 构建发布包

On Apple Silicon, install the Homebrew Python 3.13 and Python Tk 3.13 formulas,
then run `packaging/build-macos.sh v0.1.0-rc2` from a clean checkout of the
version to package. The output goes into `dist/v0.1.0-rc2/`. Homebrew manages
Python 3.13 and Tk 9 patch updates. The bundle records the actual build
versions in `Build-versions.txt`. The script compiles the pinned libusb source
as a shared library on macOS and includes that same source archive with the
release. To rebuild libusb separately, unpack the source and run
`./configure --disable-static --enable-shared && make` on arm64 macOS.

GitHub Actions runs the same build for pull requests and `main`. Pushing a
version tag creates a Release after the archive passes signature, dependency
and offline replay checks. Tags with `-rc` create prereleases.

## Contributing · 贡献

Include the exact model, macOS/Python/Tk versions, USB connection, channel
group, rate, depth and trigger settings in hardware reports. For a new model,
include a known input signal and actual measurements. A small saved record
can help reproduce problems when you can share the signal data.

Copyright (c) 2026 Michael Rong. Project-owned code is MIT-licensed.
[License](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md)
