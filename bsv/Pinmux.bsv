package Pinmux;

import Vector::*;
import RegIf::*;
import PinmuxRegs::*;

// 本包不认识任何总线：对外只给中立的 RegIf，接哪种总线由 wrap 或装配决定。
typedef struct {
  Bool padctl;
} PinmuxCfg;

// 焊盘那一侧的控制线，字段名照 ICS55 的 PBMUX 单元来，装配时直接对上。
interface PadCtl#(numeric type pins);
  (* always_ready, result = "pad_od" *) method Bit#(pins) od;
  (* always_ready, result = "pad_pu" *) method Bit#(pins) pu;
  (* always_ready, result = "pad_pd" *) method Bit#(pins) pd;
  (* always_ready, result = "pad_cs" *) method Bit#(pins) cs;
  (* always_ready, result = "pad_ds" *) method Bit#(TMul#(pins, 2)) ds;
endinterface

interface PinmuxPins#(numeric type pins, numeric type funcs);
  // 各功能块送来的输出与方向，一路一份
  (* always_ready, always_enabled, prefix = "" *)
  method Action func_o((* port = "func_o" *) Bit#(TMul#(pins, funcs)) v);
  (* always_ready, always_enabled, prefix = "" *)
  method Action func_oe((* port = "func_oe" *) Bit#(TMul#(pins, funcs)) v);
  // 回给各功能块的输入：每路都看得到焊盘，选中与否由功能块自己判断
  (* always_ready, result = "func_i" *) method Bit#(pins) func_i;

  (* always_ready, result = "pad_o"  *) method Bit#(pins) pad_o;
  (* always_ready, result = "pad_oe" *) method Bit#(pins) pad_oe;
  (* always_ready, always_enabled, prefix = "" *)
  method Action pad_i((* port = "pad_i" *) Bit#(pins) v);
endinterface

interface PinmuxIfc#(numeric type aw, numeric type dw,
                     numeric type pins, numeric type funcs);
  interface RegIf#(aw, dw) regs;
  interface PinmuxPins#(pins, funcs) pins_if;
  interface PadCtl#(pins)            pad;
endinterface

module mkPinmux#(PinmuxCfg cfg)(PinmuxIfc#(aw, dw, pins, funcs))
    provisos (Mul#(TDiv#(dw, 8), 8, dw), Add#(_a, 12, aw), Add#(_b, 4, dw),
              Add#(_c, 1, dw), Add#(_d, 2, dw));

  PinmuxRegsIfc#(aw, dw, pins, funcs) r <- mkPinmuxRegs(
      PinmuxRegsCfg { padctl: cfg.padctl });

  Wire#(Bit#(TMul#(pins, funcs))) fo  <- mkBypassWire;
  Wire#(Bit#(TMul#(pins, funcs))) foe <- mkBypassWire;
  Wire#(Bit#(pins))               pi  <- mkBypassWire;

  // 选择在编译期完全展开成每针一个 mux，不产生查表
  function Bit#(1) pick(Bit#(TMul#(pins, funcs)) src, Integer p);
    Bit#(1) v = 0;
    for (Integer f = 0; f < valueOf(funcs); f = f + 1)
      if (r.sel[p] == fromInteger(f)) v = src[f * valueOf(pins) + p];
    return v;
  endfunction

  function Bit#(pins) muxed(Bit#(TMul#(pins, funcs)) src);
    Bit#(pins) o = 0;
    for (Integer p = 0; p < valueOf(pins); p = p + 1)
      o[p] = pick(src, p);
    return o;
  endfunction

  interface regs = r.regs;
  interface PinmuxPins pins_if;
    method Action func_o(Bit#(TMul#(pins, funcs)) v);  fo._write(v);  endmethod
    method Action func_oe(Bit#(TMul#(pins, funcs)) v); foe._write(v); endmethod
    method Bit#(pins) func_i = pi;
    method Bit#(pins) pad_o  = muxed(fo);
    method Bit#(pins) pad_oe = muxed(foe);
    method Action pad_i(Bit#(pins) v); pi._write(v); endmethod
  endinterface
  interface PadCtl pad;
    method Bit#(pins) od = cfg.padctl ? pack(r.pad_od) : 0;
    method Bit#(pins) pu = cfg.padctl ? pack(r.pad_pu) : 0;
    method Bit#(pins) pd = cfg.padctl ? pack(r.pad_pd) : 0;
    method Bit#(pins) cs = cfg.padctl ? pack(r.pad_cs) : 0;
    method Bit#(TMul#(pins, 2)) ds = cfg.padctl ? pack(r.pad_ds) : 0;
  endinterface
endmodule

endpackage
