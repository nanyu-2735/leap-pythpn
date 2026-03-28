# -*- coding: utf-8 -*-
"""
Leap Motion — 上下左右挥手检测 v3
解引用 pHands 指针读取 LEAP_HAND 数据
Ctrl+C 退出
"""

import ctypes
import os
import struct
import sys
import time
import traceback


# ==================== 结构体 ====================

class LEAP_CONNECTION_CONFIG(ctypes.Structure):
    _fields_ = [
        ("size",             ctypes.c_uint32),
        ("flags",            ctypes.c_uint32),
        ("server_namespace", ctypes.c_char_p),
    ]

class LEAP_CONNECTION_MESSAGE(ctypes.Structure):
    _fields_ = [
        ("size",    ctypes.c_uint32),
        ("type",    ctypes.c_uint32),
        ("pointer", ctypes.c_void_p),
        ("_pad",    ctypes.c_uint8 * 64),
    ]

eLeapEventType_Connection = 0x0001
eLeapEventType_Device     = 0x0003
eLeapEventType_Tracking   = 0x0100


def find_dll():
    here = os.path.dirname(os.path.abspath(__file__))
    for p in [os.path.join(here, "LeapC.dll"),
              os.path.join(os.getcwd(), "LeapC.dll")]:
        if os.path.isfile(p):
            return p
    return None


def safe_read(addr, n):
    """从内存地址读取 n 字节"""
    return bytes((ctypes.c_uint8 * n).from_address(addr))


