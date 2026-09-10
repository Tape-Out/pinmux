"""pinmux 的行为测试台：选择器真的按每针各自的 sel 走，焊盘控制按位落到引脚。

图案在 BSV 里现算，不在 Python 里展开成字面量——针数乘功能数最大到 2048 位，
写成十六进制常量既难看也容易抄错。图案取 `(f + p) 是不是偶数`：选错功能或
错位一格都会露馅，而全 0 全 1 那种对称数据什么也测不出来。

认矩阵：`pins`、`funcs`、`padctl` 都从这一点的旋钮来。padctl 关掉时期望反过来
——焊盘控制寄存器读回零、控制线全零。那正是「特性关掉的数组还能读写」
那个错的回归点。
"""
import json
import pathlib
import sys

out = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else ".")
out.mkdir(parents=True, exist_ok=True)
cfg = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
label = cfg.get("label", "")
k = cfg.get("knobs", {})
pins = int(k.get("pins", 32))
funcs = int(k.get("funcs", 4))
padctl = bool(k.get("padctl", True))

# 拿第 3 针做焊盘控制的样本，针数不够就退到第 0 针
tp = 3 if pins > 3 else 0
PADV = 0x2B          # od=1 pu=1 pd=0 cs=1 ds=2

if padctl:
    pad_check = f'''  // 焊盘控制：只有被写的那一针该动
  rule padCheck (ph == PadCheck);
    Bool wrong = False;
    if (odSeen[1][{tp}] != 1 || puSeen[1][{tp}] != 1) begin
      $display("FAIL pad control did not reach pin {tp}: od=%0d pu=%0d",
               odSeen[1][{tp}], puSeen[1][{tp}]);
      wrong = True;
    end
    if (dsSeen[1][{tp * 2 + 1}:{tp * 2}] != 2'b10) begin
      $display("FAIL drive strength for pin {tp} is %02b, want 10",
               dsSeen[1][{tp * 2 + 1}:{tp * 2}]);
      wrong = True;
    end
    if (wrong) bad <= True;
    ph <= Resel;
  endrule
'''
    verdict = ("the mux follows each pin's own select and follows a change of select, "
               "a selection with no function behind it leaves the pin undriven, "
               "and pad control lands per pin")
else:
    pad_check = f'''  // padctl 关着：寄存器读回零，控制线全零
  rule padCheck (ph == PadCheck);
    let x <- d.regs.access(RegReq {{ addr: 12'h400 + {tp} * 4, write: False,
                                     wdata: 0, wstrb: 4'hF }});
    Bool wrong = False;
    if (x.rdata != 0) begin
      $display("FAIL padctl is off but the register kept %08h", x.rdata);
      wrong = True;
    end
    if (odSeen[1] != 0 || puSeen[1] != 0 || dsSeen[1] != 0) begin
      $display("FAIL padctl is off but the control lines are not zero");
      wrong = True;
    end
    if (wrong) bad <= True;
    ph <= Resel;
  endrule
'''
    verdict = ("the mux follows each pin's own select and follows a change of select, "
               "a selection with no function behind it leaves the pin undriven, "
               "and the pad control gate really gates")

# 0 号针改选第 1 路之后该出什么：图案是 (f + p) % 2 == 0
resel_bit = 1 if (1 + 0) % 2 == 0 else 0
# 功能号 15 只有在路数不足 16 时才是「超范围」
oor = 15 if funcs < 16 else None
oor_rules = (f'''
  // 选一个根本没有的功能号。寄存器收得下（字段四位）却没有对应的那一路——
  // 硬件该让这根针不驱动，而不是随便挑一路顶上。
  rule oor (ph == Oor);
    if (t == 0) wr(12'h000, {oor});
    if (t > 6) begin ph <= OorChk; t <= 0; end
    else t <= t + 1;
  endrule

  rule oorChk (ph == OorChk);
    if (oeSeen[1][0] == 1) begin
      $display("FAIL pin 0 selects function {oor}, which does not exist, yet it drives");
      bad <= True;
    end
    ph <= Done;
  endrule
''' if oor is not None else '''
  rule oor (ph == Oor);
    ph <= Done;                  // 路数已经占满四位，没有超范围的号码
  endrule

  rule oorChk (ph == OorChk);
    ph <= Done;
  endrule
''')

