"""Immutable physical acquisition and portable, versioned waveform files."""
import csv
import base64
import json
import math
import os
import tempfile
from array import array
from dataclasses import dataclass, asdict
from pathlib import Path
from statistics import median
from bisect import bisect_left


@dataclass(frozen=True)
class Waveform:
    raw: tuple
    rate: float
    scales: tuple
    probes: tuple
    couplings: tuple
    source: int
    slope: str
    level: float
    trigger_index: int | None
    time_div: float

    @property
    def depth(self):
        return len(self.raw[0])

    @property
    def origin(self):
        return self.depth//2 if self.trigger_index is None else self.trigger_index

    def save(self, filename, displayed=None, averages=1):
        data = asdict(self)
        data.update(version=1, raw=[base64.b64encode(ch).decode() for ch in self.raw],
                    channels=list(range(1, len(self.raw)+1)))
        if averages > 1:
            data.update(averages=averages, average=displayed)
        path=Path(filename)
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as out:
            tmp=Path(out.name)
            try:
                json.dump(data,out,separators=(",",":"),allow_nan=False)
                out.close()
                os.replace(tmp,path)
            finally:
                tmp.unlink(missing_ok=True)

    @classmethod
    def load(cls, filename):
        path=Path(filename)
        if path.stat().st_size>8_000_000:
            raise ValueError("Waveform file too large")
        try:
            data=json.loads(path.read_text())
            if data.pop("version")!=1:
                raise ValueError("Unsupported waveform version")
            avg=data.pop("average",None)
            count=data.pop("averages",1)
            ids=data.pop("channels")
            data["raw"]=tuple(base64.b64decode(ch,validate=True) for ch in data["raw"])
            for setting in ("scales","probes","couplings"):
                data[setting]=tuple(data[setting])
            wave=cls(**data)
            n=len(wave.raw)
            if n not in (1,2,4) or ids!=list(range(1,n+1)) or wave.depth not in (4096,8192,16384,32768,65536):
                raise ValueError("Invalid acquisition dimensions")
            if n*wave.depth>65536 or any(len(ch)!=wave.depth for ch in wave.raw):
                raise ValueError("Unequal channel lengths")
            if not (0<wave.rate<=1e9 and 0<wave.time_div<=1000 and math.isfinite(wave.level)):
                raise ValueError("Invalid timing or level")
            if wave.source not in range(n) or wave.slope not in ("Rising","Falling"):
                raise ValueError("Invalid trigger")
            if wave.trigger_index is not None and (type(wave.trigger_index)!=int or not 0<=wave.trigger_index<wave.depth):
                raise ValueError("Invalid trigger index")
            if any(len(v)!=4 for v in (wave.scales,wave.probes,wave.couplings)):
                raise ValueError("Invalid input settings")
            if not all(x in (.002,.005,.01,.02,.05,.1,.2,.5,1,2,5,10) for x in wave.scales) or not all(x in (1,10,100) for x in wave.probes) or not all(x in ("AC","DC") for x in wave.couplings):
                raise ValueError("Unsupported input settings")
            if not isinstance(count,int) or not 1<=count<=64 or (avg is None)!=(count==1):
                raise ValueError("Invalid average count")
            if avg is not None and (len(avg)!=n or any(len(a)!=wave.depth or not all(0<=v<=255 for v in a) for a in avg)):
                raise ValueError("Invalid averaged samples")
            return wave,avg or wave.raw,count
        except (KeyError,TypeError,IndexError,AttributeError,OverflowError) as exc:
            raise ValueError("Invalid waveform file") from exc


class BlockAverage:
    def __init__(self):
        self.key=None
        self.count=0

    def add(self,wave,target):
        key=(wave.rate,wave.scales,wave.probes,wave.couplings,wave.depth,len(wave.raw),
             wave.source,wave.slope,wave.level,wave.time_div,target)
        if target==1 or wave.trigger_index is None:
            self.key=None
            self.count=1
            return wave.raw
        if key!=self.key or self.count>=target:
            self.key=key
            self.origin=wave.origin
            self.count=0
            self.sums=[array('H',[0])*wave.depth for _ in wave.raw]
            self.weights=bytearray(wave.depth)
        shift=wave.origin-self.origin
        begin=max(0,-shift)
        end=min(wave.depth,wave.depth-shift)
        for i in range(begin,end):
            self.weights[i]+=1
        output=[]
        for sums,ch in zip(self.sums,wave.raw):
            samples=list(ch)
            for i in range(begin,end):
                j=i+shift
                sums[i]+=ch[j]
                samples[j]=sums[i]/self.weights[i]
            output.append(samples)
        self.count+=1
        return output


def channel_delay(first,second,rate):
    if len(first)<2 or len(second)<2:
        return None
    period=median(y-x for x,y in zip(first,first[1:]))
    delays=[]
    for t in first:
        index=bisect_left(second,t)
        near=second[max(0,index-1):index+1]
        if near:
            delta=min(near,key=lambda x:abs(x-t))-t
            if abs(delta)<period/2:
                delays.append(delta)
    return median(delays)/rate if delays else None


def write_csv(filename, wave, displayed, averages):
    inputs=range(len(wave.raw))
    volts="avg_V" if averages>1 else "V"
    with open(filename,"w",newline="") as out:
        writer=csv.writer(out)
        writer.writerow(["time_s",*(f"CH{i+1}_{volts}" for i in inputs),
                         *(f"CH{i+1}_code" for i in inputs)])
        for k in range(wave.depth):
            values=[(displayed[i][k]-128)*wave.scales[i]*wave.probes[i]/25 for i in inputs]
            writer.writerow([(k-wave.origin)/wave.rate,*values,*(ch[k] for ch in wave.raw)])