def main():
    print("=" * 60)
    print("  Leap Motion — 上下左右挥手检测 v3")
    print("  (指针解引用版)")
    print("  Ctrl+C 退出")
    print("=" * 60)

    py_bits = 64 if sys.maxsize > 2**32 else 32
    print(f"[信息] Python {sys.version.split()[0]} ({py_bits}-bit)")

    dll_path = find_dll()
    if not dll_path:
        print("[失败] 找不到 LeapC.dll")
        input("按回车退出...")
        return

    dll = ctypes.CDLL(dll_path)
    print(f"[OK] DLL: {dll_path}")

    # ---- 函数签名 ----
    dll.LeapCreateConnection.argtypes = [
        ctypes.POINTER(LEAP_CONNECTION_CONFIG),
        ctypes.POINTER(ctypes.c_void_p)]
    dll.LeapCreateConnection.restype  = ctypes.c_uint32

    dll.LeapOpenConnection.argtypes   = [ctypes.c_void_p]
    dll.LeapOpenConnection.restype    = ctypes.c_uint32

    dll.LeapPollConnection.argtypes   = [
        ctypes.c_void_p, ctypes.c_uint32,
        ctypes.POINTER(LEAP_CONNECTION_MESSAGE)]
    dll.LeapPollConnection.restype    = ctypes.c_uint32

    dll.LeapCloseConnection.argtypes  = [ctypes.c_void_p]
    dll.LeapCloseConnection.restype   = None
    dll.LeapDestroyConnection.argtypes = [ctypes.c_void_p]
    dll.LeapDestroyConnection.restype  = None

    # ---- 创建 & 打开连接 ----
    hConn = ctypes.c_void_p()
    cfg = LEAP_CONNECTION_CONFIG()
    cfg.size = ctypes.sizeof(cfg)
    cfg.flags = 0
    cfg.server_namespace = None

    if dll.LeapCreateConnection(ctypes.byref(cfg), ctypes.byref(hConn)) != 0:
        print("[失败] CreateConnection"); return
    if dll.LeapOpenConnection(hConn) != 0:
        print("[失败] OpenConnection"); return
    print("[OK] 连接已打开\n")

    msg = LEAP_CONNECTION_MESSAGE()

    # ==================== 状态变量 ====================
    palm_off          = None
    calibrating       = True
    cal_hits          = {}
    cal_frame         = 0
    dumped            = False
    confirmed_ptr_off = None

    palm_history      = []      # [(timestamp, palm_x, palm_y)]
    last_swipe_h      = 0.0
    last_swipe_v      = 0.0
    swipe_count_L     = 0
    swipe_count_R     = 0
    swipe_count_U     = 0
    swipe_count_D     = 0
    frame_count       = 0
    t0                = time.time()

    # ---- 可调参数 ----
    SWIPE_THRESH_H = 120      # mm: 左右阈值
    SWIPE_THRESH_V = 40       # mm: 上下阈值 (降低以提高灵敏度)
    SWIPE_WINDOW   = 0.45     # s:  时间窗口
    SWIPE_COOL     = 0.8      # s:  冷却时间
    CAL_NEED       = 6
    HAND_BYTES     = 400

    print("等待 Leap Motion 服务...")
    print("-" * 60)

    while True:
        try:
            rc = dll.LeapPollConnection(hConn, 1000, ctypes.byref(msg))
            if rc != 0:
                continue

            if msg.type == eLeapEventType_Connection:
                print(">>> 已连接服务 ✓")
                continue
            if msg.type == eLeapEventType_Device:
                print(">>> 检测到设备 ✓")
                print("    请把手放到 Leap Motion 上方 10~30cm ...")
                print("-" * 60)
                continue
            if msg.type != eLeapEventType_Tracking or not msg.pointer:
                continue

            ptr = msg.pointer
            frame_count += 1
            now     = time.time()
            elapsed = now - t0

            # ---- 读 tracking event 头部 ----
            hdr    = safe_read(ptr, 52)
            nHands = struct.unpack_from('<I', hdr, 32)[0]

            if nHands == 0 or nHands > 10:
                palm_history.clear()
                if frame_count % 30 == 0:
                    print(f"\r[{elapsed:5.1f}s] 等待手... (帧#{frame_count})",
                          end='', flush=True)
                continue

            # ---- 获取 pHands 指针 ----
            pHands = None

            if confirmed_ptr_off is not None:
                pHands = struct.unpack_from('<Q', hdr, confirmed_ptr_off)[0]
            else:
                for try_off in [36, 40]:
                    if try_off + 8 > len(hdr):
                        continue
                    val = struct.unpack_from('<Q', hdr, try_off)[0]
                    if 0x10000 < val < 0x7FFFFFFFFFFF:
                        try:
                            _ = safe_read(val, 16)
                            pHands = val
                            confirmed_ptr_off = try_off
                            print(f"\n[OK] pHands 指针位于 tracking event +{try_off}")
                            print(f"     pHands = 0x{val:016X}")
                            break
                        except Exception:
                            continue

            if pHands is None or pHands < 0x10000 or pHands > 0x7FFFFFFFFFFF:
                if frame_count % 120 == 0:
                    p36 = struct.unpack_from('<Q', hdr, 36)[0]
                    p40 = struct.unpack_from('<Q', hdr, 40)[0]
                    print(f"\n[!] pHands 无效  +36=0x{p36:016X}  +40=0x{p40:016X}")
                continue

            # ---- 解引用 pHands → 读取 LEAP_HAND ----
            try:
                hand = safe_read(pHands, HAND_BYTES)
            except (OSError, ValueError) as e:
                if frame_count % 120 == 0:
                    print(f"\n[!] 读 LEAP_HAND 失败 @ 0x{pHands:016X}: {e}")
                continue

            # ===================== 校准阶段 =====================
            if calibrating:
                cal_frame += 1

                if not dumped:
                    dumped = True
                    print(f"\n[诊断] nHands={nHands}  pHands=0x{pHands:016X}")
                    print(f"  LEAP_HAND 前 200 字节:")
                    for row in range(0, 200, 16):
                        hexs = ' '.join(f'{b:02X}' for b in hand[row:row+16])
                        fvals = []
                        for fi in range(row, min(row+16, HAND_BYTES-4), 4):
                            fv = struct.unpack_from('<f', hand, fi)[0]
                            fvals.append(f"{fv:10.2f}")
                        print(f"  {row:3d}: {hexs}")
                        print(f"       {' '.join(fvals)}")
                    print()

                for off in range(0, HAND_BYTES - 12, 4):
                    try:
                        x = struct.unpack_from('<f', hand, off)[0]
                        y = struct.unpack_from('<f', hand, off+4)[0]
                        z = struct.unpack_from('<f', hand, off+8)[0]
                    except struct.error:
                        break
                    if (-300 < x < 300 and 50 < y < 600 and -300 < z < 300
                            and abs(x) + abs(z) > 1.0):
                        cal_hits[off] = cal_hits.get(off, 0) + 1

                best_off, best_cnt = None, 0
                for off, cnt in cal_hits.items():
                    if cnt > best_cnt:
                        best_cnt = cnt
                        best_off = off

                if best_cnt >= CAL_NEED and best_off is not None:
                    palm_off    = best_off
                    calibrating = False

                    x = struct.unpack_from('<f', hand, palm_off)[0]
                    y = struct.unpack_from('<f', hand, palm_off+4)[0]
                    z = struct.unpack_from('<f', hand, palm_off+8)[0]

                    hand_type_val = struct.unpack_from('<I', hand, 8)[0]
                    hand_type_str = {0: "左手", 1: "右手"}.get(
                        hand_type_val, f"未知({hand_type_val})")

                    print(f"\n{'='*60}")
                    print(f"  ✓ 校准成功!")
                    print(f"  pHands 偏移:  tracking event +{confirmed_ptr_off}")
                    print(f"  掌心偏移:     LEAP_HAND +{palm_off}")
                    print(f"  掌心坐标:     ({x:+.1f}, {y:+.1f}, {z:+.1f}) mm")
                    print(f"  检测到:       {hand_type_str}")

                    ranked = sorted(cal_hits.items(), key=lambda kv: -kv[1])
                    print(f"\n  候选列表 (前 10):")
                    for off, cnt in ranked[:10]:
                        cx = struct.unpack_from('<f', hand, off)[0]
                        cy = struct.unpack_from('<f', hand, off+4)[0]
                        cz = struct.unpack_from('<f', hand, off+8)[0]
                        tag = " ★ 选用" if off == palm_off else ""
                        print(f"    +{off:3d}: "
                              f"({cx:+7.1f}, {cy:+7.1f}, {cz:+7.1f}) "
                              f"x{cnt}{tag}")

                    print(f"{'='*60}")
                    print(f"  ★ 挥手检测已启动！上下左右移动手掌试试 ★")
                    print(f"  参数: 左右阈值={SWIPE_THRESH_H}mm  "
                          f"上下阈值={SWIPE_THRESH_V}mm")
                    print(f"         窗口={SWIPE_WINDOW}s  冷却={SWIPE_COOL}s")
                    print(f"-" * 60)
                else:
                    if cal_frame % 15 == 0:
                        print(f"\r[校准] 帧{cal_frame}  "
                              f"候选{len(cal_hits)}  "
                              f"最佳:+{best_off} ({best_cnt}/{CAL_NEED})",
                              end='', flush=True)
                    if cal_frame >= 300 and best_cnt < 2:
                        print(f"\n\n[失败] 校准 {cal_frame} 帧后未找到掌心坐标。")
                        print("  请把上面的 hex dump 发给我分析。")
                        break
                continue

            # ===================== 挥手检测阶段 =====================
            px = struct.unpack_from('<f', hand, palm_off)[0]
            py = struct.unpack_from('<f', hand, palm_off+4)[0]
            pz = struct.unpack_from('<f', hand, palm_off+8)[0]

            if not (-500 < px < 500 and 20 < py < 800 and -500 < pz < 500):
                continue

            palm_history.append((now, px, py))
            cutoff = now - SWIPE_WINDOW
            palm_history = [(t, x, y) for t, x, y in palm_history if t > cutoff]

            if len(palm_history) >= 5:
                dx = palm_history[-1][1] - palm_history[0][1]
                dy = palm_history[-1][2] - palm_history[0][2]
                dt = palm_history[-1][0] - palm_history[0][0]

                # 判断主方向: 水平 vs 垂直
                if abs(dx) > abs(dy):
                    # ---- 水平挥手 ----
                    if abs(dx) > SWIPE_THRESH_H and (now - last_swipe_h) > SWIPE_COOL:
                        speed = abs(dx / dt) if dt > 0 else 0
                        if dx > 0:
                            swipe_count_R += 1
                            print(f"\n  →→→  向右挥手！  "
                                  f"Δx={dx:+.0f}mm  速度={speed:.0f}mm/s  "
                                  f"[左{swipe_count_L} 右{swipe_count_R} "
                                  f"上{swipe_count_U} 下{swipe_count_D}]")
                        else:
                            swipe_count_L += 1
                            print(f"\n  ←←←  向左挥手！  "
                                  f"Δx={dx:+.0f}mm  速度={speed:.0f}mm/s  "
                                  f"[左{swipe_count_L} 右{swipe_count_R} "
                                  f"上{swipe_count_U} 下{swipe_count_D}]")
                        last_swipe_h = now
                        last_swipe_v = now
                        palm_history.clear()
                else:
                    # ---- 垂直挥手 ----
                    if abs(dy) > SWIPE_THRESH_V and (now - last_swipe_v) > SWIPE_COOL:
                        speed = abs(dy / dt) if dt > 0 else 0
                        if dy > 0:
                            swipe_count_U += 1
                            print(f"\n  ↑↑↑  向上挥手！  "
                                  f"Δy={dy:+.0f}mm  速度={speed:.0f}mm/s  "
                                  f"[左{swipe_count_L} 右{swipe_count_R} "
                                  f"上{swipe_count_U} 下{swipe_count_D}]")
                        else:
                            swipe_count_D += 1
                            print(f"\n  ↓↓↓  向下挥手！  "
                                  f"Δy={dy:+.0f}mm  速度={speed:.0f}mm/s  "
                                  f"[左{swipe_count_L} 右{swipe_count_R} "
                                  f"上{swipe_count_U} 下{swipe_count_D}]")
                        last_swipe_v = now
                        last_swipe_h = now
                        palm_history.clear()

            # 状态栏
            if frame_count % 8 == 0:
                wdx, wdy = 0, 0
                if len(palm_history) >= 2:
                    wdx = palm_history[-1][1] - palm_history[0][1]
                    wdy = palm_history[-1][2] - palm_history[0][2]
                print(
                    f"\r[{elapsed:5.1f}s] "
                    f"掌心:({px:+6.0f},{py:+6.0f},{pz:+6.0f})  "
                    f"Δx:{wdx:+5.0f} Δy:{wdy:+5.0f}  "
                    f"帧:{frame_count}    ",
                    end='', flush=True)

        except KeyboardInterrupt:
            print("\n\n用户退出 (Ctrl+C)")
            break

        except Exception as e:
            print(f"\n[错误] {type(e).__name__}: {e}")
            traceback.print_exc()
            time.sleep(0.1)

    # ---- 汇总 ----
    total_time = time.time() - t0
    print()
    print("=" * 60)
    print(f"  总帧数:    {frame_count}")
    print(f"  运行时长:  {total_time:.1f}s")
    print(f"  向左挥手:  {swipe_count_L} 次")
    print(f"  向右挥手:  {swipe_count_R} 次")
    print(f"  向上挥手:  {swipe_count_U} 次")
    print(f"  向下挥手:  {swipe_count_D} 次")
    print(f"  掌心偏移:  LEAP_HAND +{palm_off}")
    print("=" * 60)

    try:
        dll.LeapCloseConnection(hConn)
        dll.LeapDestroyConnection(hConn)
    except Exception:
        pass

    input("按回车退出...")


if __name__ == '__main__':
    main()