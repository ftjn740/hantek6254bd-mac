import sys
import time
import math
import statistics
import tkinter as tk
from tkinter import filedialog, ttk, messagebox
from bisect import bisect_right
from collections import deque
from pathlib import Path
from waveform import Waveform, BlockAverage, channel_delay, write_csv
from bench_driver import BenchDriver, RANGES, depths_for, sampling_clock
from usb_transport import usb_backend
import usb.core

BG, PANEL, GRID, FG, MUTED = "#11181e", "#202a32", "#324149", "#e5edf1", "#9daeb7"
COLORS = ("#ffdb51", "#64dbed", "#f099cf", "#98dc89")
TIME = tuple(x*10**e for e in (-9,-8,-7,-6,-5,-4,-3) for x in (1,2,5))
INPUT_MODES = {"CH1–4":4, "CH1 + CH2":2, "CH1 only":1}
MANUAL_RATES = (125_000, 250_000, 500_000, 1_250_000, 2_500_000, 5_000_000,
                12_500_000, 25_000_000, 50_000_000, 125_000_000, 250_000_000,
                500_000_000, 1_000_000_000)
PROBES = ("1×", "10×", "100×")
SAMPLE_RECORD = Path(__file__).with_name("SAMPLE-CH1-CH2.hk6254")


def fmt(value, unit):
    if value is None:
        return "—"
    for factor, prefix in MEASURE_UNITS:
        if abs(value) >= factor*.99999 or factor == 1e-9:
            return f"{value/factor:.4g} {prefix}{unit}" if value else f"0 {unit}"


MEASURE_UNITS = ((1e9,"G"),(1e6,"M"),(1e3,"k"),(1,""),(1e-3,"m"),(1e-6,"µ"),(1e-9,"n"))


def measurement_text(value, unit, previous):
    if value is None:
        return "—", previous
    magnitude = abs(value)
    if previous and .90 <= magnitude / previous[0] < 1100:
        scale = previous
    elif magnitude:
        scale = next((s for s in MEASURE_UNITS if magnitude >= .99*s[0]), MEASURE_UNITS[-1])
    else:
        scale = previous or (1,"")
    scaled = value / scale[0]
    decimals = max(0, min(4, 3-math.floor(math.log10(abs(scaled)*1.01)))) if scaled else 3
    return f"{scaled:.{decimals}f} {scale[1]+unit:<3}", scale


def edges(data, level, rising=True):
    # Arm on the far side of a hysteresis band, timestamp the threshold crossing.
    direction = 1 if rising else -1
    armed = False
    out = []
    for i, code in enumerate(data):
        delta = (code-level)*direction
        if delta <= -2:
            armed = True
        elif armed and delta >= 0:
            prev = data[i-1]
            out.append(i-1+(level-prev)/(code-prev))
            armed = False
    return out


