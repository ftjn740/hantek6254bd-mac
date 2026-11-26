"""Storage and validation for 6254BD instrument setups."""
import json
import math
import os
from itertools import combinations
from pathlib import Path
from tempfile import NamedTemporaryFile

from bench_driver import RANGES, depths_for, lane_count

def input_label(channels):
    if not channels:
        return "None"
    if len(channels) == 4:
        return "CH1–4"
    label = " + ".join(f"CH{i+1}" for i in channels)
    return label + " only" if len(channels) == 1 else label


INPUT_MODES = {input_label(group): group for size in range(5)
               for group in combinations(range(4),size)}
MANUAL_RATES = (125_000, 250_000, 500_000, 1_250_000, 2_500_000, 5_000_000,
                12_500_000, 25_000_000, 50_000_000, 125_000_000, 250_000_000,
                500_000_000, 1_000_000_000)
ARRAYS = ("scales", "coupling", "probes", "positions", "visible", "cursor_x", "cursor_y")
CONTROLS = ("input_mode", "acq_mode", "depth", "delay", "source", "slope", "mode",
            "level", "average", "envelope", "persistence", "region", "cursor")


def validate(data):
    def require(condition, field):
        if not condition:
            raise ValueError(f"Invalid setup field: {field}")

    def number(value, low, high):
        return type(value) in (int, float) and low <= value <= high and math.isfinite(value)

    require(isinstance(data, dict) and type(data.get("version")) is int and data["version"] in (1,2), "version")
    for key in (*ARRAYS, *CONTROLS, "ch", "time_div", "manual_rate"):
        require(key in data, key)
    for key in ARRAYS:
        size = 2 if key.startswith("cursor_") else 4
        require(isinstance(data[key], list) and len(data[key]) == size, key)
    for key, choices in (("input_mode", tuple(INPUT_MODES)), ("acq_mode", ("Manual","Auto","Max")),
                         ("slope", ("Rising","Falling")), ("mode", ("Auto","Normal")),
                         ("envelope", ("Off","On")), ("persistence", ("Off","4 records")),
                         ("region", ("Record","Visible window")), ("cursor", ("Off","Time","Voltage"))):
        require(data[key] in choices, key)
    group = INPUT_MODES[data["input_mode"]]
    n = lane_count(group) or 1
    require(type(data["ch"]) is int and 0 <= data["ch"] < 4, "ch")
    require(data["source"] in [f"CH{i+1}" for i in (group or range(4))], "source")
    for key, choices in (("average", (1,4,16,64)), ("depth", depths_for(n)), ("manual_rate", MANUAL_RATES)):
        require(type(data[key]) is int and data[key] in choices, key)
    require(data["manual_rate"] <= 1e9/n, "manual_rate")
    require(all(type(v) in (float,int) and v in RANGES for v in data["scales"]), "scales")
    require(all(type(v) is int and v in (1,10,100) for v in data["probes"]), "probes")
    require(all(v in ("AC","DC") for v in data["coupling"]), "coupling")
    require(all(type(v) is bool for v in data["visible"]), "visible")
    require(all(number(v,-4,4) for v in data["positions"]), "positions")
    require(all(number(v,0,1) for key in ("cursor_x","cursor_y") for v in data[key]), "cursors")
    require(number(data["time_div"], 1e-12, 1e4), "time_div")
    require(number(data["delay"], -655360, 655360), "delay")
    i = int(data["source"][-1])-1
    limit = 5*data["scales"][i]*data["probes"][i]
    require(number(data["level"], -limit-1e-9, limit+1e-9), "level")
    if data["version"] == 1:
        enabled = tuple(i for i in group if data["visible"][i]) or group
        data = dict(data, version=2, input_mode=input_label(enabled),
                    visible=[i in enabled for i in range(4)])
        if i not in enabled:
            data["source"] = f"CH{enabled[0]+1}"
            data["level"] = 0.
    else:
        require(data["visible"] == [i in group for i in range(4)], "visible")
    return data


def read_setup(path):
    return validate(json.loads(path.read_text(encoding="utf-8")))


def write_setup(path, data, *, overwrite=True):
    payload = json.dumps(validate(data), ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete_on_close=False) as temp:
        temp.write(payload.encode("utf-8"))
        temp.flush()
        os.fsync(temp.fileno())
        if overwrite:
            os.replace(temp.name, path)
        else:
            os.link(temp.name, path)


def preset_path(directory, name):
    name = name.strip()
    if not name or "/" in name or any(ord(c)<32 for c in name) or len(name.encode("utf-8"))>200:
        raise ValueError("Choose a name of up to 200 bytes without slashes or control characters.")
    return Path(directory) / "Presets" / (name + ".json")