txt = f'''package Pinmux{label}Tb;

import Vector::*;
import RegIf::*;
import Pinmux::*;

// 由 tb/mkpinmuxtb.py 生成，勿手改。
// 这一点：pins={pins} funcs={funcs} padctl={padctl}

Integer np = {pins};
Integer nf = {funcs};

typedef enum {{ Setup, Drive, Settle, MuxCheck, PadWrite, PadSettle, PadCheck,
               Resel, ReselChk, Oor, OorChk, Done }}
  Phase deriving (Bits, Eq);

(* synthesize *)
module mkPinmux{label}Tb(Empty);
  PinmuxIfc#(12, 32, {pins}, {funcs}) d <- mkPinmux(
      PinmuxCfg {{ padctl: {"True" if padctl else "False"} }});

  Reg#(Phase)    ph  <- mkReg(Setup);
  Reg#(Bit#(16)) s   <- mkReg(0);
  Reg#(Bit#(32)) cyc <- mkReg(0);
  Reg#(Bool)     bad <- mkReg(False);

  // 每一路的输出图案：(f + p) 是偶数就置一
  function Bit#(TMul#({pins}, {funcs})) outPat();
    Bit#(TMul#({pins}, {funcs})) o = 0;
    for (Integer f = 0; f < nf; f = f + 1)
      for (Integer p = 0; p < np; p = p + 1)
        if ((f + p) % 2 == 0) o[f * np + p] = 1;
    return o;
  endfunction

  // 方向用另一个图案，免得跟输出撞在一起看不出错位
  function Bit#(TMul#({pins}, {funcs})) oePat();
    Bit#(TMul#({pins}, {funcs})) o = 0;
    for (Integer f = 0; f < nf; f = f + 1)
      for (Integer p = 0; p < np; p = p + 1)
        if ((f + 2 * p) % 3 == 0) o[f * np + p] = 1;
    return o;
  endfunction

  // 第 p 针选第 (p mod nf) 路，于是每针的期望各不相同
  function Bit#({pins}) wantOut();
    Bit#({pins}) o = 0;
    for (Integer p = 0; p < np; p = p + 1)
      if (((p % nf) + p) % 2 == 0) o[p] = 1;
    return o;
  endfunction

  function Bit#({pins}) wantOe();
    Bit#({pins}) o = 0;
    for (Integer p = 0; p < np; p = p + 1)
      if (((p % nf) + 2 * p) % 3 == 0) o[p] = 1;
    return o;
  endfunction

  Reg#(Bit#({pins})) padIn <- mkReg(0);
  Reg#(Bit#(8)) t <- mkReg(0);   // 后面这几段自己的步数
  // 引脚那条规则每拍都跑，凡是它写、检查规则读的量都得用 CReg
  Reg#(Bit#({pins})) outSeen[2] <- mkCReg(2, 0);
  Reg#(Bit#({pins})) oeSeen[2]  <- mkCReg(2, 0);
  Reg#(Bit#({pins})) inSeen[2]  <- mkCReg(2, 0);
  Reg#(Bit#({pins})) odSeen[2]  <- mkCReg(2, 0);
  Reg#(Bit#({pins})) puSeen[2]  <- mkCReg(2, 0);
  Reg#(Bit#(TMul#({pins}, 2))) dsSeen[2] <- mkCReg(2, 0);

  // 驱动与采样必须分成两条规则：func_o 写的是 BypassWire，pad_o 读的是同一根，
  // 一条规则里既写又读就是并行冲突（G0004）。分开之后 bsc 自会把驱动排在前面。
  rule drivePins;
    d.pins_if.func_o(outPat());
    d.pins_if.func_oe(oePat());
    d.pins_if.pad_i(padIn);
  endrule

  rule samplePins;
    outSeen[0] <= d.pins_if.pad_o;
    oeSeen[0]  <= d.pins_if.pad_oe;
    inSeen[0]  <= d.pins_if.func_i;
    odSeen[0]  <= d.pad.od;
    puSeen[0]  <= d.pad.pu;
    dsSeen[0]  <= d.pad.ds;
  endrule

  rule tick_;
    cyc <= cyc + 1;
    if (cyc > 40000) begin
      $display("TIMEOUT in phase %0d", pack(ph));
      $finish(1);
    end
  endrule

  function Action wr(Bit#(12) a, Bit#(32) v) = action
    let _ <- d.regs.access(RegReq {{ addr: a, write: True,
                                     wdata: v, wstrb: 4'hF }});
  endaction;

  // 每针选一路，一条一条写下去
  rule setup (ph == Setup);
    wr(12'h000 + truncate(s << 2), zeroExtend(s) % fromInteger(nf));
    // 一个寄存器在一条规则里只留一条写路径，否则是并行冲突（G0004）
    if (s + 1 == fromInteger(np)) begin ph <= Drive; s <= 0; end
    else s <= s + 1;
  endrule

  rule drive (ph == Drive);
    padIn <= wantOut();          // 输入随便给个图案，只验它原样回得来
    ph <= Settle;
  endrule

  rule settle (ph == Settle);
    ph <= MuxCheck;
  endrule

  rule muxCheck (ph == MuxCheck);
    Bool wrong = False;
    if (outSeen[1] != wantOut()) begin
      $display("FAIL pad_o is %08h, want %08h", outSeen[1], wantOut());
      wrong = True;
    end
    if (oeSeen[1] != wantOe()) begin
      $display("FAIL pad_oe is %08h, want %08h", oeSeen[1], wantOe());
      wrong = True;
    end
    if (inSeen[1] != padIn) begin
      $display("FAIL func_i is %08h, want %08h", inSeen[1], padIn);
      wrong = True;
    end
    if (wrong) bad <= True;
    ph <= PadWrite;
  endrule

  rule padWrite (ph == PadWrite);
    wr(12'h400 + {tp} * 4, 32'h{PADV:08X});
    ph <= PadSettle;
  endrule

  rule padSettle (ph == PadSettle);
    ph <= PadCheck;
  endrule

{pad_check}
  // 改了选择，输出要跟着换。原来每针只选一次，选完就再没动过——
  // 把选择锁死成第一次的值也照样全绿。
  rule resel (ph == Resel);
    if (t == 0) wr(12'h000, 1);      // 0 号针改选第 1 路
    if (t > 6) begin ph <= ReselChk; t <= 0; end
    else t <= t + 1;
  endrule

  rule reselChk (ph == ReselChk);
    if (outSeen[1][0] != {resel_bit}) begin
      $display("FAIL pin 0 was reselected to function 1 but pad_o[0] is %0d, want {resel_bit}",
               outSeen[1][0]);
      bad <= True;
    end
    ph <= Oor;
    t  <= 0;
  endrule
{oor_rules}
  rule fin (ph == Done);
    if (bad) $display("FAILED");
    else $display("PASS pinmux: {verdict}");
    $finish(bad ? 1 : 0);
  endrule
endmodule

endpackage
'''

(out / f"Pinmux{label}Tb.bsv").write_text(txt, encoding="utf-8")
print(f"  pinmux 行为测试台就位：pins={pins} funcs={funcs} padctl={padctl}")