def measure(data, rate):
    ordered = sorted(data)
    lo, hi = ordered[len(data)//10], ordered[9*len(data)//10]
    freq = duty = None
    if hi-lo >= 6:
        level = (hi+lo)/2
        up, down = edges(data,level), edges(data,level,False)
        periods, duties = [], []
        for a,b in zip(up,up[1:]):
            idx = bisect_right(down, a)
            if idx < len(down) and down[idx] < b:
                fall = down[idx]
                periods.append(b-a)
                duties.append(100*(fall-a)/(b-a))
        if periods:
            freq, duty = rate/statistics.median(periods), statistics.median(duties)
    return lo, hi, freq, duty


class Scope:
    def __init__(self, root):
        self.root = root
        root.title("Hantek 6254BD  ·  Digital Oscilloscope")
        root.geometry("1220x770")
        root.minsize(990,750)
        root.configure(bg=BG)
        self.device = self.frame = self.record = None
        self.scales = [.1,.1,.5,.5]
        self.coupling = ["DC"]*4
        self.probes = [1]*4
        self.positions = [.8,-2.6,0,0]
        self.visible = [True,True,False,False]
        self.ch = 0
        self.state = "Run"
        self.count = 0
        self.wait_since = None
        self.autopending = False
        self.acq_mode=tk.StringVar(value="Auto")
        self.manual_rate=tk.StringVar(value="1.25 MSa/s")
        self.rates={fmt(v,"Sa/s"):v for v in MANUAL_RATES}
        self.depth=tk.IntVar(value=4096)
        self.input_mode=tk.StringVar(value="CH1–4")
        self.average=tk.IntVar(value=1)
        self.averager=BlockAverage()
        self.average_count=1
        self.persistence=tk.StringVar(value="Off")
        self.envelope=tk.StringVar(value="Off")
        self.region=tk.StringVar(value="Record")
        self.history=deque(maxlen=4)
        self.tools_window=None
        self.details=tk.StringVar()
        self.gate_text=tk.StringVar(value="Measure · Record")
        self.window_info=tk.StringVar()
        self.delay_limits=(-3,3)
        self.replay=False
        self.time = tk.StringVar(value="200 µs")
        self.source = tk.StringVar(value="CH1")
        self.slope = tk.StringVar(value="Rising")
        self.mode = tk.StringVar(value="Auto")
        self.level = tk.DoubleVar(value=.10)
        self.delay = tk.DoubleVar(value=0.)
        self.cursor = tk.StringVar(value="Off")
        self.cursor_x = [.3,.7]
        self.cursor_y = [.4,.6]
        self.drag = 0
        self.marker_drag = None
        self.trace_anchor = None
        self.scale = tk.StringVar()
        self.couple = tk.StringVar()
        self.probe = tk.StringVar()
        self.position = tk.DoubleVar()
        top = tk.Frame(root,bg=PANEL,padx=14,pady=8)
        top.pack(fill="x")
        self.label(top,"HANTEK",18,FG,True).pack(side="left")
        self.label(top,"  6254BD",16).pack(side="left")
        for title,cmd in (("Acquire…",self.show_tools),("Save CSV",self.save),("Auto Scale",self.autoscale),("Single",self.single),("Run / Stop",self.toggle)):
            key=self.button(top,title,cmd)
            key.pack(side="right",padx=3)
            if cmd==self.toggle:
                self.run_key=key
                key.configure(text="■  Stop",bg="#345541")
            elif cmd==self.single:
                key.configure(text="Single",bg="#41546a")
        self.indicator = self.label(top,"CONNECT",12,"#8ce9a8",True)
        self.indicator.pack(side="right",padx=12)
        body=tk.Frame(root,bg=BG)
        body.pack(fill="both",expand=True,padx=10,pady=8)
        side=tk.Frame(body,bg=PANEL,padx=14,pady=5,width=272)
        side.pack(side="right",fill="y",padx=(10,0))
        side.pack_propagate(False)
        left=tk.Frame(body,bg=BG)
        left.pack(fill="both",expand=True)
        plotbar=tk.Frame(left,bg=BG)
        plotbar.pack(fill="x",pady=(0,5))
        self.readout=self.label(plotbar,"Connecting to USB…",13)
        self.readout.pack(side="left")
        self.label(plotbar,"Scroll  time/div    ⌥ Scroll  V/div",11,MUTED).pack(side="right")
        self.canvas=tk.Canvas(left,bg="#0c1216",highlightthickness=0)
        self.canvas.pack(fill="both",expand=True)
        self.canvas.bind("<ButtonRelease-1>",self.end_drag)
        self.scroll_target=None
        self.scroll_pixels=0
        self.scroll_time=0.
        self.canvas.bind("<Leave>",lambda e:setattr(self,"scroll_target",None))
        for modifier,vertical in (("",False),("Option-",True)):
            self.canvas.bind(f"<{modifier}MouseWheel>",
                             lambda e,v=vertical:self.plot_scroll(e,v,False))
            self.canvas.bind(f"<{modifier}TouchpadScroll>",
                             lambda e,v=vertical:self.plot_scroll(e,v,True))
        self.canvas.bind("<Configure>",lambda e:self.draw())
        self.canvas.bind("<Button-1>",self.cursor_press)
        self.canvas.bind("<B1-Motion>",self.cursor_move)
        self.tabs=tk.Frame(left,bg=BG)
        self.tabs.pack(fill="x",pady=5)
        self.channel_buttons=[]
        for i in range(4):
            b=self.button(self.tabs,"",lambda n=i:self.select(n))
            b.pack(side="left",expand=True,fill="x",padx=2)
            self.channel_buttons.append(b)
        table=tk.Frame(left,bg=BG)
        table.pack(fill="x")
        headings=("Input", "Range / coupling / probe", "Vpp", "Frequency", "Period", "Duty", "")
        self.measures=[]
        self.measure_units=[[None]*3 for _ in range(4)]
        for col,(title,size) in enumerate(zip(headings,(46,138,90,106,100,72,50))):
            table.columnconfigure(col,minsize=size,weight=int(1 <= col <= 5))
            header=self.label(table,title,10,MUTED)
            header.grid(row=0,column=col,sticky="e" if 2<=col<=5 else "w",padx=4)
        for i in range(4):
            cells=[]
            for col in range(len(headings)):
                cell=tk.Label(table,bg=BG,fg=COLORS[i],width=1,
                              font=("Menlo",11),anchor="e" if 2<=col<=5 else "w")
                cell.grid(row=i+1,column=col,sticky="ew",padx=4)
                if not self.visible[i]:
                    cell.grid_remove()
                cells.append(cell)
            self.measures.append(cells)
        self.section(side,"VERTICAL")
        self.active=self.label(side,"",14,COLORS[0],True)
        self.active.pack(anchor="w")
        self.on=self.button(side,"",self.visibility)
        self.on.pack(anchor="w",pady=2)
        self.scbox=self.combo(side,"Scale / div",self.scale,[],self.changed)
        self.segment(side,"Coupling",self.couple,["DC","AC"],self.changed)
        self.combo(side,"Probe",self.probe,PROBES,self.change_probe)
        self.slider(side,"Position · div",self.position,-4,4,.1,self.move)
        self.section(side,"HORIZONTAL")
        self.timebox=self.combo(side,"Time / div",self.time,[fmt(t,"s") for t in TIME],self.horizontal)
        self.delay_slider=self.slider(side,"Delay · div",self.delay,-3,3,.1,self.shift_record)
        self.section(side,"EDGE TRIGGER")
        self.source_box=self.combo(side,"Source",self.source,[f"CH{i+1}" for i in range(4)],self.new_source)
        self.segment(side,"Slope",self.slope,["Rising","Falling"],self.rearm)
        self.segment(side,"Sweep",self.mode,["Auto","Normal"],self.rearm)
        self.trig=self.slider(side,"Level · V",self.level,-.5,.5,.002,self.rearm)
        self.button(side,"Set level to 50%",self.midlevel).pack(anchor="w")
        self.section(side,"CURSORS")
        self.combo(side,"Drag on grid",self.cursor,["Off","Time","Voltage"],self.draw)
        status=tk.Frame(root,bg=BG)
        status.pack(fill="x",padx=14,pady=(0,6))
        self.counter=tk.Label(status,bg=BG,fg=MUTED,font=("Menlo",10),anchor="e")
        self.counter.pack(side="right")
        tk.Label(status,textvariable=self.gate_text,bg=BG,fg=MUTED,font=("Helvetica",11)).pack(side="right",padx=12)
        self.foot=self.label(status,"",11,MUTED)
        self.foot.pack(side="left")
        root.bind("<Key-s>",lambda e:self.single())
        for i in range(4):
            root.bind(str(i+1),lambda e,j=i:self.select(j))
        root.bind("<space>",lambda e:self.toggle())
        root.protocol("WM_DELETE_WINDOW",self.close)
        self.select(0)
        self.trigger_range()
        root.after(120,self.tick)

    def show_tools(self):
        if self.tools_window is not None and self.tools_window.winfo_exists():
            self.tools_window.lift()
            return
        win=self.tools_window=tk.Toplevel(self.root)
        win.title("Acquire & Measure · 6254BD")
        win.configure(bg=PANEL,padx=16,pady=12)
        self.section(win,"ACQUIRE")
        self.combo(win,"Physical inputs",self.input_mode,list(INPUT_MODES),self.acquisition_changed)
        self.combo(win,"Rate control",self.acq_mode,["Auto","Manual"],self.acquisition_changed)
        self.rate_box=self.combo(win,"Manual rate",self.manual_rate,list(self.rates),self.acquisition_changed)
        self.depth_box=self.combo(win,"Samples / input",self.depth,depths_for(INPUT_MODES[self.input_mode.get()]),self.acquisition_changed)
        self.sync_acquisition_controls()
        tk.Label(win,textvariable=self.window_info,bg=PANEL,fg=FG,justify="left",anchor="w",width=53).pack(anchor="w",pady=5)
        view=tk.Frame(win,bg=PANEL)
        view.pack(fill="x")
        self.button(view,"Fit record",self.fit_record).pack(side="left")
        self.button(view,"Center trigger",self.center_trigger).pack(side="right")
        self.section(win,"ANALYSIS")
        self.combo(win,"Triggered average",self.average,[1,4,16,64],self.clear_analysis)
        self.combo(win,"Display envelope",self.envelope,["Off","On"],self.draw)
        self.combo(win,"Persistence",self.persistence,["Off","4 records"],self.clear_history)
        self.combo(win,"Measure over",self.region,["Record","Visible window"],self.refresh_view)
        tk.Label(win,textvariable=self.details,justify="left",bg=PANEL,fg=FG,
                 font=("Menlo",11),width=56,anchor="w").pack(anchor="w",pady=6)
        row=tk.Frame(win,bg=PANEL)
        row.pack(fill="x",pady=4)
        self.button(row,"Open record…",self.open_record).pack(side="left")
        self.button(row,"Open SAMPLE",self.open_sample).pack(side="left",padx=4)
        self.button(row,"Save record…",self.save_record).pack(side="right")

    def sync_acquisition_controls(self):
        n=INPUT_MODES[self.input_mode.get()]
        depths=depths_for(n)
        self.depth.set(min(self.depth.get(),max(depths)))
        rates=[name for name,rate in self.rates.items() if rate<=1e9/n]
        if self.manual_rate.get() not in rates:
            self.manual_rate.set(rates[-1])
        if self.tools_window and self.tools_window.winfo_exists():
            self.depth_box.configure(values=depths)
            self.rate_box.configure(values=rates,state="readonly" if self.acq_mode.get()=="Manual" else "disabled")
        self.source_box.configure(values=[f"CH{i+1}" for i in range(n)])
        if self.source_index()>=n:
            self.source.set("CH1")
            self.trigger_range()
        self.select(min(self.ch,n-1))

    def acquisition_changed(self):
        self.sync_acquisition_controls()
        self.new_arm()
        self.clear_analysis()
        self.rearm()

    def clear_history(self):
        self.history.clear()
        self.previous_key=None
        self.draw()

    def clear_analysis(self):
        self.averager.key=None
        self.clear_history()

    def save_record(self):
        if self.record:
            wave,frame,averages=self.record,self.frame,self.average_count
            name=filedialog.asksaveasfilename(defaultextension=".hk6254",initialfile="capture.hk6254")
            if name:
                try:
                    wave.save(name,frame,averages)
                except OSError as exc:
                    messagebox.showerror("Save waveform",str(exc))

    def open_record(self):
        name=filedialog.askopenfilename(filetypes=[("6254BD waveform","*.hk6254"),("All files","*")])
        if name:
            try:
                self.load_record(name)
            except (OSError,ValueError) as exc:
                messagebox.showerror("Open waveform",str(exc))

    def open_sample(self):
        try:
            self.load_record(SAMPLE_RECORD)
        except (OSError,ValueError) as exc:
            messagebox.showerror("Open SAMPLE",str(exc))

    def load_record(self,name):
        wave,frame,count=Waveform.load(name)
        self.new_arm()
        self.state="Stop"
        self.replay=True
        self.run_key.configure(text="▶  Run")
        self.indicator.configure(text="REPLAY")
        self.history.clear()
        self.averager.key=None
        self.record,self.frame,self.average_count=wave,frame,count
        self.scales,self.coupling,self.probes=list(wave.scales),list(wave.couplings),list(wave.probes)
        self.source.set(f"CH{wave.source+1}")
        self.slope.set(wave.slope)
        self.input_mode.set(next(k for k,v in INPUT_MODES.items() if v==len(wave.raw)))
        self.source_box.configure(values=[f"CH{i+1}" for i in range(len(wave.raw))])
        self.trigger_range()
        self.level.set(wave.level)
        self.depth.set(wave.depth)
        self.div=wave.time_div
        self.time.set(fmt(wave.time_div,"s"))
        saved_rate=fmt(wave.rate,"Sa/s")
        if saved_rate in self.rates:
            self.manual_rate.set(saved_rate)
            self.acq_mode.set("Manual")
        else:
            self.acq_mode.set("Auto")
        self.delay.set(0)
        self.sync_acquisition_controls()
        self.visible[wave.source]=True
        self.select(wave.source)
        self.stats=[measure(ch,wave.rate) for ch in frame]
        self.horizontal()
        self.foot.configure(text="Offline replay · "+str(name))
        self.counter.configure(text="")

    def label(self,parent,text,size=12,color=FG,bold=False):
        return tk.Label(parent,text=text,bg=parent["bg"],fg=color,font=("Helvetica",size,"bold" if bold else "normal"))

    def button(self,parent,text,cmd):
        b=tk.Label(parent,text=text,bg="#32414b",fg=FG,padx=9,pady=5,cursor="hand2",font=("Helvetica",12,"bold"))
        b.configure(takefocus=True,highlightthickness=1,highlightbackground=PANEL,highlightcolor=FG)
        for event in ("<Button-1>","<Return>","<space>"):
            b.bind(event,lambda e:(cmd(),"break")[1])
        return b

    def section(self,parent,title):
        self.label(parent,title,10,MUTED,True).pack(anchor="w",pady=(5,2))

    def combo(self,parent,label,var,values,callback):
        row=tk.Frame(parent,bg=PANEL)
        row.pack(fill="x",pady=2)
        self.label(row,label).pack(side="left")
        box=ttk.Combobox(row,textvariable=var,values=values,state="readonly",width=14 if parent==self.tools_window else 9)
        if label in ("Time / div","Scale / div"):
            def step(direction):
                self.step_scale(box,callback,direction)
            self.button(row,"+",lambda:step(1)).pack(side="right",padx=1)
            box.pack(side="right")
            self.button(row,"−",lambda:step(-1)).pack(side="right",padx=1)
        else:
            box.pack(side="right")
        box.bind("<<ComboboxSelected>>",lambda e:callback())
        return box

    def step_scale(self,box,callback,direction):
        current=box.current()
        if box is self.timebox and current<0:
            # Take the adjacent knob detent after fitting a nonstandard record span.
            index=(min(len(TIME)-1,bisect_right(TIME,self.div)) if direction>0
                   else max(0,bisect_right(TIME,self.div)-1))
        else:
            index=max(0,min(len(box["values"])-1,current+direction))
        if index!=current:
            box.current(index)
            callback()

    def plot_scroll(self,event,vertical,precise):
        delta=event.delta
        if precise:
            dx,dy=map(int,self.root.tk.splitlist(
                self.root.tk.call("tk::PreciseScrollDeltas",delta)))
            if not dy or abs(dx)>abs(dy):
                return "break"
            now=time.monotonic()
            target=self.ch if vertical else "time"
            if (self.scroll_target!=target or now-self.scroll_time>.25
                    or self.scroll_pixels*dy<0):
                self.scroll_pixels=0
            self.scroll_target=target
            self.scroll_time=now
            self.scroll_pixels+=dy
            if abs(self.scroll_pixels)<40:
                return "break"
            delta=self.scroll_pixels
            self.scroll_pixels=0
        else:
            self.scroll_target=None
        if delta:
            direction=-1 if delta>0 else 1
            if vertical:
                self.step_scale(self.scbox,self.changed,direction)
            else:
                self.step_scale(self.timebox,self.horizontal,direction)
        return "break"

    def segment(self,parent,label,var,values,callback):
        row=tk.Frame(parent,bg=PANEL)
        row.pack(fill="x",pady=2)
        self.label(row,label).pack(side="left")
        buttons={}
        def refresh(*_):
            for value,button in buttons.items():
                selected=var.get()==value
                button.configure(bg="#456473" if selected else "#29363e",
                                 fg=FG if selected else MUTED)
        def pick(value):
            var.set(value)
            callback()
        for value in reversed(values):
            buttons[value]=self.button(row,value,lambda v=value:pick(v))
            buttons[value].pack(side="right",padx=1)
        var.trace_add("write",refresh)
        refresh()

    def slider(self,parent,title,var,low,high,step,cb):
        self.label(parent,title,11,MUTED).pack(anchor="w",pady=(3,0))
        box=tk.Scale(parent,variable=var,from_=low,to=high,resolution=step,orient="horizontal",
                     bg=PANEL,fg=FG,highlightthickness=0,borderwidth=0,troughcolor=GRID,
                     font=("Helvetica",10),command=lambda val:cb(),width=8,sliderlength=15)
        box.pack(fill="x")
        return box

    def select(self,i):
        n=INPUT_MODES[self.input_mode.get()]
        if i>=n:
            return
        self.ch=i
        self.active.configure(text=f"Channel {i+1}",fg=COLORS[i])
        self.on.configure(text="Display  ON" if self.visible[i] else "Display  OFF")
        values=[fmt(v*self.probes[i],"V") for v in RANGES]
        self.scbox.configure(values=values)
        self.scale.set(fmt(self.scales[i]*self.probes[i],"V"))
        self.couple.set(self.coupling[i])
        self.probe.set(f"{self.probes[i]}×")
        self.position.set(self.positions[i])
        for j,b in enumerate(self.channel_buttons):
            if j>=n:
                b.configure(text=f"CH{j+1}   Inactive",fg=MUTED,bg=PANEL,cursor="arrow",takefocus=False)
                continue
            b.configure(cursor="hand2",takefocus=True,text=f"CH{j+1}   {fmt(self.scales[j]*self.probes[j],'V')}/div" if self.visible[j] else f"CH{j+1}   Off",
                        fg=COLORS[j] if self.visible[j] else MUTED,bg="#374954" if i==j else PANEL)

    def visibility(self):
        self.visible[self.ch]=not self.visible[self.ch]
        self.select(self.ch)
        for cell in self.measures[self.ch]:
            if self.visible[self.ch]:
                cell.grid()
            else:
                cell.grid_remove()
        self.refresh_view()

    def move(self):
        self.positions[self.ch]=self.position.get()
        self.draw()

    def changed(self):
        self.scales[self.ch]=RANGES[self.scbox.current()]
        self.coupling[self.ch]=self.couple.get()
        self.select(self.ch)
        self.trigger_range()
        self.rearm()

    def change_probe(self):
        self.probes[self.ch]=int(self.probe.get()[:-1])
        self.select(self.ch)
        self.trigger_range()
        self.rearm()

    def source_index(self):
        return int(self.source.get()[-1])-1

    def trigger_range(self):
        i=self.source_index()
        span=self.scales[i]*self.probes[i]
        self.trig.configure(from_=-5*span,to=5*span,resolution=span/50)

    def new_source(self):
        self.trigger_range()
        self.midlevel()

    def midlevel(self):
        if self.frame and self.source_index()<len(self.frame):
            i=self.source_index()
            lo,hi,*_=self.stats[i]
            self.level.set(((lo+hi)/2-128)*self.record.scales[i]*self.record.probes[i]/25)
        self.rearm()

    def horizontal(self):
        self.averager.key=None
        self.previous_key=None
        if self.state!="Stop" or self.frame is None:
            return
        div=self.selected_time()
        self.set_view(div)
        self.refresh_view()

    def set_view(self,div):
        wave=self.record
        self.span=max(2.,min(wave.depth,div*10*wave.rate))
        self.div=self.span/(10*wave.rate)
        low=10*(self.span/2-wave.origin)/self.span
        high=10*(wave.depth-self.span/2-wave.origin)/self.span
        limits=(math.floor(low*10)/10,math.ceil(high*10)/10)
        if limits!=self.delay_limits:
            self.delay_limits=limits
            self.delay_slider.configure(from_=limits[0],to=limits[1])
        left=wave.origin+self.span*(self.delay.get()/10-.5)
        self.view_left=max(0.,min(wave.depth-self.span,left))
        self.pre=wave.origin-self.view_left
        # Use a continuous time axis; only the measurement slice needs whole samples.
        self.start=math.ceil(self.view_left-1e-9)
        self.width=math.ceil(self.view_left+self.span-1e-9)-self.start

    def fit_record(self):
        if not self.record:
            return
        if self.state!="Stop":
            self.toggle()
        self.delay.set(0)
        self.set_view(self.record.depth/(10*self.record.rate))
        self.time.set(fmt(self.div,"s"))
        self.refresh_view()

    def center_trigger(self):
        self.delay.set(0)
        self.shift_record()

    def selected_time(self):
        values={fmt(t,"s"):t for t in TIME}
        if self.time.get() in values:
            return values[self.time.get()]
        # Fit record can produce a view between the standard knob detents.
        return self.div

    def refresh_view(self):
        if self.record is None:
            return
        wave=self.record
        progress=f"    Avg {self.average_count}/{self.average.get()}" if self.average.get()>1 and not self.replay else ""
        if self.replay and self.average_count>1:
            progress=f"    Saved avg {self.average_count}"
        if self.state=="Stop" and not self.replay and self.average_count==1:
            progress=""
        self.readout.configure(text=f"H  {fmt(self.div,'s')}/div   ·   {fmt(wave.rate,'Sa/s')}   ·   {wave.depth:,} pts/ch{progress}")
        self.window_info.set(f"{len(wave.raw)} inputs   ·   {fmt(wave.rate,'Sa/s')}/ch   ·   {fmt(wave.depth/wave.rate,'s')} record"
                             f"\nWindow {self.start+1:,}–{self.start+self.width:,} / {wave.depth:,}   ·   {self.region.get()} measurements")
        self.update_measurements()
        self.draw()

    def rearm(self):
        self.averager.key=None
        self.previous_key=None
        self.draw()
        if self.state=="Stop":
            self.foot.configure(text="Stopped · Run or Single to acquire with the selected settings")

    def new_arm(self):
        self.wait_since=None
        if self.device:
            self.device.pending=None

    def toggle(self):
        self.new_arm()
        self.autopending=False
        self.state="Run" if self.state=="Stop" else "Stop"
        self.run_key.configure(text="▶  Run" if self.state=="Stop" else "■  Stop")
        self.indicator.configure(text=self.state.upper())

    def single(self):
        self.new_arm()
        self.autopending=False
        self.clear_analysis()
        self.state="Single"
        self.run_key.configure(text="■  Stop")
        self.indicator.configure(text="ARMED")

    def autoscale(self):
        self.new_arm()
        self.state="Run"
        self.run_key.configure(text="■  Stop")
        self.delay.set(0)
        self.time.set(fmt(.0002,"s"))
        self.acq_mode.set("Auto")
        self.sync_acquisition_controls()
        if not any(self.visible[:INPUT_MODES[self.input_mode.get()]]):
            self.visibility()
        self.autoscale_attempt=0
        self.autopending=True
        self.mode.set("Auto")

    def close(self):
        if self.device:
            self.device.close()
        self.root.destroy()

    def tick(self):
        if self.state!="Stop":
            try:
                self.acquire()
            except (ValueError,OSError,usb.core.USBError,TimeoutError) as exc:
                if self.device:
                    self.device.close()
                    self.device=None
                self.state="Stop"
                self.run_key.configure(text="▶  Run")
                self.indicator.configure(text="STOP")
                self.foot.configure(text=str(exc))
        self.root.after(10 if self.device and self.device.pending else 40,self.tick)

    def acquire(self):
        div=self.selected_time()
        inputs=INPUT_MODES[self.input_mode.get()]
        desired=self.rates[self.manual_rate.get()] if self.acq_mode.get()=="Manual" else 160/div
        rate,_,_=sampling_clock(desired,inputs)
        depth=self.depth.get()
        needed=max(2,math.ceil(div*10*rate-1e-8))
        if needed>depth or div*10*rate<1.99:
            fits=[f"{v:,}" for v in depths_for(inputs) if v>=needed]
            if needed>depth:
                choices="available depths "+", ".join(fits) if fits else "shorten time/div or lower the rate"
                warning=f"{needed:,} pts/ch required · {choices}"
            else:
                warning="Window contains fewer than two samples · increase time/div or rate"
            self.new_arm()
            self.window_info.set(warning)
            self.foot.configure(text=warning)
            self.indicator.configure(text="CONFIG")
            return
        if self.device is None:
            self.device=BenchDriver()
        self.device.apply(self.scales,self.coupling,rate,depth,inputs)
        i=self.source_index()
        threshold=128+25*self.level.get()/(self.scales[i]*self.probes[i])
        auto=self.mode.get()=="Auto" and self.state!="Single"
        result=self.device.capture_edge(i,self.slope.get()=="Falling",threshold,auto)
        if result is None:
            if self.wait_since is None:
                self.wait_since=time.monotonic()
            if self.state=="Single" or time.monotonic()-self.wait_since>.15:
                self.indicator.configure(text="ARMED")
                self.foot.configure(text=f"Waiting for hardware edge   ·   CH{i+1}   {self.slope.get()}   {fmt(self.level.get(),'V')}")
            return
        self.wait_since=None
        frame,locked,self.last_address=result
        actual=self.device.actual_rate
        stats=[measure(data,actual) for data in frame]
        if self.autopending:
            candidates=[j for j,st in enumerate(stats) if self.visible[j] and st[1]-st[0]>=6 and st[2]]
            if candidates:
                i=i if i in candidates else candidates[0]
                self.source.set(f"CH{i+1}")
            if self.autoscale_attempt<5:
                rescan=False
                for j in range(inputs):
                    if not self.visible[j]:
                        continue
                    current=RANGES.index(self.scales[j])
                    if min(frame[j])==0 or max(frame[j])==255:
                        desired=min(len(RANGES)-1,current+3)
                    elif stats[j][1]-stats[j][0]<6:
                        desired=max(0,current-2)
                    else:
                        continue
                    if current!=desired:
                        self.scales[j]=RANGES[desired]
                        rescan=True
                if rescan:
                    self.autoscale_attempt+=1
                    return
            centers={}
            for j,(lo,hi,_,_) in enumerate(stats):
                if self.visible[j]:
                    centers[j]=((lo+hi)/2-128)*self.scales[j]/25
                    target=(hi-lo)*self.scales[j]/25/min(2.5,6/max(1,len(candidates)))
                    if hi-lo>4:
                        options=[v for v in RANGES if v>=target]
                        self.scales[j]=options[0] if options else RANGES[-1]
            for rank,j in enumerate(centers):
                center=4-8*(rank+.5)/len(centers)
                self.positions[j]=max(-4,min(4,center-centers[j]/self.scales[j]))
            freq=stats[i][2]
            if freq:
                self.time.set(fmt(min(TIME,key=lambda t:abs(math.log(t*freq*10/3))),"s"))
            lo,hi=stats[i][:2]
            oldscale=self.device.settings[0][i]
            self.trigger_range()
            self.level.set(((lo+hi)/2-128)*oldscale*self.probes[i]/25)
            self.autopending=False
            self.select(self.ch)
            self.trigger_range()
            return
        wave=Waveform(tuple(frame),actual,tuple(self.scales),tuple(self.probes),tuple(self.coupling),
                      i,self.slope.get(),self.level.get(),self.device.trigger_index,div)
        self.replay=False
        key=(wave.rate,wave.depth,len(frame),wave.scales,wave.couplings,wave.probes,i,wave.slope,wave.level,div)
        if key!=getattr(self,"history_key",None) or not locked:
            self.history.clear()
        self.history_key=key
        if self.persistence.get()!="Off" and locked and self.record and getattr(self,"previous_key",None)==key and self.record.trigger_index is not None:
            self.history.append((self.record.origin,self.frame))
        self.previous_key=key
        self.record=wave
        self.frame=self.averager.add(wave,1 if self.state=="Single" else self.average.get())
        self.average_count=self.averager.count
        self.stats=stats if self.average_count==1 else [measure(ch,actual) for ch in self.frame]
        self.count+=1
        single=self.state=="Single"
        if single:
            self.state="Stop"
            self.run_key.configure(text="▶  Run")
        self.indicator.configure(text="SINGLE DONE" if single else "TRIG'D" if locked else "AUTO")
        self.counter.configure(text=f"Record {self.count:>8}")
        label="FPGA edge" if locked else "Auto timeout"
        self.foot.configure(text=f"USB connected   ·   {label}   CH{i+1}   {wave.slope}   {fmt(wave.level,'V')}   ·   Nominal voltage")
        self.set_view(div)
        self.refresh_view()

    def update_measurements(self):
        wave=self.record
        self.gate_text.set(f"Measure · {self.region.get()}")
        details=[f"{self.region.get()} · sample interval {fmt(1/wave.rate,'s')}",
                 f"{'Input':<6}{'High':>10}{'Low':>10}{'Amplitude':>12}{'Mean':>11}"]
        edge_lists=[]
        for i,cells in enumerate(self.measures):
            if i>=len(self.frame):
                for cell in cells:
                    cell.grid_remove()
                continue
            if self.visible[i] and not cells[0].winfo_manager():
                for cell in cells:
                    cell.grid()
            data=self.frame[i]
            vdiv=wave.scales[i]*wave.probes[i]
            if self.region.get()=="Visible window":
                data=data[self.start:self.start+self.width]
                low,high,freq,duty=measure(data,wave.rate)
            else:
                low,high,freq,duty=self.stats[i]
            gain=vdiv/25
            minimum,maximum=min(data),max(data)
            raw=wave.raw[i][self.start:self.start+self.width] if self.region.get()=="Visible window" else wave.raw[i]
            clipped=min(raw)==0 or max(raw)==255
            values=[f"CH{i+1}",f"{fmt(vdiv,'V')}  {wave.couplings[i]}  {wave.probes[i]}×"]
            for col,(value,unit) in enumerate((((maximum-minimum)*gain,"V"),(freq,"Hz"),(1/freq if freq else None,"s"))):
                display,self.measure_units[i][col]=measurement_text(value,unit,self.measure_units[i][col])
                values.append(display)
            values.extend(("—" if duty is None else f"{duty:.1f} %","CLIP" if clipped else ""))
            for cell,text in zip(cells,values):
                if cell["text"]!=text:
                    cell.configure(text=text)
            crossings=edges(data,(low+high)/2) if high-low>=6 else []
            edge_lists.append(crossings)
            if self.visible[i]:
                quality=[]
                if clipped:
                    quality.append("clipped")
                if not crossings:
                    quality.append("insufficient edges")
                elif freq is None:
                    quality.append("no full cycle")
                high_v=fmt((high-128)*gain,"V")
                low_v=fmt((low-128)*gain,"V")
                amplitude=fmt((high-low)*gain,"V")
                mean=fmt((statistics.fmean(data)-128)*gain,"V")
                details.append(f"CH{i+1:<4}{high_v:>10}{low_v:>10}{amplitude:>12}{mean:>11}")
                if quality:
                    details.append("      "+" · ".join(quality))
        if len(edge_lists)>=2 and all(self.visible[:2]):
            details.append("\nCH1 → CH2 rising Δt   "+fmt(channel_delay(*edge_lists[:2],wave.rate),"s"))
        self.details.set("\n".join(details))

    def cursor_fraction(self,event):
        if self.cursor.get()=="Time":
            return min(1,max(0,(event.x-32)/max(1,self.canvas.winfo_width()-56)))
        return min(1,max(0,(event.y-22)/max(1,self.canvas.winfo_height()-46)))

    def end_drag(self,event):
        self.marker_drag=None
        self.trace_anchor=None

    def trace_at(self,x,y):
        # Canvas line hit-testing includes the vertical face of a square wave.
        # Search outward so a nearby trace cannot steal a direct hit.
        c=self.canvas
        for radius in (1,3,5,8):
            hits=[]
            for item in c.find_overlapping(x-radius,y-radius,x+radius,y+radius):
                tags=c.gettags(item)
                if "trace" in tags:
                    hits.append(int(tags[1][2:]))
            if hits:
                return self.ch if self.ch in hits else hits[-1]
        return None

    def shift_record(self):
        if self.frame is None:
            return
        # Pan the displayed record immediately, including while armed or stopped.
        # At full-record zoom there is no additional data to pan into view.
        self.set_view(self.div)
        self.refresh_view()

    def cursor_press(self,event):
        self.end_drag(event)
        if self.cursor.get()=="Off":
            if self.frame is None:
                return
            height=max(1,self.canvas.winfo_height()-46)
            center=22+height/2
            if event.x<34:
                choices=[i for i in range(len(self.frame)) if self.visible[i] and i<INPUT_MODES[self.input_mode.get()]]
                if choices:
                    i=min(choices,key=lambda n:abs(event.y-center+self.positions[n]*height/8))
                    if abs(event.y-center+self.positions[i]*height/8)<20:
                        self.select(i)
                        self.marker_drag="position"
            elif event.x>self.canvas.winfo_width()-33:
                self.marker_drag="level"
            elif event.y<10:
                self.marker_drag="overview"
            elif event.y<24:
                self.marker_drag="delay"
            elif event.y<=self.canvas.winfo_height()-24:
                channel=self.trace_at(event.x,event.y)
                if channel is not None and channel<INPUT_MODES[self.input_mode.get()]:
                    self.select(channel)
                    self.trace_anchor=(event.x,event.y,self.positions[channel],self.delay.get())
                    self.marker_drag="trace"
            self.cursor_move(event)
            return
        values=self.cursor_x if self.cursor.get()=="Time" else self.cursor_y
        pos=self.cursor_fraction(event)
        self.drag=min(range(2),key=lambda i:abs(values[i]-pos))
        self.cursor_move(event)

    def cursor_move(self,event):
        if self.cursor.get()=="Off":
            divs=4-8*(event.y-22)/max(1,self.canvas.winfo_height()-46)
            if self.trace_anchor is not None:
                x,y,position,delay=self.trace_anchor
                dx,dy=event.x-x,event.y-y
                if self.marker_drag=="trace":
                    if max(abs(dx),abs(dy))<5:
                        return
                    self.marker_drag="trace_x" if abs(dx)>abs(dy) else "trace_y"
                if self.marker_drag=="trace_x":
                    value=delay-10*dx/max(1,self.canvas.winfo_width()-56)
                    self.delay.set(round(max(self.delay_limits[0],min(self.delay_limits[1],value)),1))
                    self.shift_record()
                else:
                    value=position-8*dy/max(1,self.canvas.winfo_height()-46)
                    self.position.set(round(max(-4,min(4,value)),1))
                    self.move()
                return
            if self.marker_drag=="position":
                self.position.set(round(max(-4,min(4,divs)),2))
                self.move()
            elif self.marker_drag=="level":
                i=self.source_index()
                v=(divs-self.positions[i])*self.scales[i]*self.probes[i]
                self.level.set(max(float(self.trig["from"]),min(float(self.trig["to"]),v)))
            elif self.marker_drag=="overview":
                fraction=(event.x-32)/max(1,self.canvas.winfo_width()-56)
                value=10*(self.record.depth*fraction-self.record.origin)/self.span
                self.delay.set(max(self.delay_limits[0],min(self.delay_limits[1],value)))
                self.shift_record()
            elif self.marker_drag=="delay":
                pos=(event.x-32)/max(1,self.canvas.winfo_width()-56)
                self.delay.set(round(max(-3,min(3,(.5-pos)*10)),1))
            return
        values=self.cursor_x if self.cursor.get()=="Time" else self.cursor_y
        values[self.drag]=self.cursor_fraction(event)
        self.draw()

    def draw(self):
        if self.frame is None:
            return
        c=self.canvas
        c.delete("all")
        w,h=c.winfo_width(),c.winfo_height()
        if w<100 or h<100:
            return
        l,r,t,b=32,w-24,22,h-24
        dx,dy=(r-l)/10,(b-t)/8
        sc,probes=self.record.scales,self.record.probes
        div,pre=self.div,self.pre
        for j in range(11):
            x=l+j*dx
            c.create_line(x,t,x,b,fill=GRID)
        for j in range(9):
            y=t+j*dy
            c.create_line(l,y,r,y,fill=GRID)
        for j in range(51):
            x=l+j*dx/5
            c.create_line(x,(t+b)/2-2,x,(t+b)/2+2,fill=MUTED)
        xt=l+pre/self.span*(r-l)
        bar_l=l+(r-l)*self.view_left/self.record.depth
        bar_r=l+(r-l)*(self.view_left+self.span)/self.record.depth
        c.create_line(l,4,r,4,fill=GRID,width=2)
        c.create_line(bar_l,4,bar_r,4,fill=MUTED,width=2)
        if self.record.trigger_index is not None and l<=xt<=r:
            c.create_polygon(xt-6,t-10,xt+6,t-10,xt,t,fill="#eaaa5e")
        for n in (0,2,4,5,6,8,10):
            x=l+n*dx
            c.create_text(x,b+12,text=fmt(0 if abs(n-pre/self.span*10)<.005 else (n-pre/self.span*10)*div,"s"),fill=MUTED,font=("Helvetica",10))
        for j,data in enumerate(self.frame):
            if not self.visible[j]:
                continue
            displayed_div=self.scales[j]*self.probes[j]
            divisions_per_code=sc[j]*probes[j]/(25*displayed_div)
            zero=(t+b)/2-self.positions[j]*dy
            c.create_polygon(l-27,zero-7,l-12,zero-7,l-5,zero,l-12,zero+7,l-27,zero+7,fill=COLORS[j])
            c.create_text(l-18,zero,text=str(j+1),fill=BG,font=("Helvetica",10,"bold"))
            if j==self.source_index():
                y=zero-self.level.get()/displayed_div*dy
                if t<=y<=b:
                    c.create_polygon(r+1,y,r+8,y-7,r+21,y-7,r+21,y+7,r+8,y+7,fill="#eaaa5e")
                    c.create_text(r+14,y,text="T",fill=BG,font=("Helvetica",10,"bold"))
            def trace(samples,start,color,tags=()):
                points=[]
                first=max(0,math.ceil(start-1e-9))
                last=min(len(samples),math.ceil(start+self.span-1e-9))
                step=max(1,math.ceil(self.span/(r-l))) if self.envelope.get()=="On" else 1
                for k in range(first,last,step):
                    x=l+(r-l)*(k-start)/self.span
                    block=samples[k:min(k+step,last)] if step>1 else None
                    codes=(min(block),max(block)) if block else (samples[k],)
                    for code in codes:
                        y=min(b,max(t,zero-(code-128)*dy*divisions_per_code))
                        points.extend((x,y))
                if len(points)>=4:
                    c.create_line(*points,fill=color,width=1,capstyle="butt",joinstyle="round",tags=tags)
            for origin,prior in self.history:
                if j<len(prior):
                    trace(prior[j],self.view_left+origin-self.record.origin,("#665826","#27565c","#68445a","#425c40")[j])
            trace(data,self.view_left,COLORS[j],("trace",f"ch{j}"))
        if self.cursor.get()=="Time":
            a,z=self.cursor_x
            for n,v in enumerate((a,z),1):
                x=l+v*(r-l)
                c.create_line(x,t,x,b,fill=FG,dash=(3,4))
                c.create_text(x+8,t+10,text=str(n),fill=FG)
            delta=abs(z-a)*div*10
            cursor_text=f"Δt   {fmt(delta,'s')}      1/Δt   {fmt(1/delta if delta else None,'Hz')}"
        elif self.cursor.get()=="Voltage":
            a,z=self.cursor_y
            for n,v in enumerate((a,z),1):
                y=t+v*(b-t)
                c.create_line(l,y,r,y,fill=FG,dash=(3,4))
                c.create_text(l+10,y-8,text=str(n),fill=FG)
            delta=(a-z)*8*self.scales[self.ch]*self.probes[self.ch]
            cursor_text=f"CH{self.ch+1}   ΔV   {fmt(delta,'V')}"
        else:
            return
        c.create_text(r,t-10,text=cursor_text,anchor="e",fill=FG)

    def save(self):
        if self.frame is None:
            return
        wave,frame,averages=self.record,self.frame,self.average_count
        name=filedialog.asksaveasfilename(defaultextension=".csv",initialfile="6254BD-waveform.csv")
        if name:
            try:
                write_csv(name,wave,frame,averages)
            except OSError as exc:
                messagebox.showerror("Save CSV",str(exc))



def main():
    window = tk.Tk()
    panel = Scope(window)
    if sys.argv[1:] == ["--smoke-test"]:
        try:
            backend = usb_backend()
            if backend is None:
                raise RuntimeError("libusb backend did not load")
            # Exercise the compiled library without opening or claiming a device.
            list(usb.core.find(find_all=True, backend=backend))
            panel.load_record(SAMPLE_RECORD)
            window.update_idletasks()
            panel.tick()
            measured = [row[2] for row in panel.stats[:2]]
            if panel.device is not None or not all(f and abs(f-1000)<5 for f in measured):
                raise RuntimeError(f"Unexpected offline SAMPLE measurements: {measured}")
            visible = panel.width
            panel.time.set("20 µs")
            panel.horizontal()
            if panel.width >= visible:
                raise RuntimeError("SAMPLE zoom check failed")
            print(f"Tk {window.tk.call('info', 'patchlevel')}; libusb loaded; SAMPLE {measured} Hz")
        finally:
            panel.close()
        return
    if len(sys.argv) == 2:
        panel.load_record(sys.argv[1])
    window.mainloop()


if __name__ == "__main__":
    main()
