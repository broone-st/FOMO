#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FOMO（Fear of Missing Out）—— 轻量 A 股桌面看盘小组件
=========================================================

特点
----
* 纯 Python 标准库实现（ctypes 原生 Win32 窗口），零第三方依赖，任意 Python 3.8+ 可直接运行
* 迷你置顶小窗：默认白 / 浅灰配色，多套主题一键切换，支持自定义配色
* 小字体紧凑排版；涨跌幅两种显示模式：【隐蔽模式】灰/黑的 0.xxxx 小数，【正常模式】红涨绿跌百分比
* 股票名称默认以「拼音首字母大写」显示（（每个汉字取拼音首字母、大写）），可一键切回完整名称
* 外观可调：右键菜单 → 外观调节，用滑轨调「背景透明度 / 字体透明度 / 字号」
* 交易时段秒级轮询（默认 3 秒），休市自动降频
* **本程序不含任何数据源**：数值由同目录的 provider.py 按约定提供（见《给AI的提示词.md》）
* 全局快捷键（无需管理员权限）：
    Ctrl+Alt+H   隐藏 / 显示窗口
    Ctrl+Alt+T   切换配色主题
    Ctrl+Alt+P   切换置顶
    Ctrl+Alt+R   立即刷新
    Ctrl+Alt+N   切换名称显示形态
* 自选股清单 / 主题 / 刷新频率 / 快捷键 / 窗口位置均在 watch_config.json 中修改，保存后自动生效

数据来源：使用者自行提供的 provider.py。本程序只负责显示，不含任何接口地址、请求逻辑与数据解析代码。
本工具不构成投资建议。
"""

import ctypes
import json
import traceback
import os
import sys
import time
import subprocess
import tempfile
import importlib.util
import threading
from ctypes import wintypes
from datetime import datetime

# 打包（PyInstaller 等冻结）后，配置/日志放到可执行文件同级目录，而非临时解包目录
if getattr(sys, "frozen", False):
    APP_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.environ.get("MQ_CONFIG") or os.path.join(APP_DIR, "watch_config.json")
LOG_PATH = os.path.join(APP_DIR, "fomo.log")

# --------------------------------------------------------------------------
# Win32 常量
# --------------------------------------------------------------------------
CS_HREDRAW, CS_VREDRAW = 0x0002, 0x0001
WS_POPUP, WS_VISIBLE = 0x80000000, 0x10000000
WS_THICKFRAME = 0x00040000        # 有它系统才允许"拖边框缩放"
WS_EX_TOOLWINDOW, WS_EX_TOPMOST, WS_EX_NOACTIVATE = 0x00000080, 0x00000008, 0x08000000
WS_EX_LAYERED = 0x00080000
LWA_COLORKEY, LWA_ALPHA = 0x00000001, 0x00000002
# 逐像素透明：UpdateLayeredWindow + 32 位 DIB，背景可以真透明、菜单栏可以做半透明
DIB_RGB_COLORS, ULW_ALPHA = 0, 0x00000002
AC_SRC_OVER, AC_SRC_ALPHA = 0, 1
# 透明主题各区域的默认透明度（0-255）：菜单栏只留一层很淡的纱，边框只留一条淡线
ALPHA_HEADER, ALPHA_BORDER, ALPHA_GRIP = 82, 45, 150
# 鼠标移进窗口时把轮廓提亮，方便看清窗口边界与缩放目标；移出后回到淡淡的一圈
ALPHA_BORDER_HOVER = 165
# 透明键色：老式 COLORKEY 用（现在只有非透明主题会走 SetLayeredWindowAttributes）
TRANSPARENT_KEY = 0x00000000

SW_HIDE, SW_SHOW = 0, 5
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
SW_SHOWNOACTIVATE = 4
WM_RBUTTONDOWN = 0x0204
SWP_FRAMECHANGED = 0x0020
HWND_TOPMOST, HWND_NOTOPMOST = -1, -2
GWL_EXSTYLE = -20

WM_PAINT, WM_ERASEBKGND, WM_TIMER = 0x000F, 0x0014, 0x0113
WM_SIZE, WM_GETMINMAXINFO, WM_ENTERSIZEMOVE = 0x0005, 0x0024, 0x0231
WM_NCCALCSIZE = 0x0083            # 用来把 WS_THICKFRAME 带来的系统边框收干净
WM_MOUSEMOVE, WM_MOUSELEAVE = 0x0200, 0x02A3
WM_LBUTTONDOWN, WM_LBUTTONUP, WM_LBUTTONDBLCLK, WM_RBUTTONUP = 0x0201, 0x0202, 0x0203, 0x0205
WM_NCHITTEST, WM_NCLBUTTONDOWN, WM_COMMAND = 0x0084, 0x00A1, 0x0111
WM_NCLBUTTONDBLCLK = 0x00A3
WM_HOTKEY, WM_CLOSE, WM_DESTROY, WM_EXITSIZEMOVE = 0x0312, 0x0010, 0x0002, 0x0232
WM_APP = 0x8000
WM_APP_REFRESH = WM_APP + 1
WM_APP_INPUT = WM_APP + 2      # 后台线程拿到输入框结果后，通知 UI 线程去应用

HTCAPTION, HTCLIENT = 2, 1
# 非客户区命中：拖拽窗口边框实现自由缩放
HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT = 10, 11, 12, 13
HTTOPRIGHT, HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 14, 15, 16, 17
IDC_ARROW, IDI_APPLICATION = 32512, 32512
MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x0001, 0x0002, 0x0004, 0x0008, 0x4000
MF_STRING, MF_SEPARATOR, TPM_RETURNCMD, TPM_LEFTBUTTON = 0x0000, 0x0800, 0x0100, 0x0000
TRANSPARENT, SRCCOPY = 1, 0x00CC0020
DT_LEFT, DT_RIGHT, DT_CENTER, DT_VCENTER, DT_SINGLELINE, DT_END_ELLIPSIS = \
    0x0000, 0x0002, 0x0001, 0x0010, 0x0020, 0x8000
TME_LEAVE = 0x00000002
LOGPIXELSY = 90

CLASS_NAME = os.environ.get("MQ_CLASS") or "FOMOWndCls"
MUTEX_NAME = os.environ.get("MQ_MUTEX") or "Local\\FOMOMutex"
FONT_NAME = "Microsoft YaHei UI"

# 应用名（窗口标题 / 消息框）与主页栏（顶部栏）显示名
APP_NAME = "FOMO"
LICENSE_TEXT = """【使用前须知】

您对本软件的任何下载、使用、复制或分发行为，均视为您已充分阅读、理解并完全同意本协议的全部条款，
视为您已充分阅读、理解并完全同意本声明内容。

【免责声明】

1. 不构成投资建议。本软件是行情显示工具，不是投资顾问。所有数据仅供参考，不构成任何
   投资建议、买卖要约或收益承诺。股市有风险，投资需谨慎，盈亏由使用者自行承担。

2. 数据与合规。数据由使用者自行配置的数据源提供，作者不保证其真实性、完整性、及时性
   或合法性；数据源的使用条件与授权由使用者自行确认。请勿将行情数据对外发布、转发、
   公开展示或用于经营牟利。

3. 不含交易功能。本软件不包含任何交易、下单、资金划转功能。

4. 按现状提供、不承担责任。在法律允许的最大范围内，本软件按「原样」提供，不附带任何
   明示或暗示的担保或条件；因本协议、本软件的使用或其性质引起的任何损害（含但不限于
   资金损失、数据丢失、利润损失），许可人不向您承担任何责任，无论基于何种法律主张。

5. 仅限非商业用途，且不得用于宗教活动。本软件不得用于任何商业用途，也不得用于任何
   宗教活动、礼拜、宗教实践或宗教仪式；上述使用均需另行取得作者的书面许可。

6. 署名。「FOMO」名称与作者署名不得移除，也不得用于误导性宣传。

【许可协议】基于 PolyForm Noncommercial License 1.0.0 修改（不是未经修改的原版）

· 允许：个人使用（研究、实验、个人学习、私人娱乐、爱好项目、业余追求），且不预期商业应用；
· 允许：慈善组织、教育机构、公共研究组织、公共安全或卫生组织、环境保护组织、政府机构使用；
· 允许：修改、创作新作品，以及分发副本；
· 必须：随副本附带本协议全文或官方网址，并保留 Required Notice: 开头的声明行；
· 必须：保留版权与许可声明，不得移除「FOMO」名称与作者署名；
· 禁止：任何商业用途；
· 禁止：任何宗教用途（礼拜、宗教实践、宗教仪式、以宗教为主要目的的教育、宗教宣传推广）；
· 法律允许范围内，本软件按「原样」提供，许可人不承担责任。

对原版的三处修改：① 个人使用中删除「宗教活动」；② 新增「排除用途」一节；
③ 非商业目的增加对「排除用途」的交叉引用。

Copyright (c) 2026 Broone-st
Required Notice: Copyright (c) 2026 Broone-st

完整中英文协议见程序目录 LICENSE / LICENSE.zh-CN.md（冲突时以英文版为准）。
"""


APP_TITLE = "Fear of Missing Out"

# 涨跌幅显示模式
PCT_STEALTH = "stealth"   # 隐蔽模式：灰/黑的 0.xxxx 小数，不用红绿
PCT_NORMAL = "normal"     # 正常模式：红涨绿跌的百分比

# 外观滑块的三项（右键菜单 → 外观调节）
#   key        配置字段     范围下限 上限   显示
SLIDER_DEFS = (
    ("bg_opacity",   "背景", 10.0, 100.0, "pct"),
    ("font_opacity", "字体", 10.0, 100.0, "pct"),
    ("font_scale",   "字号",  0.6,   1.8, "x"),
)
# 外观面板打开时，透明主题下给面板垫一层薄纱的 alpha（否则整块透明，鼠标点不到滑块）
ALPHA_PANEL = 150

# 菜单命令 ID
ID_REFRESH, ID_THEME, ID_TOP, ID_COLLAPSE, ID_EDIT, ID_EXIT, ID_OPEN, ID_NAME, ID_HOME = \
    1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009
ID_PCT, ID_LOOK, ID_KLINE, ID_AUTO = 1010, 1011, 1012, 1013
ID_ALERT_STYLE, ID_ALERT_SET = 1014, 1015
ID_SHOWCODE = 1016
ID_ACH = 1017
ID_HOTKEYS = 1018
ID_CHART_STYLE_MAIN = 1019
ID_LICENSE = 1020
# 热键 ID
HK_HIDE, HK_THEME, HK_TOP, HK_REFRESH, HK_NAME = 1, 2, 3, 4, 5

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

# --------------------------------------------------------------------------
# 主题（十六进制 RGB；浅色为主，另附深灰夜用）
# --------------------------------------------------------------------------
THEMES = {
    "graywhite": {"label": "浅灰白", "bg": "F5F6F8", "panel": "FFFFFF", "header": "ECEFF3",
                  "text": "2B2F36", "sub": "8A9099", "border": "E2E5EA", "up": "C4483F", "down": "2F8F63"},
    "purewhite": {"label": "纯白", "bg": "FFFFFF", "panel": "FFFFFF", "header": "F4F5F6",
                  "text": "2B2F36", "sub": "9AA0A8", "border": "EBECEF", "up": "C4483F", "down": "2F8F63"},
    "warmwhite": {"label": "米白", "bg": "FAF8F3", "panel": "FFFFFF", "header": "F1EDE3",
                  "text": "3B362D", "sub": "9A927F", "border": "E7E0D2", "up": "B85C50", "down": "4A7C59"},
    "lightblue": {"label": "浅蓝", "bg": "EFF4FA", "panel": "FFFFFF", "header": "E2ECF6",
                  "text": "2C3A4A", "sub": "8496A8", "border": "D7E2EE", "up": "C14A42", "down": "2F8F63"},
    "slate": {"label": "深灰", "bg": "262B33", "panel": "2E343D", "header": "2A3038",
              "text": "D8DCE2", "sub": "8A929C", "border": "3B424C", "up": "E06C75", "down": "6FBF8B"},
    # 透明主题：走逐像素 alpha（UpdateLayeredWindow），所以这里可以既有"真透明背景"，
    # 又有"半透明菜单栏/边框"—— 菜单栏只留一层很淡的白纱，不再是硬邦邦一个方块。
    # 行情文字不加描边（GDI 抗锯齿在 alpha 通道里是干净的，不会像 COLORKEY 那样留黑边）
    "transparent": {"label": "透明", "bg": "000000", "panel": "000000", "header": "FFFFFF",
                    "header_text": "4E545C", "header_sub": "7C838C",
                    "text": "4E545C", "sub": "8A919B", "border": "A8AEB6", "grip": "8A919B",
                    "up": "D0544B", "down": "3E9C6E",
                    "alpha_header": 82, "alpha_border": 45, "alpha_grip": 150,
                    "transparent": True},
}
THEME_ORDER = ["graywhite", "purewhite", "warmwhite", "lightblue", "slate", "transparent"]
COLOR_KEYS = ("bg", "panel", "header", "text", "sub", "border", "up", "down")


def to_colorref(hexstr):
    """把主题里的 "RRGGBB" 转成 Win32 的 COLORREF。

    坑：COLORREF 在内存里的字节序是 0x00BBGGRR，直接把 0xRRGGBB 当 COLORREF 塞给
    GDI 会把红蓝通道对调 —— 最直接的后果是"涨"色(红)渲染成蓝色、涨跌色整个反过来。
    """
    v = int(str(hexstr).lstrip("#"), 16)
    return ((v & 0x0000FF) << 16) | (v & 0x00FF00) | ((v >> 16) & 0x0000FF)


def premult_color(colorref, alpha):
    """按 alpha 预乘颜色（COLORREF 布局 0x00BBGGRR），供 UpdateLayeredWindow 用。

    逐像素透明的位图要求颜色是"预乘 alpha"的：半透明菜单栏必须先把颜色乘上 0.32，
    系统再叠加时才对得上；文字的抗锯齿像素本来就是 GDI 跟底色混出来的，已经等于预乘值。
    """
    if alpha >= 255:
        return colorref
    b, g, r = (colorref >> 16) & 255, (colorref >> 8) & 255, colorref & 255
    return (((b * alpha) // 255) << 16) | (((g * alpha) // 255) << 8) | ((r * alpha) // 255)


def alpha_gray(alpha):
    """把 alpha 变成一个灰度色，用在"透明度图"上（B 通道即最终 alpha）。"""
    a = max(0, min(255, int(alpha)))
    return (a << 16) | (a << 8) | a


DEFAULT_CONFIG = {
    "theme": "graywhite",
    "width": 300,
    "height": None,
    "font_scale": 1.0,
    # 外观调节（右键菜单 → 外观调节…）
    "bg_opacity": 100,        # 0-100：整体背景不透明度（普通主题=整窗 alpha，透明主题=薄纱浓淡）
    "font_opacity": 100,      # 0-100：文字不透明度
    "pct_mode": PCT_STEALTH,  # stealth=灰黑 0.xxxx；normal=红绿百分比
    "always_on_top": True,
    "start_hidden": False,
    "refresh_fast": 3,
    "refresh_midday": 15,
    "refresh_slow": 30,
    # 本版本不含任何数据源：数值由 provider.py 提供（路径相对程序目录）。
    # 文件不存在显示 No source，加载/调用失败显示 Source error，绝不崩溃。
    "provider": {"path": "provider.py", "options": {}},
    "auto_hide_minutes": 0,          # 0 = 关闭；>0 = 每这么多分钟自动隐藏（含 K 线窗口）
    "license_seen": False,          # 首次启动是否已展示过开源协议
    "show_code": True,               # 行情行是否在名称后面显示 (代码)
    "alert": {"enabled": True, "style": "blink", "surge_window": 180,
              "surge_pct": 2.0, "hold": 10, "cooldown": 60, "levels": {}},
    "name_style": "pinyin",
    "hotkeys": {
        "toggle_hide": "ctrl+alt+h",
        "cycle_theme": "ctrl+alt+t",
        "toggle_top": "ctrl+alt+p",
        "refresh_now": "ctrl+alt+r",
        "cycle_name": "ctrl+alt+n",
    },
    "window": {"x": None, "y": None, "collapsed": False},
    # 默认自选股只用公开指数。这个文件可能随程序一起被拷给别人：
    # 默认值里写个人持仓既泄露隐私，也容易被误解成"推荐"。
    # 个人持仓写进 watch_config.json 即可（它会覆盖这里的默认值）。
    "watchlist": [
        {"code": "sh000001", "alias": "上证指数"},
        {"code": "sz399001", "alias": "深证成指"},
        {"code": "sz399006", "alias": "创业板指"},
    ],
    "custom_theme": None,
}


def log(msg):
    """写运行日志（便于排查），失败时静默忽略。"""
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > 200 * 1024:
            os.remove(LOG_PATH)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass


def load_config():
    """读取配置；文件不存在或损坏时回退默认值（并把默认值落盘一份）。"""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # 深拷贝
    first_run = not os.path.exists(CONFIG_PATH)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k, v in data.items():
                if v is not None:
                    cfg[k] = v
    except FileNotFoundError:
        pass                                  # 首次运行，正常情况，不算错误
    except Exception as e:
        log("load_config fallback to default: %r" % e)
    # 规范化
    if cfg.get("theme") not in THEMES and cfg.get("theme") != "custom":
        cfg["theme"] = "graywhite"
    # 宽度下限要和 WM_GETMINMAXINFO 里的拖动下限(_D(170))、README 保持一致：
    # 之前这里写 240，导致"手动拖到 170 并写进配置"的窗口一重启就被悄悄撑宽。
    cfg["width"] = max(170, min(560, int(cfg.get("width", 300))))
    for k in ("refresh_fast", "refresh_midday", "refresh_slow"):
        cfg[k] = max(2, int(cfg.get(k, DEFAULT_CONFIG[k])))
    cfg["always_on_top"] = bool(cfg.get("always_on_top", True))
    # 自动隐蔽：0 = 关闭；上限 240 分钟
    try:
        cfg["auto_hide_minutes"] = max(0, min(240, int(cfg.get("auto_hide_minutes") or 0)))
    except (TypeError, ValueError):
        cfg["auto_hide_minutes"] = 0
    cfg["show_code"] = bool(cfg.get("show_code", True))
    al = dict(DEFAULT_CONFIG["alert"])
    if isinstance(cfg.get("alert"), dict):
        al.update(cfg["alert"])
    cfg["alert"] = al
    if cfg.get("name_style") not in ("pinyin", "full"):
        cfg["name_style"] = "pinyin"
    # 外观：透明度下限给 10，避免被拖成"完全看不见又点不到"
    cfg["bg_opacity"] = max(10, min(100, int(cfg.get("bg_opacity", 100) or 100)))
    cfg["font_opacity"] = max(10, min(100, int(cfg.get("font_opacity", 100) or 100)))
    try:
        fs = float(cfg.get("font_scale") or 1.0)
    except (TypeError, ValueError):
        fs = 1.0
    cfg["font_scale"] = max(0.6, min(1.8, fs))
    if cfg.get("pct_mode") not in (PCT_STEALTH, PCT_NORMAL):
        cfg["pct_mode"] = PCT_STEALTH
    wl = []
    for item in cfg.get("watchlist") or []:
        if isinstance(item, str):
            code, alias = item, None
        elif isinstance(item, dict):
            code, alias = item.get("code"), item.get("alias")
        else:
            continue
        code = normalize_code(code)
        if code and code not in [w["code"] for w in wl]:
            wl.append({"code": code, "alias": alias})
    cfg["watchlist"] = wl or [dict(w) for w in DEFAULT_CONFIG["watchlist"]]
    # 首次运行就把默认配置落盘：这样"想改配置"的用户一打开文件夹就能看到这个文件，
    # 不用先改点什么它才出现（使用说明里写着"用记事本打开 watch_config.json"）。
    if first_run:
        save_config(cfg)
        log("config not found, defaults written to watch_config.json")
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        log("save_config failed: %r" % e)


def normalize_code(code):
    """把 '000001' / 'SH000001' 统一成 'sh000001'；纯 6 位按板块推断交易所。"""
    if not code:
        return None
    c = str(code).strip().lower()
    if c[:2] in ("sh", "sz") and len(c) == 8 and c[2:].isdigit():
        return c
    if len(c) == 6 and c.isdigit():
        if c[0] in "69" or c == "000001":
            return "sh" + c
        return "sz" + c
    return None


# --------------------------------------------------------------------------
# 汉字 → 拼音首字母（零第三方依赖）
# --------------------------------------------------------------------------
# 原理：GB2312 一级汉字（0xB0A1–0xD7F9，共 3755 字）按拼音顺序排列，
#      用 23 个「每声母段首字」做边界，二分/顺序比较即可定位首字母。
#      该表为中国大陆常见的「取汉字拼音首字母」标准做法，覆盖全部常用汉字。
_PY_BOUNDARY = "啊芭擦搭蛾发噶哈击喀垃妈拿哦啪期然撒塌挖昔压匝"
_PY_LETTERS = "ABCDEFGHJKLMNOPQRSTWXYZ"   # 无 I / U / V
_PY_TABLE = []
for _ch, _ltr in zip(_PY_BOUNDARY, _PY_LETTERS):
    try:
        _b = _ch.encode("gb2312")
        _PY_TABLE.append(((_b[0] << 8) | _b[1], _ltr))
    except UnicodeEncodeError:
        pass
del _ch, _ltr
# GB2312 一级字库码位范围（超出即为二级字库，按部首排序，无法取拼音首字母）
_GB1_LOW, _GB1_HIGH = 0xB0A1, 0xD7F9


def _py_letter(ch):
    """单个汉字 → 拼音首字母（大写）；非一级常用字返回 None。"""
    try:
        b = ch.encode("gb2312")
    except UnicodeEncodeError:
        return None
    if len(b) != 2:
        return None
    code = (b[0] << 8) | b[1]
    if code < _GB1_LOW or code > _GB1_HIGH:
        return None
    letter = None
    for c, ltr in _PY_TABLE:
        if code >= c:
            letter = ltr
        else:
            break
    return letter


def pinyin_initials(text):
    """把名称转成「拼音首字母大写」串。

    * 常用汉字 → 其拼音首字母（大写），（每个汉字取拼音首字母、大写）
    * 英文/数字/符号原样保留，如 *ST 华业 → *STHY
    * 个别取不到拼音的生僻字原样保留，保证不丢字
    """
    if not text:
        return ""
    out = []
    for ch in str(text):
        if "\u4e00" <= ch <= "\u9fff":
            out.append(_py_letter(ch) or ch)
        else:
            out.append(ch)
    return "".join(out)


def display_name(text, style):
    """按 name_style 决定显示形态：pinyin=拼音首字母大写，full=完整名称。"""
    if style == "pinyin":
        return pinyin_initials(text)
    return text or ""


# 用户晾着输入框不管时的兜底超时（秒）。因为等待发生在后台线程里，窗口不会因此卡住，
# 所以可以给得宽松些：超过这个时间才收掉子进程。
PS_INPUT_TIMEOUT = 600
# 当前挂着的输入框子进程（正常路径下会在 finally 里移除；退出程序时兜底收掉）
_ps_child = []


def ps_field_form(title, rows, note=""):
    """通用多字段表单：rows = [(标签, 初值), ...]，返回 list[str] 或 None。

    比 ps_input_box 灵活（那个只有一个输入框），比自绘窗体省事（复用系统控件）。
    """
    n = len(rows)
    h = 96 + n * 38 + (26 if note else 0)
    ps = [
        "Add-Type -AssemblyName System.Windows.Forms",
        "Add-Type -AssemblyName System.Drawing",
        "[System.Windows.Forms.Application]::EnableVisualStyles()",
        "$f = New-Object System.Windows.Forms.Form",
        "$f.Text = '" + title + "'",
        "$f.ClientSize = New-Object System.Drawing.Size(360, %d)" % h,
        "$f.StartPosition = 'CenterScreen'",
        "$f.FormBorderStyle = 'FixedDialog'",
        "$f.MaximizeBox = $false",
        "$f.MinimizeBox = $false",
        "$f.TopMost = $true",
    ]
    for i, (lab, val) in enumerate(rows):
        y = 14 + i * 38
        ps += [
            "$l%d = New-Object System.Windows.Forms.Label" % i,
            "$l%d.Text = '%s'" % (i, lab),
            "$l%d.Location = New-Object System.Drawing.Point(16, %d)" % (i, y + 3),
            "$l%d.Size = New-Object System.Drawing.Size(96, 22)" % i,
            "$f.Controls.Add($l%d)" % i,
            "$t%d = New-Object System.Windows.Forms.TextBox" % i,
            "$t%d.Text = '%s'" % (i, str(val).replace("'", "")),
            "$t%d.Location = New-Object System.Drawing.Point(118, %d)" % (i, y),
            "$t%d.Size = New-Object System.Drawing.Size(224, 22)" % i,
            "$f.Controls.Add($t%d)" % i,
        ]
    by = 14 + n * 38 + (18 if note else 0)
    if note:
        ps += [
            "$nl = New-Object System.Windows.Forms.Label",
            "$nl.Text = '%s'" % note,
            "$nl.Location = New-Object System.Drawing.Point(16, %d)" % (14 + n * 38),
            "$nl.Size = New-Object System.Drawing.Size(330, 18)",
            "$nl.ForeColor = [System.Drawing.Color]::Gray",
            "$f.Controls.Add($nl)",
        ]
    ps += [
        "$ok = New-Object System.Windows.Forms.Button",
        "$ok.Text = '确定'",
        "$ok.Location = New-Object System.Drawing.Point(118, %d)" % (by + 22),
        "$ok.Size = New-Object System.Drawing.Size(104, 28)",
        "$ok.DialogResult = 'OK'",
        "$f.Controls.Add($ok)",
        "$f.AcceptButton = $ok",
        "$no = New-Object System.Windows.Forms.Button",
        "$no.Text = '取消'",
        "$no.Location = New-Object System.Drawing.Point(236, %d)" % (by + 22),
        "$no.Size = New-Object System.Drawing.Size(104, 28)",
        "$no.DialogResult = 'Cancel'",
        "$f.Controls.Add($no)",
        "$f.CancelButton = $no",
        "if ($f.ShowDialog() -eq 'OK') {",
        "  $sep = [char]9",
        "  $s = ''",
    ]
    for i in range(n):
        ps.append("  $s = $s + $t%d.Text + $sep" % i)
    ps += ["  Write-Output $s", "}"]
    path = os.path.join(tempfile.gettempdir(), "fomo_field_form.ps1")
    proc = None
    try:
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(chr(10).join(ps))
        proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", path],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        _ps_child.append(proc)
        out, _ = proc.communicate(timeout=600)
    except Exception as e:
        log("field form failed: %r" % e)
        return None
    finally:
        try:
            if proc and proc in _ps_child:
                _ps_child.remove(proc)
        except Exception:
            pass
    lines = [x for x in (out or "").splitlines() if x.strip() != ""]
    if not lines:
        return None
    parts = lines[0].split("\t")
    while len(parts) < n:
        parts.append("")
    return parts[:n]


def ps_alert_form(vals, title="FOMO · 异动提示"):
    """弹出 5 个输入框的小表单：代码 / 分钟 / 幅度% / 止损价 / 目标价。

    返回 list[str]（5 项，原样字符串）或 None（取消）。
    用 WinForms 而不是 VB InputBox —— InputBox 只有一个输入框，塞不下 5 个字段。
    ★ 不要加 STARTUPINFO/SW_HIDE：进程第一次 ShowWindow 会忽略参数，表单会被一起藏掉。
    """
    labels = ("股票代码", "分钟", "幅度%", "止损价", "目标价")
    ps = [
        "Add-Type -AssemblyName System.Windows.Forms",
        "Add-Type -AssemblyName System.Drawing",
        "[System.Windows.Forms.Application]::EnableVisualStyles()",
        "$f = New-Object System.Windows.Forms.Form",
        "$f.Text = '" + title + "'",
        "$f.ClientSize = New-Object System.Drawing.Size(330, 236)",
        "$f.StartPosition = 'CenterScreen'",
        "$f.FormBorderStyle = 'FixedDialog'",
        "$f.MaximizeBox = $false",
        "$f.MinimizeBox = $false",
        "$f.TopMost = $true",
    ]
    for i in range(5):
        y = 14 + i * 38
        ps.append("$l%d = New-Object System.Windows.Forms.Label" % i)
        ps.append("$l%d.Text = '%s'" % (i, labels[i]))
        ps.append("$l%d.Location = New-Object System.Drawing.Point(16, %d)" % (i, y + 3))
        ps.append("$l%d.Size = New-Object System.Drawing.Size(78, 22)" % i)
        ps.append("$f.Controls.Add($l%d)" % i)
        ps.append("$t%d = New-Object System.Windows.Forms.TextBox" % i)
        ps.append("$t%d.Text = '%s'" % (i, vals[i]))
        ps.append("$t%d.Location = New-Object System.Drawing.Point(100, %d)" % (i, y))
        ps.append("$t%d.Size = New-Object System.Drawing.Size(210, 22)" % i)
        ps.append("$f.Controls.Add($t%d)" % i)
    ps += [
        "$ok = New-Object System.Windows.Forms.Button",
        "$ok.Text = '确定'",
        "$ok.Location = New-Object System.Drawing.Point(100, 200)",
        "$ok.Size = New-Object System.Drawing.Size(96, 26)",
        "$ok.DialogResult = 'OK'",
        "$f.Controls.Add($ok)",
        "$f.AcceptButton = $ok",
        "$no = New-Object System.Windows.Forms.Button",
        "$no.Text = '取消'",
        "$no.Location = New-Object System.Drawing.Point(210, 200)",
        "$no.Size = New-Object System.Drawing.Size(96, 26)",
        "$no.DialogResult = 'Cancel'",
        "$f.Controls.Add($no)",
        "$f.CancelButton = $no",
        "if ($f.ShowDialog() -eq 'OK') {",
        "  $sep = [char]9",
        "  Write-Output ($t0.Text + $sep + $t1.Text + $sep + $t2.Text + $sep + $t3.Text + $sep + $t4.Text)",
        "}",
    ]
    path = os.path.join(tempfile.gettempdir(), "fomo_alert_form.ps1")
    proc = None
    try:
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write("\n".join(ps))
        proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", path],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        _ps_child.append(proc)
        out, _ = proc.communicate(timeout=600)
    except Exception as e:
        log("alert form failed: %r" % e)
        return None
    finally:
        try:
            if proc and proc in _ps_child:
                _ps_child.remove(proc)
        except Exception:
            pass
    line = (out or "").strip().splitlines()
    if not line:
        return None
    parts = line[0].split("\t")
    while len(parts) < 5:
        parts.append("")
    return parts[:5]


def ps_ach_panel(text, title="FOMO · 成就"):
    """弹一个只读的成就列表窗体（独立进程，不阻塞主界面）。"""
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms" + chr(10) +
        "Add-Type -AssemblyName System.Drawing" + chr(10) +
        "[System.Windows.Forms.Application]::EnableVisualStyles()" + chr(10) +
        "$f = New-Object System.Windows.Forms.Form" + chr(10) +
        "$f.Text = '" + title + "'" + chr(10) +
        "$f.ClientSize = New-Object System.Drawing.Size(452, 392)" + chr(10) +
        "$f.StartPosition = 'CenterScreen'" + chr(10) +
        "$f.FormBorderStyle = 'FixedDialog'" + chr(10) +
        "$f.MaximizeBox = $false" + chr(10) +
        "$f.MinimizeBox = $false" + chr(10) +
        "$f.TopMost = $true" + chr(10) +
        "$t = New-Object System.Windows.Forms.TextBox" + chr(10) +
        "$t.Multiline = $true" + chr(10) +
        "$t.ReadOnly = $true" + chr(10) +
        "$t.ScrollBars = 'Vertical'" + chr(10) +
        "$t.BackColor = [System.Drawing.Color]::White" + chr(10) +
        "$t.Font = New-Object System.Drawing.Font('Microsoft YaHei UI', 10)" + chr(10) +
        "$t.Location = New-Object System.Drawing.Point(12, 12)" + chr(10) +
        "$t.Size = New-Object System.Drawing.Size(428, 328)" + chr(10) +
        "$t.Text = @'" + chr(10) + text + chr(10) + "'@" + chr(10) +
        "$f.Controls.Add($t)" + chr(10) +
        "$b = New-Object System.Windows.Forms.Button" + chr(10) +
        "$b.Text = '关闭'" + chr(10) +
        "$b.Location = New-Object System.Drawing.Point(176, 352)" + chr(10) +
        "$b.Size = New-Object System.Drawing.Size(100, 28)" + chr(10) +
        "$b.DialogResult = 'OK'" + chr(10) +
        "$f.Controls.Add($b)" + chr(10) +
        "$f.AcceptButton = $b" + chr(10) +
        "$f.ShowDialog() | Out-Null" + chr(10)
    )
    path = os.path.join(tempfile.gettempdir(), "fomo_ach_panel.ps1")
    try:
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(ps)
        proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", path],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        _ps_child.append(proc)
    except Exception as e:
        log("ach panel failed: %r" % e)


# ============================ 成就系统 ============================
ACH_PATH = os.path.join(APP_DIR, "achievements.json")

ACH_LIST = (
    ("calm", "心如止水",
     "除指数外全部跌停",
     ("添加的所有除指数外的个股全部跌停",)),
    ("salary", "月薪三千",
     "累计带薪摸鱼 240 小时",
     ("累计带薪摸鱼达到 240 小时",
      "你已摸鱼240个小时，已从老板手上多赚了3000块")),
    ("arrogant", "傲慢的交易员",
     "高调设置 + 4 个图表 + 不隐藏，连续 5 个交易日",
     ("在显示完整名称、代码，涨跌幅处于正常模式，不设置自动隐蔽，",
      "背景字体字号都为100%及以上，同时开启4个k线图窗口，",
      "不使用快捷键隐藏起来，不折叠的情况下运行5个交易日，",
      "可是为什么呢？你为什么要用FOMO？")),
    ("sleep", "睡觉战法",
     "连续 5 个交易日全程隐藏",
     ("连续5个交易日都处于隐藏状态，",
      "睡觉战法，无需盯盘")),
    ("immovable", "不动明王",
     "自选股全部跌超 5%，仍持到收盘",
     ("自选股全部跌幅超过5%，但截至收盘都没有关闭程序，",
      "心态真好")),
    ("alone", "主打陪伴",
     "48 小时没加过任何股票代码",
     ("启动程序后但不添加任何股票代码超过48小时",
      "您更需要一个桌宠")),
)
ACH_NAMES = {k: n for k, n, _s, _f in ACH_LIST}

def ach_is_index(code):
    """指数（sh000xxx / sz399xxx）不算"个股"，成就判定要排除。"""
    c = (code or "").lower()
    if not c:
        return False
    num = c[2:] if c[:2] in ("sh", "sz", "bj") else c
    return num.startswith("000") if c.startswith("sh") else num.startswith("399")


def ach_all_limit_down(watchlist, quotes):
    """除指数外全部跌停（≤ -9.8%）；至少一只个股，否则不算。"""
    n = 0
    for i, wl in enumerate(watchlist):
        if ach_is_index(wl.get("code")):
            continue
        q = quotes[i] if i < len(quotes) else None
        pct = (q or {}).get("pct")
        if pct is None or pct > -9.8:
            return False
        n += 1
    return n > 0


def ach_all_drop(watchlist, quotes, thr):
    """除指数外全部跌破 thr（负数），至少一只个股。"""
    n = 0
    for i, wl in enumerate(watchlist):
        if ach_is_index(wl.get("code")):
            continue
        q = quotes[i] if i < len(quotes) else None
        pct = (q or {}).get("pct")
        if pct is None or pct > thr:
            return False
        n += 1
    return n > 0


class AchStore:
    """成就进度：独立文件 achievements.json（不污染 watch_config.json）。"""

    def __init__(self, path=ACH_PATH):
        self.path = path
        self.data = {"unlocked": {}, "fish_sec": 0.0, "day": "", "d": {},
                     "streak_arrogant": 0, "streak_sleep": 0, "hist": {}}
        self.load()

    def load(self):
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, dict):
                self.data.update(raw)
        except Exception:
            pass
        self.data.setdefault("unlocked", {})
        self.data.setdefault("hist", {})
        self.rollover(datetime.now())

    def save(self):
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            log("achievement save failed: %r" % e)

    def today(self):
        return datetime.now().strftime("%Y-%m-%d")

    def rollover(self, now):
        """跨天：结算前一天（算连续天数），再开新的一天。"""
        d = self.data
        t = now.strftime("%Y-%m-%d")
        if d.get("day") == t:
            return None
        prev = d.get("d") or {}
        if d.get("day") and prev:
            ok_ar = bool(prev.get("arrogant_ok"))
            ok_sl = bool(prev.get("sleep_ok"))
            d["streak_arrogant"] = (d.get("streak_arrogant", 0) + 1) if ok_ar else 0
            d["streak_sleep"] = (d.get("streak_sleep", 0) + 1) if ok_sl else 0
            d.setdefault("hist", {})[d["day"]] = {
                "arrogant": ok_ar, "sleep": ok_sl,
                "vis": int(prev.get("vis", 0)), "hid": int(prev.get("hid", 0)),
            }
            d["day"] = t
            d["d"] = {"vis": 0.0, "hid": 0.0, "chart_max": 0, "hides": 0,
                      "collapsed": False, "bad": False, "close_alive": False,
                      "arrogant_ok": False, "sleep_ok": False}
            self.save()
            return prev
        d["day"] = t
        d["d"] = {"vis": 0.0, "hid": 0.0, "chart_max": 0, "hides": 0,
                  "collapsed": False, "bad": False, "close_alive": False,
                  "arrogant_ok": False, "sleep_ok": False}
        return None

    def unlocked(self, key):
        return key in self.data.get("unlocked", {})

    def unlock(self, key):
        if self.unlocked(key):
            return False
        self.data["unlocked"][key] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save()
        return True


class AchievementToast:
    """右下角升起的成就提示（Steam 风格），带滑入/停留/滑出动画。"""

    W, H = 380, 92
    _reg = {}
    _queue = []
    _cur = None
    _next_at = 0.0

    def __init__(self, app, name, desc, unlocked=True):
        self.app = app
        self.name = name
        self.desc = desc
        self.unlocked = unlocked
        self.hwnd = None
        self.t0 = None
        self.stage = 0          # 0 滑入 / 1 停留 / 2 滑出
        self.sx = 0
        self.sy = 0
        self.tx = 0
        self.ty = 0
        self._f1 = None
        self._f2 = None
        self._f3 = None
        self._create()

    @classmethod
    def show(cls, app, name, desc):
        cls._queue.append((app, name, desc))
        if cls._cur is None:
            cls._next()

    @classmethod
    def _next(cls):
        if cls._cur is not None or not cls._queue:
            return
        app, name, desc = cls._queue.pop(0)
        try:
            cls._cur = cls(app, name, desc)
        except Exception as e:
            log("toast failed: %r" % e)
            cls._cur = None
            cls._next()

    @classmethod
    def pump(cls):
        """由主窗口 250ms tick 驱动：上一张播完后隔 0.3 秒起下一张。

        不要在"窗口正在销毁的那个定时器回调里"直接创建下一个窗口 ——
        那样第 2 张起会被静默吞掉（只能弹出第一张）。
        """
        if cls._cur is not None or not cls._queue:
            return
        if time.time() < cls._next_at:
            return
        cls._next()

    def _font(self, px, weight=400):
        try:
            return gdi32.CreateFontW(-int(px), 0, 0, 0, weight, 0, 0, 0, 134, 0, 0, 0, 0,
                                     "Microsoft YaHei UI")
        except Exception:
            return None

    def _create(self):
        w, h = self.W, self.H
        self._f1 = self._font(13, 700)
        self._f2 = self._font(17, 700)
        self._f3 = self._font(12, 400)
        cls_name = "FOMOToastCls"
        if cls_name not in AchievementToast._reg:
            wc = WNDCLASSEX()
            wc.cbSize = ctypes.sizeof(WNDCLASSEX)
            wc.style = 0x0008            # CS_DBLCLKS
            self._proc = WNDPROC(self._wnd_proc)
            wc.lpfnWndProc = self._proc
            wc.hInstance = kernel32.GetModuleHandleW(None)
            wc.hCursor = user32.LoadCursorW(None, 32512)
            wc.lpszClassName = cls_name
            user32.RegisterClassExW(ctypes.byref(wc))
            AchievementToast._reg[cls_name] = True
        # 只用屏幕尺寸算位置：这台上 SPI_GETWORKAREA 返回值不可信（会把卡片放到屏幕外）
        sw = int(user32.GetSystemMetrics(0) or 1920)
        sh = int(user32.GetSystemMetrics(1) or 1080)
        self.tx = sw - w - 24
        self.ty = sh - h - 56          # 56 ≈ 任务栏 + 留白，保证整张卡片可见
        self.sx = self.tx
        self.sy = sh + 4          # 从屏幕下沿升起
        self.hwnd = user32.CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE,
            cls_name, "", WS_POPUP, self.sx, self.sy, w, h, None, None,
            kernel32.GetModuleHandleW(None), None)
        if not self.hwnd:
            raise RuntimeError("toast window create failed")
        AchievementToast._reg[self.hwnd] = self
        user32.ShowWindow(self.hwnd, SW_SHOWNOACTIVATE)
        self.t0 = time.time()
        self.stage = 0
        user32.SetTimer(self.hwnd, 1, 16, None)

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_PAINT:
                self._paint(hwnd)
                return 0
            if msg == WM_ERASEBKGND:
                return 1
            if msg == WM_TIMER:
                self._animate()
                return 0
            if msg in (WM_LBUTTONDOWN, WM_RBUTTONDOWN):
                self._finish()
                return 0
            if msg == WM_DESTROY:
                try:
                    user32.KillTimer(hwnd, 1)
                except Exception:
                    pass
                AchievementToast._reg.pop(hwnd, None)
                return 0
        except Exception as e:
            log("toast proc error: %r" % e)
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _paint(self, hwnd):
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            w, h = self.W, self.H
            mem = gdi32.CreateCompatibleDC(hdc)
            bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
            old = gdi32.SelectObject(mem, bmp)
            self.app._fill(mem, 0, 0, w, h, 0x221D1B)          # 深色卡片
            self.app._fill(mem, 0, 0, 4, h, 0x4AB2F0)          # 左侧金条
            self.app._fill(mem, 0, 0, w, 1, 0x453D3A)
            self.app._fill(mem, 0, h - 1, w, 1, 0x453D3A)
            self.app._text(mem, 14, 8, w - 24, 20,
                           "成就已解锁" if self.unlocked else "成就进度",
                           self._f1, 0x4AB2F0,
                           DT_LEFT | DT_VCENTER | DT_SINGLELINE)
            self.app._text(mem, 14, 29, w - 24, 26, self.name, self._f2, 0xFFFFFF,
                           DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS)
            self.app._text(mem, 14, 57, w - 24, 24, self.desc, self._f3, 0xACA09A,
                           DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS)
            gdi32.BitBlt(hdc, 0, 0, w, h, mem, 0, 0, SRCCOPY)
            gdi32.SelectObject(mem, old)
            gdi32.DeleteObject(bmp)
            gdi32.DeleteDC(mem)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))

    def _animate(self):
        if not self.hwnd:
            return
        el = time.time() - self.t0
        if self.stage == 0:
            k = min(1.0, el / 0.35)
            k = 1 - (1 - k) ** 3                                # ease-out
            y = int(self.sy + (self.ty - self.sy) * k)
            user32.SetWindowPos(self.hwnd, None, self.tx, y, 0, 0,
                                SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            if k >= 1.0:
                self.stage = 1
                self.t0 = time.time()
        elif self.stage == 1:
            if el >= 3.6:
                self.stage = 2
                self.t0 = time.time()
        else:
            k = min(1.0, el / 0.28)
            wa = wintypes.RECT()
            user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(wa), 0)
            y = int(self.ty + (wa.bottom + 4 - self.ty) * k)
            user32.SetWindowPos(self.hwnd, None, self.tx, y, 0, 0,
                                SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
            if k >= 1.0:
                self._finish()

    def _finish(self):
        h = self.hwnd
        self.hwnd = None
        try:
            if h:
                user32.DestroyWindow(h)
        except Exception:
            pass
        for f in (self._f1, self._f2, self._f3):
            try:
                if f:
                    gdi32.DeleteObject(f)
            except Exception:
                pass
        self._f1 = self._f2 = self._f3 = None
        AchievementToast._cur = None
        AchievementToast._next_at = time.time() + 0.3   # 交给主窗口 tick 起下一张


def ps_input_box(prompt, title, default=""):
    """用 PowerShell 的 VB InputBox 弹出一个文本输入框，返回用户输入（取消/为空返回 None）。

    ★ 关于"又大又蓝的控制台窗口"，以及那个**把它和输入框一起藏掉**的坑：

      powershell.exe 默认会给自己拉一个控制台窗口。掐掉它**只需要** `CREATE_NO_WINDOW`
      这一个开关，实测三种组合（在 pythonw.exe 下、隔离进程逐个查窗口）：

        · 只有 CREATE_NO_WINDOW                        -> 控制台 0 个，输入框**正常显示** ✔
        · 再加 STARTUPINFO + STARTF_USESHOWWINDOW/SW_HIDE -> 控制台 0 个，输入框**也被隐藏** ✘
        · 什么都不加（基线）                            -> 控制台 1 个（蓝色管理员窗）

      坑的机理：Win32 规定进程**第一次**调用 ShowWindow 时忽略传入的 nCmdShow，
      改用 STARTUPINFO.wShowWindow。而 InputBox 恰好就是 powershell 进程显示出来的
      **第一个窗口**，于是被强制 SW_HIDE —— 用户点了「＋」屏幕上什么都不会出现，
      主线程还卡在这里等它，表现就是"程序崩溃 / 未响应"。
      **不要再叠加 STARTUPINFO**；`-WindowStyle Hidden` 只作用于 PowerShell 自己的
      控制台窗口，不影响 InputBox，可以保留。

    若环境缺少 Microsoft.VisualBasic 程序集导致不可用，则退化为打开配置文件供手动编辑。
    """
    ps = (
        "$msg = @'\n" + prompt + "\n'@\n"
        "$ttl = @'\n" + title + "\n'@\n"
        "Add-Type -AssemblyName Microsoft.VisualBasic\n"
        "[Microsoft.VisualBasic.Interaction]::InputBox($msg, $ttl, '')\n"
    )
    path = os.path.join(tempfile.gettempdir(), "mq_inputbox.ps1")
    proc = None
    try:
        with open(path, "w", encoding="utf-8-sig") as f:
            f.write(ps)
        proc = subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-WindowStyle", "Hidden",
             "-ExecutionPolicy", "Bypass", "-File", path],
            stdin=subprocess.DEVNULL,          # 别去继承父进程的 stdin（pythonw 下可能无效）
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, errors="replace",       # 输出里有中文/非 ASCII 也别让解码炸掉
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
        _ps_child.append(proc)
        try:
            out, _err = proc.communicate(timeout=PS_INPUT_TIMEOUT)
        except subprocess.TimeoutExpired:
            # 用户把输入框晾着没管：收掉子进程（对话框随之关闭），按"未输入"处理
            proc.kill()
            proc.communicate()
            log("ps_input_box timeout after %ds" % PS_INPUT_TIMEOUT)
            return None
        out = (out or "").strip()
        return out if out else None
    except Exception as e:
        log("ps_input_box failed: %r" % e)
        try:
            os.startfile(CONFIG_PATH)
        except Exception:
            pass
        return None
    finally:
        if proc is not None:
            try:
                _ps_child.remove(proc)
            except ValueError:
                pass
        try:
            os.remove(path)
        except Exception:
            pass


def kill_ps_input_box():
    """收掉还挂着的输入框子进程（退出程序时用，免得留下孤儿对话框）。"""
    for proc in list(_ps_child):
        try:
            proc.kill()
        except Exception:
            pass
    del _ps_child[:]


def market_status(now=None):
    """返回 (状态文案, 频率档位)。A 股时段，节假日不自动识别（数据停更时会提示行情延迟）。

    状态文案按用户要求用英文三态（原来是"盘前/集合竞价/交易中/午间休市/收盘结算/已收盘"一长串中文）：
      Auction = 集合竞价(9:15–9:30)   Trading = 连续交易(9:30–11:30 / 13:00–15:00)
      Closed  = 其余所有时段（盘前、午间休市、收盘结算、已收盘、周末）
    第二个返回值是刷新频率档位，与文案无关，保持原样。
    """
    now = now or datetime.now()
    hm = now.hour * 60 + now.minute
    if now.weekday() >= 5:
        return "Closed", "slow"
    if hm < 9 * 60 + 15:
        return "Closed", "slow"
    if hm < 9 * 60 + 30:
        return "Auction", "fast"
    if hm < 11 * 60 + 30:
        return "Trading", "fast"
    if hm < 13 * 60:
        return "Closed", "mid"
    if hm < 15 * 60:
        return "Trading", "fast"
    return "Closed", "slow"


def _f(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


class ProviderClient:
    """加载并调用同目录的 provider.py。

    约定（详见《给AI的提示词.md》）：provider.py 必须暴露三个函数
      fetch(codes, options)                       → {code: quote}
      fetch_kline(code, period, count, adjust)    → (名称, bars)
      fetch_minute(code, days)                    → (名称, 昨收, minutes)
    程序本体**不含任何接口地址、请求逻辑与数据解析代码**。
    """

    def __init__(self, prov_cfg):
        cfgd = prov_cfg if isinstance(prov_cfg, dict) else {}
        self.path = str(cfgd.get("path") or "").strip()
        self.options = cfgd.get("options") or {}
        self.mod = None
        self.name = "--"
        self.state = "no_source"
        self.last_error = None
        self._tried_mtime = None
        if self.path:
            self._try_reload()

    def _resolve(self):
        if not self.path:
            return None
        return self.path if os.path.isabs(self.path) else os.path.join(APP_DIR, self.path)

    def _try_reload(self):
        """只在 provider.py 出现或变化时重新加载，避免每 3 秒刷一次日志。"""
        p = self._resolve()
        if not p or not os.path.isfile(p):
            self.mod = None
            self.state = "no_source"
            self.name = "--"
            return
        try:
            mtime = os.path.getmtime(p)
        except OSError:
            return
        if mtime == self._tried_mtime:
            return
        self._tried_mtime = mtime
        try:
            spec = importlib.util.spec_from_file_location("fomo_provider", p)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if not callable(getattr(mod, "fetch", None)):
                raise RuntimeError("provider.py 里没有 fetch(codes, options) 函数")
            self.mod = mod
            nm = getattr(mod, "SOURCE_NAME", None)
            self.name = nm.strip() if isinstance(nm, str) and nm.strip() else "My Source"
            self.state = "ok"
            self.last_error = None
            log("provider loaded: %s <- %s" % (self.name, p))
        except Exception as e:
            self.mod = None
            self.state = "error"
            self.last_error = repr(e)
            log("provider load failed: %r" % e)

    def fetch(self, codes):
        """返回 {code: quote}；未配置或出错时返回 None（调用方保留旧数值）。"""
        if self.mod is None:
            self._try_reload()
        if self.mod is None:
            return None
        try:
            data = self.mod.fetch(list(codes), dict(self.options))
        except Exception as e:
            self.state = "error"
            self.last_error = repr(e)
            log("provider fetch failed: %r" % e)
            return None
        if not isinstance(data, dict):
            self.state = "error"
            self.last_error = "fetch() 必须返回 dict"
            log(self.last_error)
            return None
        self.state = "ok"
        out = {}
        for c in codes:
            q = data.get(c)
            if not isinstance(q, dict):
                continue
            out[c] = {
                "code": c, "name": q.get("name") or "",
                "price": _f(q.get("price")), "prev": _f(q.get("prev")),
                "change": _f(q.get("change")), "pct": _f(q.get("pct")),
                "high": _f(q.get("high")), "low": _f(q.get("low")),
                "qtime": str(q.get("qtime") or ""),
            }
        return out

    def _call(self, fn_name, *args):
        """调 provider 的 K线/分时函数；缺函数就把原因抛给调用方（图表会显示出来）。"""
        if self.mod is None:
            self._try_reload()
        if self.mod is None:
            raise RuntimeError("没有可用的数据源（provider.py 未配置或加载失败）")
        fn = getattr(self.mod, fn_name, None)
        if not callable(fn):
            raise RuntimeError("provider.py 里没有 %s() 函数" % fn_name)
        try:
            return fn(*args)
        except TypeError:
            # 兼容旧版 provider（少一个参数）：fetch_kline 只给 3 个、fetch_minute 只给 1 个
            if len(args) > 1:
                return fn(*args[:-1])
            raise

    def fetch_kline(self, code, period="day", count=160, adjust="qfq"):
        return self._call("fetch_kline", code, period, count, adjust)

    def fetch_minute(self, code, days=1):
        return self._call("fetch_minute", code, days)


# --------------------------------------------------------------------------
# Win32 结构体
# --------------------------------------------------------------------------
WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                             ctypes.c_size_t, ctypes.c_ssize_t)


class WNDCLASSEX(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("style", wintypes.UINT),
                ("lpfnWndProc", WNDPROC), ("cbClsExtra", ctypes.c_int),
                ("cbWndExtra", ctypes.c_int), ("hInstance", wintypes.HINSTANCE),
                ("hIcon", wintypes.HICON), ("hCursor", ctypes.c_void_p),
                ("hbrBackground", ctypes.c_void_p), ("lpszMenuName", wintypes.LPCWSTR),
                ("lpszClassName", wintypes.LPCWSTR), ("hIconSm", ctypes.c_void_p)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class PAINTSTRUCT(ctypes.Structure):
    _fields_ = [("hdc", wintypes.HDC), ("fErase", wintypes.BOOL),
                ("rcPaint", RECT), ("fRestore", wintypes.BOOL),
                ("fIncUpdate", wintypes.BOOL), ("rgbReserved", ctypes.c_byte * 32)]


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", wintypes.UINT),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", wintypes.DWORD), ("pt", POINT)]


class TRACKMOUSEEVENT(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("hwndTrack", wintypes.HWND), ("dwHoverTime", wintypes.DWORD)]


class MINMAXINFO(ctypes.Structure):
    _fields_ = [("ptReserved", POINT), ("ptMaxSize", POINT), ("ptMaxPosition", POINT),
                ("ptMinTrackSize", POINT), ("ptMaxTrackSize", POINT)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
                ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 1)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


# 常用 API 原型
user32.DefWindowProcW.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEX)]
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID]
user32.DrawTextW.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                             ctypes.POINTER(RECT), wintypes.UINT]
user32.FillRect.argtypes = [wintypes.HDC, ctypes.POINTER(RECT), wintypes.HBRUSH]
user32.SetTimer.argtypes = [wintypes.HWND, ctypes.c_size_t, wintypes.UINT, wintypes.LPVOID]
user32.RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
user32.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.SendMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                ctypes.c_int, ctypes.c_int, wintypes.UINT]
user32.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
gdi32.CreateFontW.argtypes = [ctypes.c_int] * 5 + [wintypes.DWORD] * 8 + [wintypes.LPCWSTR]
gdi32.CreateFontW.restype = wintypes.HFONT
gdi32.CreateSolidBrush.argtypes = [wintypes.COLORREF]
gdi32.CreateSolidBrush.restype = wintypes.HBRUSH
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL
gdi32.GetStockObject.restype = wintypes.HGDIOBJ
gdi32.SetTextColor.argtypes = [wintypes.HDC, wintypes.COLORREF]
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.HDC,
                         ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.GetDeviceCaps.argtypes = [wintypes.HDC, ctypes.c_int]
user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
user32.MessageBoxW.restype = ctypes.c_int
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = wintypes.HWND
# 逐像素透明（透明主题用）：GDI 不写 alpha 通道，所以"颜色"和"透明度"分开画两张图再合成
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
user32.UpdateLayeredWindow.argtypes = [wintypes.HWND, wintypes.HDC, ctypes.POINTER(POINT),
                                       ctypes.POINTER(wintypes.SIZE), wintypes.HDC,
                                       ctypes.POINTER(POINT), wintypes.DWORD,
                                       ctypes.POINTER(BLENDFUNCTION), wintypes.DWORD]
user32.UpdateLayeredWindow.restype = wintypes.BOOL
user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG
user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.LONG]
user32.SetWindowLongW.restype = wintypes.LONG
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
user32.SystemParametersInfoW.argtypes = [wintypes.UINT, wintypes.UINT, ctypes.c_void_p, wintypes.UINT]
user32.SetLayeredWindowAttributes.argtypes = [wintypes.HWND, wintypes.COLORREF,
                                              ctypes.c_ubyte, wintypes.DWORD]
user32.SetLayeredWindowAttributes.restype = wintypes.BOOL


# --------------------------------------------------------------------------
# 窗口位置工具：保证窗口一定出现在看得见的地方
# --------------------------------------------------------------------------
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
SPI_GETWORKAREA = 0x0030


def primary_work_area():
    """主显示器的工作区（已刨掉任务栏）。"""
    r = RECT()
    if user32.SystemParametersInfoW(SPI_GETWORKAREA, 0, ctypes.byref(r), 0):
        return r.left, r.top, r.right, r.bottom
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


def virtual_screen():
    """所有显示器并集的范围（多屏时包含副屏）。"""
    x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
    y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
    w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
    h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    return x, y, x + w, y + h


def rect_visible_enough(x, y, w, h, need_w, need_h, area=None):
    """窗口是否在屏幕内露出足够的可见区域（默认按所有显示器并集判断）。"""
    l, t, r, b = area or virtual_screen()
    ix = max(0, min(x + w, r) - max(x, l))
    iy = max(0, min(y + h, b) - max(y, t))
    return ix >= need_w and iy >= need_h


def default_window_pos(width, height, margin=24):
    """默认位置：主屏工作区右下角（并保证不会超出工作区）。"""
    l, t, r, b = primary_work_area()
    x = max(l, min(r - width - margin, r - width))
    y = max(t, min(b - height - margin, b - height))
    return int(x), int(y)


def bring_into_view(hwnd):
    """已有实例再次被启动时调用：把窗口显示出来；若跑到屏幕外则搬回右下角。"""
    r = RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return
    w, h = r.right - r.left, r.bottom - r.top
    if not rect_visible_enough(r.left, r.top, w, h, min(140, w), min(28, h)):
        x, y = default_window_pos(w, h)
        log("existing window off-screen at (%d,%d) -> moved to (%d,%d)"
            % (r.left, r.top, x, y))
        user32.SetWindowPos(hwnd, HWND_TOPMOST, x, y, 0, 0,
                            SWP_NOSIZE | SWP_NOACTIVATE)
    user32.ShowWindowAsync(hwnd, SW_SHOW)

# 其余常用 API 原型（防止 64 位句柄截断）
_EXTRA_PROTOS = (
    (user32.BeginPaint, [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]),
    (user32.EndPaint, [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]),
    (user32.GetMessageW, [ctypes.POINTER(MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT]),
    (user32.TranslateMessage, [ctypes.POINTER(MSG)]),
    (user32.DispatchMessageW, [ctypes.POINTER(MSG)]),
    (user32.ShowWindow, [wintypes.HWND, ctypes.c_int]),
    (user32.ShowWindowAsync, [wintypes.HWND, ctypes.c_int]),
    (user32.UpdateWindow, [wintypes.HWND]),
    (user32.InvalidateRect, [wintypes.HWND, ctypes.c_void_p, wintypes.BOOL]),
    (user32.GetWindowRect, [wintypes.HWND, ctypes.POINTER(RECT)]),
    (user32.GetSystemMetrics, [ctypes.c_int]),
    (user32.KillTimer, [wintypes.HWND, ctypes.c_size_t]),
    (user32.FindWindowW, [wintypes.LPCWSTR, wintypes.LPCWSTR]),
    (user32.LoadIconW, [wintypes.HINSTANCE, ctypes.c_void_p]),
    (user32.LoadCursorW, [wintypes.HINSTANCE, ctypes.c_void_p]),
    (user32.GetDC, [wintypes.HWND]),
    (user32.ReleaseDC, [wintypes.HWND, wintypes.HDC]),
    (user32.DestroyWindow, [wintypes.HWND]),
    (user32.PostQuitMessage, [ctypes.c_int]),
    (kernel32.GetModuleHandleW, [wintypes.LPCWSTR]),
    (user32.CreatePopupMenu, []),
    (user32.DestroyMenu, [wintypes.HMENU]),
    (gdi32.SetBkMode, [wintypes.HDC, ctypes.c_int]),
    (gdi32.GetStockObject, [ctypes.c_int]),
)
for _fn, _proto in _EXTRA_PROTOS:
    try:
        _fn.argtypes = _proto
    except Exception:
        pass

# K 线图要用到的两条（MACD 的 DIF/DEA 折线）：画笔句柄必须声明 restype，
# 否则 64 位下会被截断成一个假句柄，Polyline 静默画不出来
gdi32.CreatePen.argtypes = [ctypes.c_int, ctypes.c_int, wintypes.COLORREF]
gdi32.CreatePen.restype = ctypes.c_void_p
gdi32.Polyline.argtypes = [wintypes.HDC, ctypes.POINTER(POINT), ctypes.c_int]
gdi32.Polyline.restype = wintypes.BOOL
# 分时图的折线要裁在价格区内：数据里出现异常值时，线会画到标题栏上去
gdi32.IntersectClipRect.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int,
                                    ctypes.c_int, ctypes.c_int]
gdi32.IntersectClipRect.restype = ctypes.c_int
gdi32.SelectClipRgn.argtypes = [wintypes.HDC, ctypes.c_void_p]
gdi32.SelectClipRgn.restype = ctypes.c_int
user32.BeginPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.BeginPaint.restype = wintypes.HDC
user32.EndPaint.argtypes = [wintypes.HWND, ctypes.POINTER(PAINTSTRUCT)]
user32.EndPaint.restype = wintypes.BOOL
gdi32.GetTextExtentPoint32W.argtypes = [wintypes.HDC, wintypes.LPCWSTR, ctypes.c_int,
                                        ctypes.POINTER(wintypes.SIZE)]
gdi32.GetTextExtentPoint32W.restype = wintypes.BOOL


def _lo16(v):
    v &= 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def _hi16(v):
    v = (v >> 16) & 0xFFFF
    return v - 0x10000 if v >= 0x8000 else v


def parse_hotkey(combo):
    """'ctrl+alt+h' -> (修饰键, 虚拟键码)；非法返回 (0, None)。"""
    if not combo:
        return 0, None
    mods, vk = 0, None
    for part in str(combo).lower().replace(" ", "").split("+"):
        if part == "ctrl":
            mods |= MOD_CONTROL
        elif part == "alt":
            mods |= MOD_ALT
        elif part == "shift":
            mods |= MOD_SHIFT
        elif part == "win":
            mods |= MOD_WIN
        elif len(part) == 1 and part.isalnum():
            vk = ord(part.upper())
        elif part.startswith("f") and part[1:].isdigit():
            n = int(part[1:])
            if 1 <= n <= 24:
                vk = 0x70 + n - 1
    return mods, vk


# --------------------------------------------------------------------------
# K 线图：数据获取 + MACD + 独立窗口（可多开）
# --------------------------------------------------------------------------
KLINE_COUNT = 160                 # 每次取多少根 K 线
KLINE_TIMEOUT = 8
CHART_CLASS = os.environ.get("MQ_CHART_CLASS") or "FOMOKLineWndCls"
CHART_WM_READY = WM_APP + 11      # K 线数据就绪（后台线程 → UI 线程）
CHART_WM_CODE = WM_APP + 12       # 输入框结果回传
PERIODS = (("day", "日线"), ("week", "周线"), ("month", "月线"),
           ("min", "分时"), ("min5", "五日"))
ADJUSTS = (("qfq", "前复权"), ("hfq", "后复权"), ("none", "不复权"))
# (key, 菜单标签, 默认开关)
INDS = (("MA", "MA(5/10/20/60)", True), ("BOLL", "BOLL(20)", False),
        ("VOL", "VOL 成交量", True), ("MACD", "MACD(12,26,9)", True),
        ("KDJ", "KDJ(9,3,3)", False), ("RSI", "RSI(6/12)", False))
CID_CHART_REFRESH, CID_CHART_PERIOD, CID_CHART_THEME, CID_CHART_CODE, CID_CHART_CLOSE = \
    2001, 2002, 2003, 2004, 2005
CID_PERIOD_BASE, CID_ADJ_BASE, CID_IND_BASE = 2100, 2110, 2120
CID_CHART_STYLE = 2006
CID_CHART_STYLE_RESET = 2007
CHART_TIMER_REFRESH, CHART_TIMER_PRESS = 1, 2
LONG_PRESS_MS = 1000          # 长按多久出现十字标线
CHART_DEF_W, CHART_DEF_H = 660, 430
CHART_MIN_W, CHART_MIN_H = 380, 260
WM_MOUSEWHEEL = 0x020A            # 主窗口用不到，K 线窗口用它做滚轮缩放

_charts = []                      # 当前打开的 K 线窗口
_chart_serial = [0]


def calc_ma(vals, n):
    """简单移动平均；不足 n 根的位置给 None。"""
    out, acc = [], 0.0
    for i, v in enumerate(vals):
        acc += v if v is not None else 0.0
        if i >= n:
            acc -= vals[i - n] if vals[i - n] is not None else 0.0
        out.append(acc / n if i >= n - 1 else None)
    return out


def calc_boll(closes, n=20, k=2.0):
    """布林带：中轨 = MA20，上下轨 = ±k 倍标准差。"""
    mid = calc_ma(closes, n)
    up, low = [], []
    for i in range(len(closes)):
        if i < n - 1:
            up.append(None)
            low.append(None)
            continue
        win = closes[i - n + 1:i + 1]
        m = mid[i]
        sd = (sum((c - m) ** 2 for c in win) / n) ** 0.5
        up.append(m + k * sd)
        low.append(m - k * sd)
    return mid, up, low


def calc_kdj(bars, n=9, m1=3, m2=3):
    """KDJ：RSV=(C−L9)/(H9−L9)×100，K/D 用 2/3 平滑，J=3K−2D。"""
    ks, ds, js = [], [], []
    k_prev = d_prev = 50.0
    for i, b in enumerate(bars):
        win = bars[max(0, i - n + 1):i + 1]
        hi = max(x["high"] for x in win)
        ln = min(x["low"] for x in win)
        rsv = (b["close"] - ln) / (hi - ln) * 100.0 if hi > ln else 50.0
        k_prev = (m1 - 1) / float(m1) * k_prev + rsv / float(m1)
        d_prev = (m2 - 1) / float(m2) * d_prev + k_prev / float(m2)
        ks.append(k_prev)
        ds.append(d_prev)
        js.append(3 * k_prev - 2 * d_prev)
    return ks, ds, js


def calc_rsi(closes, n):
    """RSI(n)：简单平均涨跌幅的平滑版本。"""
    out = [None] * len(closes)
    if len(closes) <= n:
        return out
    gain = loss = 0.0
    for i in range(1, n + 1):
        ch = closes[i] - closes[i - 1]
        gain += max(ch, 0.0)
        loss += max(-ch, 0.0)
    out[n] = 100.0 if loss == 0 else 100.0 - 100.0 / (1 + gain / loss)
    for i in range(n + 1, len(closes)):
        ch = closes[i] - closes[i - 1]
        gain = (gain * (n - 1) + max(ch, 0.0)) / n
        loss = (loss * (n - 1) + max(-ch, 0.0)) / n
        out[i] = 100.0 if loss == 0 else 100.0 - 100.0 / (1 + gain / loss)
    return out


def calc_macd(bars, fast=12, slow=26, sig=9):
    """标准 MACD：EMA12/EMA26 → DIF，DEA = DIF 的 EMA9，柱 = 2×(DIF−DEA)。"""
    dif, dea, hist = [], [], []
    e12 = e26 = None
    for b in bars:
        c = b["close"]
        e12 = c if e12 is None else e12 + (c - e12) * 2.0 / (fast + 1)
        e26 = c if e26 is None else e26 + (c - e26) * 2.0 / (slow + 1)
        d = e12 - e26
        dea_v = d if not dea else dea[-1] + (d - dea[-1]) * 2.0 / (sig + 1)
        dif.append(d)
        dea.append(dea_v)
        hist.append(2.0 * (d - dea_v))
    return dif, dea, hist


def _mix_color(c1, c2, f):
    """把 c1 按比例 f 混向 c2（f=0 取 c2，f=1 取 c1）—— 成交量柱要淡一点才不抢眼。"""
    f = max(0.0, min(1.0, f))

    def ch(sh):
        a, b = (c1 >> sh) & 255, (c2 >> sh) & 255
        return int(round(b + (a - b) * f))
    return (ch(16) << 16) | (ch(8) << 8) | ch(0)


def _fmt_vol(v):
    if not v:
        return "--"
    if v >= 1e8:
        return "%.2f亿" % (v / 1e8)
    if v >= 1e4:
        return "%.1f万" % (v / 1e4)
    return "%d" % int(v)


class ChartWindow:
    """一张 K 线图 = 一个独立的顶层窗口，可以同时开多个。

    这个窗口自己只管：代码、周期、缩放（滚轮改显示条数）。
    主题 / 字号 / 透明度统一跟随主窗口的全局外观 —— 主窗口的外观调节滑轨一动，
    所有 K 线窗口跟着变（不需要在每个窗口里再实现一套滑轨）。
    """

    def __init__(self, app, code, period="day"):
        self.app = app
        self.code = code
        self.period = period
        self.name = ""
        self.bars = []
        self.dif = []
        self.dea = []
        self.hist = []
        self.error = None
        self.loading = False
        self.hwnd = None
        self.dpi = app.dpi or 96
        self.width = self._D(CHART_DEF_W)
        self.height = self._D(CHART_DEF_H)
        self.f_title = None
        self.f_axis = None
        self.btn_rects = {}
        self.hover_btn = None
        self.zoom = 0                 # 0 = 按窗口宽度自动
        self.minutes = []             # 分时数据（period 为 min / min5 时用）
        self.adjust = "qfq"           # qfq 前复权 / hfq 后复权 / none 不复权
        self.ind = {k: d for k, _lb, d in INDS}
        self.ma = {}
        self.boll = (None, None, None)
        self.kdj = (None, None, None)
        self.rsi = {}
        self.vol_ma = {}
        self._vis_n = 0
        self._range = (0.0, 1.0)
        self._prect = None
        self._subs = []
        self.panes_w = None           # 面板相对权重（拖动分隔条后变成用户自定义）
        self._splitters = []          # [(y, 上key, 下key, 上高, 下高)]
        self.hover_sep = None
        self._drag_sep = None
        self._drag_y0 = 0
        self._drag_h0 = (0, 0)
        self._drag_w0 = (1.0, 1.0)
        self._cursor_sizens = None
        self.panel_open = False        # 外观滑块面板
        self._drag_slider = None
        self.cross_on = False
        self.cross_i = None
        self.cross_y = None
        self._pressed = False
        self._long_armed = False
        self._press_xy = (0, 0)
        self.pre_close = None         # 分时图的中轴（昨收）
        self._hidden_by_auto = False  # 是否由「自动隐蔽」藏起来的
        self._pending = None
        self._pending_code = None
        self._proc = None
        self._cls = None
        self._input_busy = False

    # ---- 尺寸：只跟 DPI 走；字号由全局 font_scale 决定 ----
    def _D(self, v):
        return int(round(v * self.dpi / 96.0))

    def _rebuild_fonts(self):
        for f in (self.f_title, self.f_axis):
            try:
                if f:
                    gdi32.DeleteObject(f)
            except Exception:
                pass
        fs = self.app.font_scale

        def mk(pt, bold):
            h = -int(round(pt * fs * self.dpi / 72.0))
            return gdi32.CreateFontW(h, 0, 0, 0, 700 if bold else 400, 0, 0, 0, 1, 4, 0, 5, 0, FONT_NAME)

        self.f_title = mk(9, True)
        self.f_axis = mk(8, False)

    def _apply_alpha(self):
        if not self.hwnd:
            return
        a = int(round(255.0 * max(10, min(100, self._bg_pct())) / 100.0))
        try:
            user32.SetLayeredWindowAttributes(self.hwnd, 0, max(1, min(255, a)), LWA_ALPHA)
        except Exception as e:
            log("chart alpha failed: %r" % e)

    def _invalidate(self):
        if self.hwnd:
            user32.InvalidateRect(self.hwnd, None, False)

    def close(self):
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)

    # ---- 创建窗口 ----
    def open(self):
        hinst = kernel32.GetModuleHandleW(None)
        self._proc = WNDPROC(self._wnd_proc)
        _chart_serial[0] += 1
        self._cls = "%s#%d" % (CHART_CLASS, _chart_serial[0])
        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.style = CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.hIcon = user32.LoadIconW(None, IDI_APPLICATION)
        wc.hCursor = user32.LoadCursorW(None, IDC_ARROW)
        wc.lpszClassName = self._cls
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            log("chart: RegisterClassExW failed")
            return False
        win = self.app.cfg.get("window") or {}
        bx, by = win.get("x"), win.get("y")
        if bx is None or by is None:
            bx, by = default_window_pos(self.width, self.height, self._D(24))
        off = self._D(26) * len(_charts)          # 多开时依次错开，不叠在一起
        x, y = int(bx) + off, int(by) + off
        if not rect_visible_enough(x, y, self.width, self.height, self._D(140), self._D(90)):
            x, y = default_window_pos(self.width, self.height, self._D(24))
        self.hwnd = user32.CreateWindowExW(
            WS_EX_TOOLWINDOW | WS_EX_LAYERED | WS_EX_NOACTIVATE
            | (WS_EX_TOPMOST if self.app.always_on_top else 0),
            self._cls, "FOMO · K线", WS_POPUP | WS_THICKFRAME,
            x, y, self.width, self.height, None, None, hinst, None)
        if not self.hwnd:
            log("chart: CreateWindowExW failed")
            return False
        _charts.append(self)
        self._rebuild_fonts()
        self._apply_alpha()
        user32.SetTimer(self.hwnd, CHART_TIMER_REFRESH, 120000, None)  # 两分钟自动刷一次（当天那根会动；别太频繁，公开接口会被限流）
        user32.ShowWindow(self.hwnd, SW_SHOW)
        user32.UpdateWindow(self.hwnd)
        self._start_load()
        return True

    # ---- 数据 ----
    def _start_load(self):
        if self.loading:
            return
        self.loading = True
        threading.Thread(target=self._load_worker, daemon=True).start()

    def _load_worker(self):
        name, bars, err, pre = "", [], None, None
        try:
            if self.period in ("min", "min5"):
                name, pre, bars = self.app.client.fetch_minute(
                    self.code, 5 if self.period == "min5" else 1)
                if not bars:
                    err = "这个代码没取到分时数据"
            else:
                name, bars = self.app.client.fetch_kline(
                    self.code, self.period, 160, self.adjust)
                if not bars:
                    err = "这个代码没取到数据"
        except Exception as e:
            err = "取数失败：%s" % type(e).__name__
            log("chart load failed: %r" % e)
        self._pending = (self.code, self.period, name, bars, err, pre)
        if self.hwnd:
            try:
                user32.PostMessageW(self.hwnd, CHART_WM_READY, 0, 0)
            except Exception:
                pass

    def _on_ready(self):
        self.loading = False
        pend = self._pending
        self._pending = None
        if not pend:
            return
        code, period, name, bars, err, pre = pend
        if code != self.code or period != self.period:
            self._start_load()                     # 期间又换了代码/周期，重来
            return
        self.error = err
        if bars:
            self.name = name or self._name_from_watchlist()
            if period in ("min", "min5"):
                self.minutes = bars
                self.pre_close = pre
            else:
                self.bars = bars
                self.dif, self.dea, self.hist = calc_macd(bars)
                self._calc_ind()
            self.cross_on = False
        self._invalidate()

    def hide_by_auto(self):
        """自动隐蔽：把窗口藏起来（等 Ctrl+Alt+H 再显示）。"""
        if self.hwnd:
            user32.ShowWindow(self.hwnd, SW_HIDE)
            self._hidden_by_auto = True

    def show_by_auto(self):
        if self.hwnd and self._hidden_by_auto:
            user32.ShowWindow(self.hwnd, SW_SHOW)
            self._hidden_by_auto = False

    def _calc_ind(self):
        """一次算好所有指标（只在数据到达时算，绘制时直接取）。"""
        closes = [b["close"] for b in self.bars]
        self.ma = {n: calc_ma(closes, n) for n in (5, 10, 20, 60)}
        self.boll = calc_boll(closes, 20)
        self.kdj = calc_kdj(self.bars)
        self.rsi = {6: calc_rsi(closes, 6), 12: calc_rsi(closes, 12)}
        vols = [b["vol"] for b in self.bars]
        self.vol_ma = {5: calc_ma(vols, 5), 10: calc_ma(vols, 10)}

    def _name_from_watchlist(self):
        """备源不给名称时，用自选股里配置的别名顶上。"""
        for w in (self.app.watchlist or []):
            if w.get("code") == self.code:
                return w.get("alias") or ""
        return ""

    # ---- 换代码 / 换周期 ----
    def _ask_code(self):
        if self._input_busy:
            return
        self._input_busy = True
        threading.Thread(target=self._code_worker, daemon=True).start()

    def _code_worker(self):
        try:
            text = ps_input_box("输入证券代码（如 sh000001、sz399001），留空取消", "K 线 · 输入代码", self.code)
        finally:
            self._input_busy = False
        if not text or not text.strip():
            return
        code = normalize_code(text.strip().split()[0])
        if not code:
            return
        self._pending_code = code
        if self.hwnd:
            try:
                user32.PostMessageW(self.hwnd, CHART_WM_CODE, 0, 0)
            except Exception:
                pass

    def _on_code_msg(self):
        code = self._pending_code
        self._pending_code = None
        if not code:
            return
        self.code = code
        self.name = ""
        self._start_load()
        self._invalidate()

    def _step_code(self, delta):
        wl = [w["code"] for w in (self.app.watchlist or [])]
        if not wl:
            return
        try:
            i = wl.index(self.code)
        except ValueError:
            i = -1 if delta > 0 else 0
        self.code = wl[(i + delta) % len(wl)]
        self.name = ""
        self._start_load()
        self._invalidate()

    def _cycle_period(self):
        keys = [p[0] for p in PERIODS]
        i = (keys.index(self.period) + 1) % len(keys) if self.period in keys else 0
        self.period = keys[i]
        self.cross_on = False
        self._start_load()
        self._invalidate()

    def _set_period(self, key):
        if key != self.period:
            self.period = key
            self.cross_on = False
            self._start_load()
            self._invalidate()

    def _cycle_adjust(self):
        keys = [a[0] for a in ADJUSTS]
        i = (keys.index(self.adjust) + 1) % len(keys) if self.adjust in keys else 0
        self._set_adjust(keys[i])

    def _set_adjust(self, key):
        if key != self.adjust:
            self.adjust = key
            self.cross_on = False
            self._start_load()
            self._invalidate()

    def _toggle_ind(self, key):
        self.ind[key] = not self.ind.get(key)
        self.cross_on = False
        self._invalidate()
    def _show_menu(self, hwnd):
        hmenu = user32.CreatePopupMenu()
        user32.AppendMenuW(hmenu, MF_STRING, CID_CHART_REFRESH, "刷新")
        for i, (key, label) in enumerate(PERIODS):
            mark = " ✓" if key == self.period else ""
            user32.AppendMenuW(hmenu, MF_STRING, CID_PERIOD_BASE + i, "周期：" + label + mark)
        for i, (key, label) in enumerate(ADJUSTS):
            mark = " ✓" if key == self.adjust else ""
            user32.AppendMenuW(hmenu, MF_STRING, CID_ADJ_BASE + i, "复权：" + label + mark)
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        for i, (key, label, _d) in enumerate(INDS):
            mark = " ✓" if self.ind.get(key) else ""
            user32.AppendMenuW(hmenu, MF_STRING, CID_IND_BASE + i, "指标：" + label + mark)
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, CID_CHART_THEME, "切换主题")
        user32.AppendMenuW(hmenu, MF_STRING, CID_CHART_CODE, "输入代码…")
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, CID_CHART_STYLE,
                           "外观滑块：%s" % ("收起" if self.panel_open else "展开"))
        user32.AppendMenuW(hmenu, MF_STRING, CID_CHART_STYLE_RESET, "跟随主窗口外观")
        user32.AppendMenuW(hmenu, MF_STRING, CID_CHART_CLOSE, "关闭本窗口")
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        cmd = user32.TrackPopupMenu(hmenu, TPM_RETURNCMD | TPM_LEFTBUTTON,
                                    pt.x, pt.y, 0, hwnd, None)
        user32.DestroyMenu(hmenu)
        if cmd:
            self._do_cmd(cmd)

    def _do_cmd(self, cmd):
        if cmd == CID_CHART_REFRESH:
            self._start_load()
        elif cmd == CID_CHART_PERIOD:
            self._cycle_period()
        elif cmd == CID_CHART_THEME:
            self.app._cycle_theme()
        elif cmd == CID_CHART_CODE:
            self._ask_code()
        elif cmd == CID_CHART_STYLE:
            self.panel_open = not self.panel_open
            self._invalidate()
        elif cmd == CID_CHART_STYLE_RESET:
            self.app.cfg["chart_style"] = {}
            save_config(self.app.cfg)
            self.app._cfg_mtime = self.app._config_mtime()
            self._reload_style()
        elif cmd == CID_CHART_CLOSE:
            self.close()
        elif CID_PERIOD_BASE <= cmd < CID_PERIOD_BASE + len(PERIODS):
            self._set_period(PERIODS[cmd - CID_PERIOD_BASE][0])
        elif CID_ADJ_BASE <= cmd < CID_ADJ_BASE + len(ADJUSTS):
            self._set_adjust(ADJUSTS[cmd - CID_ADJ_BASE][0])
        elif CID_IND_BASE <= cmd < CID_IND_BASE + len(INDS):
            self._toggle_ind(INDS[cmd - CID_IND_BASE][0])
    # ---- 鼠标 ----
    def _track(self):
        try:
            tme = TRACKMOUSEEVENT()
            tme.cbSize = ctypes.sizeof(TRACKMOUSEEVENT)
            tme.dwFlags = TME_LEAVE
            tme.hwndTrack = self.hwnd
            user32.TrackMouseEvent(ctypes.byref(tme))
        except Exception:
            pass

    def _on_nchittest(self, hwnd, lparam):
        pt = POINT(_lo16(lparam), _hi16(lparam))
        user32.ScreenToClient(hwnd, ctypes.byref(pt))
        x, y, w, h = pt.x, pt.y, self.width, self.height
        m = self._D(5)
        left, right = x < m, x >= w - m
        top, bottom = y < m, y >= h - m
        if left and top:
            return HTTOPLEFT
        if right and top:
            return HTTOPRIGHT
        if left and bottom:
            return HTBOTTOMLEFT
        if right and bottom:
            return HTBOTTOMRIGHT
        if top:
            return HTTOP
        if bottom:
            return HTBOTTOM
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
        if y < self._D(26):
            for _bid, (bx, by, bw, bh, _lbl) in self.btn_rects.items():
                if bx <= x <= bx + bw and by <= y <= by + bh:
                    return HTCLIENT
            return HTCAPTION
        return HTCLIENT

    def _hit_button(self, x, y):
        for bid, (bx, by, bw, bh, _lbl) in self.btn_rects.items():
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return bid
        return None

    def _do_btn(self, bid):
        if bid == "prev":
            self._step_code(-1)
        elif bid == "next":
            self._step_code(1)
        elif bid == "code":
            self._ask_code()
        elif bid == "theme":
            self.app._cycle_theme()
        elif bid == "refresh":
            self._start_load()
        elif bid == "close":
            self.close()

    def _sep_hit(self, y):
        """鼠标是否落在某条分隔条上（±5px）。返回索引或 None。"""
        tol = self._D(5)
        for i, (sy, _a, _b, _ha, _hb) in enumerate(self._splitters):
            if abs(y - sy) <= tol:
                return i
        return None

    def _sepor_cursor(self):
        if self._cursor_sizens is None:
            try:
                self._cursor_sizens = user32.LoadCursorW(
                    None, ctypes.cast(ctypes.c_void_p(32645), wintypes.LPCWSTR))
            except Exception:
                self._cursor_sizens = 0
        return self._cursor_sizens

    def _on_lbuttondown(self, lparam):
        x, y = _lo16(lparam), _hi16(lparam)
        if self.panel_open:
            key = self._slider_hit(x, y)
            if key:
                self._drag_slider = key
                try:
                    user32.SetCapture(self.hwnd)
                except Exception:
                    pass
                self._slider_set(key, x)
                return
        bid = self._hit_button(x, y)
        if bid:
            self._do_btn(bid)
            return
        sep = self._sep_hit(y)
        if sep is not None:
            sy, ka, kb, ha, hb = self._splitters[sep]
            self._drag_sep = sep
            self._drag_y0 = y
            self._drag_h0 = (ha, hb)
            ww = self._pane_weights()
            self._drag_w0 = (ww.get(ka, 1.0), ww.get(kb, 1.0))
            try:
                user32.SetCapture(self.hwnd)
            except Exception:
                pass
            return
        self._pressed = True
        self._press_xy = (x, y)
        self._long_armed = not self.cross_on
        if self._long_armed:
            try:
                user32.SetTimer(self.hwnd, CHART_TIMER_PRESS, LONG_PRESS_MS, None)
            except Exception:
                pass

    def _on_lbuttonup(self):
        if self._drag_slider:
            self._drag_slider = None
            try:
                user32.ReleaseCapture()
            except Exception:
                pass
            self._save_chart_style()
            return
        try:
            user32.KillTimer(self.hwnd, CHART_TIMER_PRESS)
        except Exception:
            pass
        if self._drag_sep is not None:
            try:
                user32.ReleaseCapture()
            except Exception:
                pass
            self._drag_sep = None
            self._save_panes()
            self._invalidate()
            return
        self._pressed = False
        if self.cross_on and not self._long_armed:
            self.cross_on = False          # 已经显示着，单击一次关掉
            self._invalidate()

    def _drag_sep_to(self, y):
        """按鼠标纵向位移调整相邻两个面板的权重（两者权重之和保持不变）。"""
        sep = self._drag_sep
        if sep is None or sep >= len(self._splitters):
            return
        _sy, ka, kb, _ha, _hb = self._splitters[sep]
        dy = y - self._drag_y0
        ha, hb = self._drag_h0[0] + dy, self._drag_h0[1] - dy
        minh = self._D(26)
        sum_h = max(1, self._drag_h0[0] + self._drag_h0[1])
        if ha < minh:
            ha = minh
            hb = sum_h - minh
        if hb < minh:
            hb = minh
            ha = sum_h - minh
        wsum = max(0.2, self._drag_w0[0] + self._drag_w0[1])
        ww = self._pane_weights()
        ww[ka] = max(0.15, wsum * ha / float(sum_h))
        ww[kb] = max(0.15, wsum * hb / float(sum_h))
        self.panes_w = ww
        self._invalidate()

    def _save_panes(self):
        """松手后把权重写进配置，下次打开/其它窗口都用这个比例。"""
        if not self.panes_w:
            return
        try:
            self.app.cfg["chart_panes"] = dict(self.panes_w)
            save_config(self.app.cfg)
            self.app._cfg_mtime = self.app._config_mtime()
        except Exception as e:
            log("save chart panes failed: %r" % e)

    def _reset_panes(self):
        self.panes_w = None
        try:
            self.app.cfg.pop("chart_panes", None)
            save_config(self.app.cfg)
        except Exception:
            pass
        self._invalidate()

    def _on_mousemove(self, lparam):
        x, y = _lo16(lparam), _hi16(lparam)
        if self._drag_slider:
            self._slider_set(self._drag_slider, x)
            return
        self._track()
        if self._drag_sep is not None:
            self._drag_sep_to(y)
            return
        if self.cross_on:
            self._set_cross(x, y)
            self._invalidate()
            return
        sep = self._sep_hit(y)
        if sep != self.hover_sep:
            self.hover_sep = sep
            self._invalidate()
        if sep is not None:
            cur = self._sepor_cursor()
            if cur:
                try:
                    user32.SetCursor(cur)
                except Exception:
                    pass
        bid = self._hit_button(x, y)
        if bid != self.hover_btn:
            self.hover_btn = bid
            self._invalidate()

    def _on_wheel(self, wparam):
        if not self.bars:
            return
        delta = _hi16(wparam)
        cur = self.zoom or min(len(self.bars), 120)
        step = max(2, cur // 8)
        cur = cur - step if delta > 0 else cur + step
        self.zoom = max(20, min(len(self.bars), cur))
        self._invalidate()

    def _on_size(self, lparam):
        self.width = max(1, _lo16(lparam))
        self.height = max(1, _hi16(lparam))
        self._invalidate()

    # ---- 消息 ----
    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_PAINT:
                self._on_paint(hwnd)
                return 0
            if msg == WM_ERASEBKGND:
                return 1
            if msg == WM_NCCALCSIZE:
                if not wparam:
                    try:
                        r = ctypes.cast(ctypes.c_void_p(lparam & 0xFFFFFFFFFFFFFFFF),
                                        ctypes.POINTER(RECT)).contents
                        wr = RECT()
                        if user32.GetWindowRect(hwnd, ctypes.byref(wr)):
                            r.left, r.top = wr.left, wr.top
                            r.right, r.bottom = wr.right, wr.bottom
                    except Exception as e:
                        log("chart nccalcsize failed: %r" % e)
                return 0
            if msg == WM_GETMINMAXINFO:
                try:
                    mmi = ctypes.cast(ctypes.c_void_p(lparam),
                                      ctypes.POINTER(MINMAXINFO)).contents
                    mmi.ptMinTrackSize.x = self._D(CHART_MIN_W)
                    mmi.ptMinTrackSize.y = self._D(CHART_MIN_H)
                except Exception:
                    pass
                return 0
            if msg == WM_NCHITTEST:
                return self._on_nchittest(hwnd, lparam)
            if msg == WM_SIZE:
                self._on_size(lparam)
                return 0
            if msg == WM_MOUSEMOVE:
                self._on_mousemove(lparam)
                return 0
            if msg == WM_MOUSELEAVE:
                if self.hover_btn is not None:
                    self.hover_btn = None
                    self._invalidate()
                return 0
            if msg == WM_LBUTTONDOWN:
                self._on_lbuttondown(lparam)
                return 0
            if msg == WM_LBUTTONDBLCLK:
                lx, ly = _lo16(lparam), _hi16(lparam)
                if self._sep_hit(ly) is not None:
                    self._reset_panes()          # 双击分隔条 = 恢复默认比例
                else:
                    self._ask_code()
                return 0
            if msg == WM_NCLBUTTONDBLCLK:
                return 0
            if msg == WM_RBUTTONUP:
                self._show_menu(hwnd)
                return 0
            if msg == WM_MOUSEWHEEL:
                self._on_wheel(wparam)
                return 0
            if msg == WM_COMMAND:
                self._do_cmd(int(wparam) & 0xFFFF)
                return 0
            if msg == WM_TIMER:
                if int(wparam) == CHART_TIMER_PRESS:
                    try:
                        user32.KillTimer(hwnd, CHART_TIMER_PRESS)
                    except Exception:
                        pass
                    if self._pressed:
                        self._set_cross(*self._press_xy)
                        self._invalidate()
                    return 0
                self._start_load()
                return 0
            if msg == WM_LBUTTONUP:
                self._on_lbuttonup()
                return 0
            if msg == CHART_WM_READY:
                self._on_ready()
                return 0
            if msg == CHART_WM_CODE:
                self._on_code_msg()
                return 0
            if msg == WM_CLOSE:
                self.close()
                return 0
            if msg == WM_DESTROY:
                self._on_destroy()
                return 0
        except BaseException:
            log("chart proc error:\n" + traceback.format_exc())
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ---- 绘制 ----
    def _pane_weights(self):
        """各面板的相对权重。用户拖过分隔条就用用户的，否则用配置里的，最后用默认。"""
        w = {"price": 2.20, "VOL": 0.85, "MACD": 1.05, "KDJ": 1.00, "RSI": 1.00}
        if isinstance(self.panes_w, dict):
            return dict(self.panes_w)
        saved = self.app.cfg.get("chart_panes")
        if isinstance(saved, dict):
            for k, v in saved.items():
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if k in w and 0.2 <= fv <= 8.0:
                    w[k] = fv
        return w

    def _panes(self):
        """价格区 + 子指标区。高度按"相对权重"分配 —— 拖分隔条改的就是这些权重，
        所以窗口再放大缩小时比例仍然保持。
        """
        w, h = self.width, self.height
        hdr = self._D(26)
        gap = self._D(6)
        axis = self._D(15)
        rw = self._D(58)
        x0 = self._D(8)
        x1 = w - rw
        top = hdr + gap
        avail = max(self._D(120), h - top - axis - gap)
        keys = [k for k, _lb, _d in INDS
                if k in ("VOL", "MACD", "KDJ", "RSI") and self.ind.get(k)]
        subs = []
        self._splitters = []
        if keys:
            ww = self._pane_weights()
            room = avail - gap * len(keys)
            total = ww["price"] + sum(ww[k] for k in keys)
            heights = {k: max(self._D(26), int(room * ww[k] / float(total))) for k in keys}
            pri_h = room - sum(heights.values())
            if pri_h < self._D(70):                    # 价格区不够 → 等比压子面板
                scale = max(0.25, (room - self._D(70)) / float(max(1, sum(heights.values()))))
                heights = {k: max(self._D(22), int(heights[k] * scale)) for k in keys}
                pri_h = room - sum(heights.values())
            pri_h = max(self._D(50), pri_h)
            prect = (x0, top, max(10, x1 - x0), pri_h)
            y = top + pri_h
            prev_key, prev_h = "price", pri_h
            for k in keys:
                self._splitters.append((y + gap // 2, prev_key, k, prev_h, heights[k]))
                subs.append((k, (x0, y + gap, max(10, x1 - x0), heights[k])))
                y += gap + heights[k]
                prev_key, prev_h = k, heights[k]
        else:
            prect = (x0, top, max(10, x1 - x0), avail)
        self._prect = prect
        self._subs = subs
        return hdr, gap, axis, rw, x1, prect, subs

    def _on_paint(self, hwnd):
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            w, h = max(1, self.width), max(1, self.height)
            memdc = gdi32.CreateCompatibleDC(hdc)
            bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
            old = gdi32.SelectObject(memdc, bmp)
            try:
                self._draw(memdc, w, h)
                gdi32.BitBlt(hdc, 0, 0, w, h, memdc, 0, 0, SRCCOPY)
            finally:
                gdi32.SelectObject(memdc, old)
                gdi32.DeleteObject(bmp)
                gdi32.DeleteDC(memdc)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))

    def _style_ov(self):
        # 主窗口没有 self.app，此时返回 {} 表示"不覆盖"
        app = getattr(self, "app", None)
        if app is None:
            return {}
        ov = getattr(app, "cfg", {}).get("chart_style")
        return ov if isinstance(ov, dict) else {}

    def _mkfont(self, pt, bold=False):
        """图表字体：字号/字体名可被「图表外观」单独覆盖。"""
        app = getattr(self, "app", None)
        ov = (getattr(app, "cfg", {}) or {}).get("chart_style") if app else None
        if not isinstance(ov, dict):
            ov = {}
        try:
            mul = float(ov.get("font_scale") or 1.0)
        except (TypeError, ValueError):
            mul = 1.0
        fam = str(ov.get("font") or "").strip() or FONT_NAME
        h = -int(round(pt * self.font_scale * mul * self.ui_scale * self.dpi / 72.0))
        quality = 4 if self.transparent else 5
        return gdi32.CreateFontW(h, 0, 0, 0, 700 if bold else 400, 0, 0, 0, 1, 4, 0, quality, 0, fam)

    def _apply_bg_alpha(self):
        """已停用（2026-10-21）：图表窗口本身走逐像素 alpha（UpdateLayeredWindow），
        再叠 SetLayeredWindowAttributes(LWA_ALPHA) 会触发"两条路互斥"，
        窗口直接变成全透明 → 用户看到的是"K 线图彻底不见了"。

        背景透明度仍然有效 —— 由 _bg_pct() 在逐像素合成时按比例缩放背景 alpha。
        """
        return

    # ---------------- 外观滑块面板（与主窗口一致的操作方式） ----------------
    def _chart_sliders(self):
        # 只保留一条：透明度（字体）。背景那条落在标题栏拖动区里没法拖，已移除。
        return (("font_opacity", "透明度", 10.0, 100.0),)

    def _sv(self, key):
        """滑块当前值：图表有覆盖就用覆盖值，否则回退主窗口的值。"""
        ov = self._style_ov()
        if key == "bg_opacity":
            return float(self._bg_pct())
        if key == "font_opacity":
            v = ov.get("font_opacity")
            return float(v) if v not in (None, "") else float(self.app.font_opacity)
        v = ov.get("font_scale")
        return float(v) * 100.0 if v not in (None, "") else 100.0

    def _panel_rect(self):
        return (0, 0, self.width, self._D(74))

    def _slider_rows(self):
        """每行 (key, 标签, lo, hi, x0, x1, y)。★ y 从标题栏下方开始，不能盖住拖动区。"""
        w = self.width
        x0 = self._D(58)
        x1 = w - self._D(52)
        base = self._D(26) + self._D(6)
        return [(k, lb, lo, hi, x0, x1, base + i * self._D(20))
                for i, (k, lb, lo, hi) in enumerate(self._chart_sliders())]

    def _slider_hit(self, x, y):
        if not self.panel_open:
            return None
        for key, _lb, _lo, _hi, x0, x1, yy in self._slider_rows():
            if yy - self._D(6) <= y <= yy + self._D(14) and x >= x0 - self._D(24):
                return key
        return None

    def _slider_set(self, key, x):
        for k, _lb, lo, hi, x0, x1, _yy in self._slider_rows():
            if k != key:
                continue
            frac = 0.0 if x1 <= x0 else max(0.0, min(1.0, (x - x0) / float(x1 - x0)))
            v = lo + (hi - lo) * frac
            ov = dict(self._style_ov())
            if key == "font_scale":
                ov["font_scale"] = round(v / 100.0, 2)
            else:
                ov[key] = int(round(v))
            self.app.cfg["chart_style"] = ov
            self._invalidate()
            return

    def _save_chart_style(self):
        try:
            save_config(self.app.cfg)
            self.app._cfg_mtime = self.app._config_mtime()
        except Exception as e:
            log("save chart style failed: %r" % e)
        self._reload_style()          # 字号变了要重建字体

    def _draw_panel(self, hdc, t):
        """标题栏下方一条细面板（单条滑块，高 22px）。"""
        top = self._D(26)
        ph = self._D(22)
        self._fill(hdc, 0, top, self.width, ph, t["header"])
        self._fill(hdc, 0, top + ph - 1, self.width, 1, t["border"])
        for key, label, lo, hi, x0, x1, yy in self._slider_rows():
            v = self._sv(key)
            self._text(hdc, self._D(6), yy - self._D(6), self._D(52), self._D(18), label,
                       self.f_axis, t["header_text"], DT_LEFT | DT_VCENTER | DT_SINGLELINE)
            self._fill(hdc, x0, yy + self._D(2), max(1, x1 - x0), self._D(2), t["border"])
            frac = 0.0 if hi <= lo else max(0.0, min(1.0, (v - lo) / (hi - lo)))
            kx = x0 + int((x1 - x0) * frac)
            self._fill(hdc, x0, yy + self._D(2), max(1, kx - x0), self._D(2), t["up"])
            self._fill(hdc, kx - self._D(3), yy, self._D(7), self._D(7), t["up"])
            self._text(hdc, x1 + self._D(4), yy - self._D(6), self._D(44), self._D(18),
                       "%d%%" % int(round(v)), self.f_axis, t["sub"],
                       DT_RIGHT | DT_VCENTER | DT_SINGLELINE)

    def _bg_pct(self):
        """图表背景不透明度：优先用「图表外观」里的值，否则跟随主窗口。"""
        ov = self._style_ov()
        bo = ov.get("bg_opacity")
        if bo:
            try:
                return max(10, min(100, int(float(bo))))
            except (TypeError, ValueError):
                pass
        return max(10, min(100, int(self.app.cfg.get("bg_opacity", 100) or 100)))

    def _palette(self):
        """图表配色：字体透明度可被「图表外观」覆盖；没设就跟随主窗口。"""
        t = dict(self.app.colors)
        ov = self._style_ov()
        fo = ov.get("font_opacity")
        if fo is None:
            return self.app._faded(t)
        try:
            f = max(0.0, min(1.0, float(fo) / 100.0))
        except (TypeError, ValueError):
            return self.app._faded(t)
        if f >= 0.999:
            return t

        def mix(c, bg):
            cb, cg, cr = (c >> 16) & 255, (c >> 8) & 255, c & 255
            bb, bgc, br = (bg >> 16) & 255, (bg >> 8) & 255, bg & 255
            fn = lambda a, b: int(round(b + (a - b) * f))
            return (fn(cb, bb) << 16) | (fn(cg, bgc) << 8) | fn(cr, br)

        t["header_text"] = mix(t["header_text"], t["header"])
        t["header_sub"] = mix(t["header_sub"], t["header"])
        for k in ("text", "sub", "up", "down"):
            t[k] = mix(t[k], t["panel"])
        return t
    def _reload_style(self):
        """外观改动后重建字体（字号/字体名变了必须重建）。"""
        # 背景透明度也跟着图表外观走（没设就退回主窗口的值）
        _ov = self._style_ov()
        _bo = _ov.get("bg_opacity")
        try:
            self.bg_opacity = max(10, min(100, int(float(_bo) if _bo else
                                                  self.app.cfg.get("bg_opacity", 100))))
        except (TypeError, ValueError):
            pass
        for f in (self.f_title, self.f_body, self.f_price, self.f_small):
            try:
                if f:
                    gdi32.DeleteObject(f)
            except Exception:
                pass
        self.f_title = self._mkfont(9, bold=True)
        self.f_body = self._mkfont(9)
        self.f_price = self._mkfont(9, bold=True)
        self.f_small = self._mkfont(8)
        self._apply_bg_alpha()
        self._invalidate()
    def _set_chart_style(self):
        ov = self._style_ov()
        rows = [("字号 %", "%g" % (float(ov.get("font_scale") or 1.0) * 100)),
                ("字体透明度 %", "" if ov.get("font_opacity") is None else "%g" % float(ov["font_opacity"])),
                ("字体名", ov.get("font") or ""),
                ("背景透明度 %", "" if ov.get("bg_opacity") is None else "%g" % float(ov["bg_opacity"]))]
        slider_probe = True
        vals = None if slider_probe else ps_field_form("K 线图外观", rows, "留空 = 跟随主窗口")
        if vals is None:
            return
        nf = dict(ov)
        if vals[0].strip():
            try:
                nf["font_scale"] = max(0.5, min(3.0, float(vals[0]) / 100.0))
            except ValueError:
                pass
        else:
            nf["font_scale"] = 1.0
        if vals[1].strip():
            try:
                nf["font_opacity"] = max(0.0, min(100.0, float(vals[1])))
            except ValueError:
                pass
        else:
            nf["font_opacity"] = None
        nf["font"] = vals[2].strip()
        if vals[3].strip():
            try:
                nf["bg_opacity"] = max(10.0, min(100.0, float(vals[3])))
            except ValueError:
                pass
        else:
            nf["bg_opacity"] = None
        self.app.cfg["chart_style"] = nf
        save_config(self.app.cfg)
        self.app._cfg_mtime = self.app._config_mtime()
        self.app._apply_chart_style()
    def _fill(self, hdc, x, y, w, h, color):
        r = RECT(int(x), int(y), int(x + w), int(y + h))
        br = gdi32.CreateSolidBrush(color)
        user32.FillRect(hdc, ctypes.byref(r), br)
        gdi32.DeleteObject(br)

    def _text(self, hdc, x, y, w, h, s, font, color, fmt):
        old = gdi32.SelectObject(hdc, font)
        gdi32.SetBkMode(hdc, TRANSPARENT)
        r = RECT(int(x), int(y), int(x + w), int(y + h))
        gdi32.SetTextColor(hdc, color)
        user32.DrawTextW(hdc, s, -1, ctypes.byref(r), fmt)
        gdi32.SelectObject(hdc, old)

    def _rect_outline(self, hdc, x, y, w, h, color, bg):
        """空心矩形（隐蔽模式下"下跌"的 K 线实体要用它：底色挖空 + 四边描线）。"""
        w, h = max(2, int(w)), max(2, int(h))
        x, y = int(x), int(y)
        self._fill(hdc, x, y, w, h, bg)
        self._fill(hdc, x, y, w, 1, color)
        self._fill(hdc, x, y + h - 1, w, 1, color)
        self._fill(hdc, x, y, 1, h, color)
        self._fill(hdc, x + w - 1, y, 1, h, color)

    def _col_updown(self, t):
        """涨跌配色。返回 (涨色, 跌色, 跌是否实心)。

        隐蔽模式（默认）：上涨 = 实心浅灰(sub)，下跌 = 空心黑(text) —— 与主窗口
        "上涨走 sub、下跌走 text"的规则保持一致，一眼看不出红绿。
        正常模式：红涨绿跌，全部实心。
        """
        if self.app.pct_mode == PCT_STEALTH:
            return t["sub"], t["text"], False
        return t["up"], t["down"], True

    def _poly(self, hdc, pts, color, width=1):
        if len(pts) < 2:
            return
        arr = (POINT * len(pts))()
        for i, (a, b) in enumerate(pts):
            arr[i].x, arr[i].y = a, b
        pen = gdi32.CreatePen(0, max(1, self._D(width)), color)
        old = gdi32.SelectObject(hdc, pen)
        gdi32.Polyline(hdc, arr, len(pts))
        gdi32.SelectObject(hdc, old)
        gdi32.DeleteObject(pen)

    def _draw_overlay(self, hdc, prect, slot, lo_i, n, t, yprice):
        """价格区叠加：MA / BOLL。"""
        px, py, pw, ph = prect
        series = []
        if self.ind.get("MA"):
            for nn, col in ((5, t["up"]), (10, t["sub"]), (20, t["text"]), (60, t["border"])):
                arr = self.ma.get(nn)
                if arr:
                    series.append((arr, col))
        if self.ind.get("BOLL"):
            mid, up, low = self.boll
            for arr, col in ((up, t["up"]), (mid, t["text"]), (low, t["down"])):
                if arr:
                    series.append((arr, col))
        for arr, col in series:
            pts = [(px + int(i * slot + slot / 2), yprice(v))
                   for i, v in enumerate(arr[lo_i:lo_i + n]) if v is not None]
            self._poly(hdc, pts, col)

    def _draw_sub(self, hdc, key, rect, slot, lo_i, n, t, bars, c_up, c_dn):
        """子指标区：VOL / MACD / KDJ / RSI。"""
        x, y, w, h = rect
        self._fill(hdc, x, y - 1, w, 1, t["border"])
        bsize = max(1, int(slot * 0.62))
        if key == "VOL":
            vmax = max((b["vol"] for b in bars), default=0.0) or 1.0
            for i, b in enumerate(bars):
                cx = x + int(i * slot + slot / 2)
                hgt = max(1, int(b["vol"] / vmax * (h - 3)))
                col = c_up if b["close"] >= b["open"] else c_dn
                self._fill(hdc, cx - bsize // 2, y + h - hgt, bsize, hgt,
                           _mix_color(col, t["panel"], 0.50))
            for nn, col in ((5, t["text"]), (10, t["sub"])):
                arr = self.vol_ma.get(nn)
                if not arr:
                    continue
                pts = [(x + int(i * slot + slot / 2), y + h - int(v / vmax * (h - 2)))
                       for i, v in enumerate(arr[lo_i:lo_i + n]) if v is not None]
                self._poly(hdc, pts, col)
            label = "VOL " + _fmt_vol(vmax)
        elif key == "MACD":
            mvals = [abs(v) for v in (self.hist[lo_i:lo_i + n] + self.dif[lo_i:lo_i + n]
                                      + self.dea[lo_i:lo_i + n])]
            mmax = max(mvals or [0.0]) or 1.0
            zero = y + h // 2
            self._fill(hdc, x, zero, w, 1, t["border"])
            for i, v in enumerate(self.hist[lo_i:lo_i + n]):
                cx = x + int(i * slot + slot / 2)
                y2 = zero - int(v / mmax * (h / 2 - 2))
                col = c_up if v >= 0 else c_dn
                self._fill(hdc, cx - bsize // 2, min(zero, y2), bsize,
                           max(1, abs(y2 - zero)), _mix_color(col, t["panel"], 0.40))
            for arr, col in ((self.dif, t["text"]), (self.dea, t["sub"])):
                pts = [(x + int(i * slot + slot / 2), zero - int(v / mmax * (h / 2 - 2)))
                       for i, v in enumerate(arr[lo_i:lo_i + n])]
                self._poly(hdc, pts, col)
            label = "MACD(12,26,9)"
        elif key == "KDJ":
            k_l, d_l, j_l = self.kdj
            lo_v = min([min(a[lo_i:lo_i + n]) for a in (k_l, d_l, j_l)])
            hi_v = max([max(a[lo_i:lo_i + n]) for a in (k_l, d_l, j_l)])
            if hi_v - lo_v < 1e-9:
                hi_v = lo_v + 1
            def yk(v):
                return y + h - int((v - lo_v) / (hi_v - lo_v) * (h - 2)) - 1
            for gv in (20, 80):
                if lo_v <= gv <= hi_v:
                    self._fill(hdc, x, yk(gv), w, 1, t["border"])
            for arr, col in ((k_l, t["text"]), (d_l, t["sub"]), (j_l, t["up"])):
                pts = [(x + int(i * slot + slot / 2), yk(v))
                       for i, v in enumerate(arr[lo_i:lo_i + n])]
                self._poly(hdc, pts, col)
            label = "KDJ(9,3,3)  K/D/J"
        else:                                   # RSI
            lo_v, hi_v = 0.0, 100.0
            def yr(v):
                return y + h - int(v / 100.0 * (h - 2)) - 1
            for gv in (30, 70):
                self._fill(hdc, x, yr(gv), w, 1, t["border"])
            for nn, col in ((6, t["text"]), (12, t["sub"])):
                arr = self.rsi.get(nn) or []
                pts = [(x + int(i * slot + slot / 2), yr(v))
                       for i, v in enumerate(arr[lo_i:lo_i + n]) if v is not None]
                self._poly(hdc, pts, col)
            label = "RSI(6/12)"
        self._text(hdc, x + self._D(2), y, w - self._D(4), self._D(14), label,
                   self.f_axis, t["sub"], DT_LEFT | DT_VCENTER | DT_SINGLELINE)

    def _draw(self, hdc, w, h):
        self._apply_bg_alpha()
        t = self._palette()
        c_up, c_dn, dn_filled = self._col_updown(t)
        hdr, gap, axis, rw, x1, prect, subs = self._panes()
        self._fill(hdc, 0, 0, w, h, t["panel"])
        self._fill(hdc, 0, 0, w, hdr, t["header"])

        btns = (("close", "×"), ("refresh", "刷"), ("theme", "色"),
                ("code", "代"), ("next", "›"), ("prev", "‹"))
        bw, bh, gapb = self._D(18), self._D(15), self._D(2)
        bx = w - self._D(6) - bw
        self.btn_rects = {}
        for bid, lbl in btns:
            self.btn_rects[bid] = (bx, self._D(5), bw, bh, lbl)
            bx -= (bw + gapb)
        btn_left = bx + bw + gapb

        pname = dict(PERIODS).get(self.period, "")
        adj = "" if self.period in ("min", "min5") else dict(ADJUSTS).get(self.adjust, "")
        title = ("%s  %s  %s %s" % (self.name, self.code, pname, adj)).strip() if self.name \
            else ("%s  %s %s" % (self.code, pname, adj)).strip()
        self._text(hdc, self._D(8), 0, max(self._D(40), btn_left - self._D(10)), hdr,
                   title, self.f_title, t["header_text"],
                   DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS)
        for bid, (bx2, by2, bw2, bh2, lbl) in self.btn_rects.items():
            bgc = self.app._hover_color(t["header"]) if self.hover_btn == bid else t["header"]
            self._fill(hdc, bx2, by2, bw2, bh2, bgc)
            self._text(hdc, bx2, by2, bw2, bh2, lbl, self.f_axis, t["header_sub"],
                       DT_CENTER | DT_VCENTER | DT_SINGLELINE)

        if self.period in ("min", "min5"):
            self._draw_minute(hdc, w, h, t, hdr, gap, axis, rw, x1)
            if self.panel_open:
                self._draw_panel(hdc, t)
            return
        if not self.bars:
            msg = self.error or ("加载中…" if self.loading else "没有数据")
            self._text(hdc, 0, hdr, w, max(1, h - hdr), msg, self.f_title, t["sub"],
                       DT_CENTER | DT_VCENTER | DT_SINGLELINE)
            return

        px, py, pw, ph = prect
        max_bars = max(20, int(pw / max(1, self._D(4))))
        n = self.zoom or min(len(self.bars), max_bars)
        n = max(20, min(n, len(self.bars)))
        bars = self.bars[-n:]
        lo_i = len(self.bars) - n
        slot = pw / float(n)
        body = max(1, int(slot * 0.62))
        self._vis_n = n

        hi = max(b["high"] for b in bars)
        lo = min(b["low"] for b in bars)
        extra = []
        if self.ind.get("MA"):
            for arr in self.ma.values():
                extra.extend(v for v in arr[lo_i:] if v is not None)
        if self.ind.get("BOLL"):
            for arr in self.boll:
                if arr:
                    extra.extend(v for v in arr[lo_i:] if v is not None)
        if extra:
            hi = max(hi, max(extra))
            lo = min(lo, min(extra))
        pad = (hi - lo) * 0.05 or max(0.01, abs(hi) * 0.01)
        hi += pad
        lo -= pad
        self._range = (lo, hi)

        def yprice(v):
            return py + ph - int((v - lo) / (hi - lo) * ph)

        for frac in (0.0, 0.5, 1.0):
            v = lo + (hi - lo) * frac
            yy = yprice(v)
            self._fill(hdc, px, yy, pw, 1, t["border"])
            self._text(hdc, px + pw + self._D(4), yy - self._D(7), rw - self._D(5), self._D(14),
                       "%.2f" % v, self.f_axis, t["sub"], DT_LEFT | DT_VCENTER | DT_SINGLELINE)

        for i, b in enumerate(bars):
            cx = px + int(i * slot + slot / 2)
            rising = b["close"] >= b["open"]
            col = c_up if rising else c_dn
            yh, yl = yprice(b["high"]), yprice(b["low"])
            yo, yc = yprice(b["open"]), yprice(b["close"])
            self._fill(hdc, cx, min(yh, yl), 1, max(1, abs(yl - yh)), col)
            ty = min(yo, yc)
            bh2 = max(1, abs(yc - yo))
            if rising or dn_filled:
                self._fill(hdc, cx - body // 2, ty, body, bh2, col)
            else:
                self._rect_outline(hdc, cx - body // 2, ty, body, bh2, col, t["panel"])

        self._draw_overlay(hdc, prect, slot, lo_i, n, t, yprice)

        if len(self.bars) >= 2:
            last, prev = self.bars[-1]["close"], self.bars[-2]["close"]
            pct = (last - prev) / prev * 100.0 if prev else 0.0
            col = c_up if last >= prev else c_dn
            self._text(hdc, px + self._D(2), py + self._D(1), pw - self._D(4), self._D(14),
                       "%.2f  %+.2f%%" % (last, pct), self.f_axis, col,
                       DT_LEFT | DT_VCENTER | DT_SINGLELINE)

        for key, rect in subs:
            self._draw_sub(hdc, key, rect, slot, lo_i, n, t, bars, c_up, c_dn)

        ay = (subs[-1][1][1] + subs[-1][1][3] + gap) if subs else (py + ph + gap)
        self._text(hdc, px, ay, pw // 2, axis, bars[0]["date"], self.f_axis, t["sub"],
                   DT_LEFT | DT_VCENTER | DT_SINGLELINE)
        self._text(hdc, px + pw // 2, ay, pw // 2, axis, bars[-1]["date"], self.f_axis,
                   t["sub"], DT_RIGHT | DT_VCENTER | DT_SINGLELINE)

        idx = self._drag_sep if self._drag_sep is not None else self.hover_sep
        if idx is not None and 0 <= idx < len(self._splitters):
            sy = self._splitters[idx][0]
            self._fill(hdc, px, sy - 1, pw, 3, t["up"] if self._drag_sep is not None else t["sub"])

        if self.panel_open:
            self._draw_panel(hdc, t)
        if self.cross_on:
            self._draw_cross(hdc, w, h, t, prect, subs, slot, bars, lo_i)

    def _set_cross(self, x, y):
        """十字标线定位。K 线按"第几根"，分时按"第几个分钟点"。"""
        if self.period in ("min", "min5"):
            g = getattr(self, "_min_hit", None)
            if not g:
                return
            px, py, pw, ph = g["rect"]
            slot, n = g["slot"], g["n"]
            if n <= 0 or pw <= 0 or slot <= 0:
                return
            self.cross_i = max(0, min(n - 1, int((x - px) / slot)))
            self.cross_y = max(py + 1, min(py + ph - 1, y))
            self.cross_on = True
            return
        prect = self._prect
        n = self._vis_n
        if not prect or n <= 0:
            return
        px, py, pw, ph = prect
        slot = pw / float(n)
        self.cross_i = max(0, min(n - 1, int((x - px) / slot)))
        self.cross_y = max(py + 1, min(py + ph - 1, y))
        self.cross_on = True

    def _draw_cross(self, hdc, w, h, t, prect, subs, slot, bars, lo_i):
        """十字标线：竖线 + 横线 + 右轴价格 + 底部日期 + 左上角数据框。"""
        px, py, pw, ph = prect
        cx = px + int(self.cross_i * slot + slot / 2)
        cy = self.cross_y
        bottom = (subs[-1][1][1] + subs[-1][1][3]) if subs else (py + ph)
        pen = gdi32.CreatePen(1, 1, t["text"])       # PS_DASH，宽度只能是 1
        oldp = gdi32.SelectObject(hdc, pen)
        arr = (POINT * 2)()
        arr[0].x, arr[0].y = cx, py
        arr[1].x, arr[1].y = cx, bottom
        gdi32.Polyline(hdc, arr, 2)
        arr[0].x, arr[0].y = px, cy
        arr[1].x, arr[1].y = px + pw, cy
        gdi32.Polyline(hdc, arr, 2)
        gdi32.SelectObject(hdc, oldp)
        gdi32.DeleteObject(pen)

        lo_v, hi_v = self._range
        price = lo_v + (py + ph - cy) / float(max(1, ph)) * (hi_v - lo_v)
        bwid = self._D(56)
        self._fill(hdc, px + pw + self._D(1), cy - self._D(8), bwid, self._D(16), t["header"])
        self._text(hdc, px + pw + self._D(1), cy - self._D(8), bwid, self._D(16),
                   "%.2f" % price, self.f_axis, t["header_text"],
                   DT_CENTER | DT_VCENTER | DT_SINGLELINE)

        gi = lo_i + self.cross_i
        b = self.bars[gi] if 0 <= gi < len(self.bars) else None
        if not b:
            return
        self._fill(hdc, cx - self._D(32), bottom + self._D(1), self._D(64), self._D(14),
                   t["header"])
        self._text(hdc, cx - self._D(32), bottom + self._D(1), self._D(64), self._D(14),
                   b["date"], self.f_axis, t["header_text"],
                   DT_CENTER | DT_VCENTER | DT_SINGLELINE)

        prev = self.bars[gi - 1]["close"] if gi > 0 else b["open"]
        pct = (b["close"] - prev) / prev * 100.0 if prev else 0.0
        lines = ("%s   开 %.2f  高 %.2f" % (b["date"], b["open"], b["high"]),
                 "低 %.2f  收 %.2f   %+.2f%%" % (b["low"], b["close"], pct),
                 "量 %s" % _fmt_vol(b["vol"]))
        bw2 = self._D(196)
        bh2 = self._D(15) * len(lines) + self._D(6)
        self._fill(hdc, px + self._D(2), py + self._D(16), bw2, bh2, t["header"])
        for k, ln in enumerate(lines):
            self._text(hdc, px + self._D(6), py + self._D(19) + k * self._D(15),
                       bw2 - self._D(8), self._D(15), ln, self.f_axis,
                       t["header_text"], DT_LEFT | DT_VCENTER | DT_SINGLELINE)
    def _draw_minute(self, hdc, w, h, t, hdr, gap, axis, rw, x1):
        """分时图（单日 / 五日）：价格线 + 均价线 + 成交量柱，不画 MACD。"""
        mins = self.minutes
        self._min_hit = None
        if not mins:
            msg = self.error or ("加载中…" if self.loading else "没有分时数据")
            self._text(hdc, 0, hdr, w, max(1, h - hdr), msg, self.f_title, t["sub"],
                       DT_CENTER | DT_VCENTER | DT_SINGLELINE)
            return
        c_up, c_dn, _dnf = self._col_updown(t)
        pre = self.pre_close
        mdays = len({m.get("date", "") for m in mins}) > 1
        x0 = self._D(8)
        top = hdr + gap
        rw2 = self._D(78)
        avail = max(self._D(120), h - top - axis - gap)
        vol_h = int(avail * 0.24)
        pri_h = avail - vol_h - gap
        px, py = x0, top
        pw = max(10, (w - rw2) - x0)
        vx, vy, vw, vh = x0, top + pri_h + gap, pw, vol_h

        vals = [m["price"] for m in mins if m["price"] is not None]
        if not vals:
            return
        hi, lo = max(vals), min(vals)
        if pre and not mdays:
            dev = max(abs(hi - pre), abs(lo - pre)) or (pre * 0.005)
            hi, lo = pre + dev, pre - dev
        else:
            pad = (hi - lo) * 0.05 or 1
            hi, lo = hi + pad, lo - pad
        span = (hi - lo) or 1.0

        def yp(v):
            return py + pri_h - int((v - lo) / span * pri_h)

        n = len(mins)
        slot = pw / float(max(1, n))
        bar_w = max(1, int(slot * 0.7))
        self._min_hit = {"rect": (px, py, pw, pri_h), "vol": (vx, vy, vw, vh),
                         "slot": slot, "n": n, "lo": lo, "hi": hi}
        self._splitters = []              # 分时没有可拖的面板分隔条

        for frac in (0.0, 0.5, 1.0):
            v = lo + span * frac
            yy = yp(v)
            self._fill(hdc, px, yy, pw, 1, t["border"])
            lab = "%.2f" % v
            if pre:
                lab += " %+.2f%%" % ((v - pre) / pre * 100.0)
            self._text(hdc, px + pw + self._D(4), yy - self._D(7), rw2 - self._D(4),
                       self._D(14), lab, self.f_axis, t["sub"],
                       DT_LEFT | DT_VCENTER | DT_SINGLELINE)

        vmax = max([(m["vol"] or 0.0) for m in mins] or [0.0]) or 1.0
        for i, m in enumerate(mins):
            cx = px + int(i * slot + slot / 2)
            hgt = max(1, int((m["vol"] or 0.0) / vmax * (vh - 3)))
            if pre and not mdays:
                base = pre
            elif i and mins[i - 1]["price"] is not None:
                base = mins[i - 1]["price"]      # 五日：按"相对上一分钟"着色，否则全是涨色
            else:
                base = m["price"] or 0.0
            col = c_up if (m["price"] or 0.0) >= base else c_dn
            self._fill(hdc, cx - bar_w // 2, vy + vh - hgt, bar_w, hgt,
                       _mix_color(col, t["panel"], 0.50))
        self._fill(hdc, vx, vy + vh - 1, vw, 1, t["border"])
        self._text(hdc, vx + vw + self._D(4), vy, rw2 - self._D(4), vh,
                   _fmt_vol(vmax), self.f_axis, t["sub"], DT_LEFT | DT_VCENTER | DT_SINGLELINE)

        gdi32.IntersectClipRect(hdc, px, py, px + pw, py + pri_h)
        for key, col in (("avg", t["sub"]), ("price", t["text"])):
            pts = [(px + int(i * slot + slot / 2), yp(m[key]))
                   for i, m in enumerate(mins) if m.get(key) is not None]
            self._poly(hdc, pts, col)
        gdi32.SelectClipRgn(hdc, None)

        if self.hover_sep is not None or self._drag_sep is not None:
            self._fill(hdc, px, vy - gap // 2 - 1, pw, 3, t["sub"])

        ay = vy + vh + gap
        if mdays:
            seen, marks = set(), []
            for i, m in enumerate(mins):
                d = m.get("date") or ""
                if d and d not in seen:
                    seen.add(d)
                    marks.append((i, d[5:]))
            for i, (idx, lab) in enumerate(marks):
                cx = px + int(idx * slot)
                if idx:
                    self._fill(hdc, cx, py, 1, pri_h + gap + vh, t["border"])
                self._text(hdc, cx + self._D(2), ay, pw // 5, axis, lab, self.f_axis,
                           t["sub"], DT_LEFT | DT_VCENTER | DT_SINGLELINE)
        else:
            self._text(hdc, px, ay, pw // 3, axis, "09:30", self.f_axis, t["sub"],
                       DT_LEFT | DT_VCENTER | DT_SINGLELINE)
            self._text(hdc, px + pw // 3, ay, pw // 3, axis, "11:30/13:00", self.f_axis,
                       t["sub"], DT_CENTER | DT_VCENTER | DT_SINGLELINE)
            self._text(hdc, px + pw * 2 // 3, ay, pw // 3, axis, "15:00", self.f_axis,
                       t["sub"], DT_RIGHT | DT_VCENTER | DT_SINGLELINE)

        last = mins[-1]["price"] or 0.0
        if pre:
            pct = (last - pre) / pre * 100.0
            col = c_up if last >= pre else c_dn
            self._text(hdc, px + self._D(2), py + self._D(1), pw - self._D(4), self._D(14),
                       "%.2f  %+.2f%%" % (last, pct), self.f_axis, col,
                       DT_LEFT | DT_VCENTER | DT_SINGLELINE)
        if self.cross_on:
            self._draw_cross_min(hdc, t)
    def _draw_cross_min(self, hdc, t):
        """分时十字标线：竖线+横线、右轴价格、底部时间、左上数据框（价/涨跌幅/均价/量）。"""
        g = self._min_hit
        if not g or self.cross_i is None or self.cross_y is None:
            return
        px, py, pw, ph = g["rect"]
        vx, vy, vw, vh = g["vol"]
        slot, lo, hi = g["slot"], g["lo"], g["hi"]
        cx = px + int(self.cross_i * slot + slot / 2)
        cy = self.cross_y

        pen = gdi32.CreatePen(1, 1, t["text"])       # PS_DASH，宽度只能 1
        oldp = gdi32.SelectObject(hdc, pen)
        arr = (POINT * 2)()
        arr[0].x, arr[0].y = cx, py
        arr[1].x, arr[1].y = cx, vy + vh
        gdi32.Polyline(hdc, arr, 2)
        arr[0].x, arr[0].y = px, cy
        arr[1].x, arr[1].y = px + pw, cy
        gdi32.Polyline(hdc, arr, 2)
        gdi32.SelectObject(hdc, oldp)
        gdi32.DeleteObject(pen)

        rw2 = self._D(92)
        price = lo + (py + ph - cy) / float(max(1, ph)) * (hi - lo)
        self._fill(hdc, px + pw + self._D(1), cy - self._D(8), rw2 - self._D(2), self._D(16),
                   t["header"])
        self._text(hdc, px + pw + self._D(1), cy - self._D(8), rw2 - self._D(2), self._D(16),
                   "%.2f" % price, self.f_axis, t["header_text"],
                   DT_CENTER | DT_VCENTER | DT_SINGLELINE)

        mins = self.minutes
        if not (0 <= self.cross_i < len(mins)):
            return
        m = mins[self.cross_i]
        pre = self.pre_close
        label = m.get("time") or ""
        if len(label) == 4 and label.isdigit():      # 1335 → 13:35
            label = "%s:%s" % (label[:2], label[2:])
        if m.get("date"):
            label = (m["date"] + " " + label).strip()

        bw = self._D(86)
        self._fill(hdc, cx - bw // 2, vy + vh + self._D(1), bw, self._D(14), t["header"])
        self._text(hdc, cx - bw // 2, vy + vh + self._D(1), bw, self._D(14), label,
                   self.f_axis, t["header_text"], DT_CENTER | DT_VCENTER | DT_SINGLELINE)

        lines = [label]
        if m.get("price") is not None:
            txt = "价 %.2f" % m["price"]
            if pre:
                txt += "   %+.2f%%" % ((m["price"] - pre) / pre * 100.0)
            lines.append(txt)
        if m.get("avg") is not None:
            lines.append("均 %.2f" % m["avg"])
        lines.append("量 %s" % _fmt_vol(m.get("vol") or 0))
        bw2 = self._D(158)
        bh2 = self._D(15) * len(lines) + self._D(6)
        self._fill(hdc, px + self._D(2), py + self._D(16), bw2, bh2, t["header"])
        for k, ln in enumerate(lines):
            self._text(hdc, px + self._D(6), py + self._D(19) + k * self._D(15),
                       bw2 - self._D(8), self._D(15), ln, self.f_axis,
                       t["header_text"], DT_LEFT | DT_VCENTER | DT_SINGLELINE)

    def _on_destroy(self):
        try:
            user32.KillTimer(self.hwnd, 1)
        except Exception:
            pass
        try:
            if self in _charts:
                _charts.remove(self)
        except Exception:
            pass
        for f in (self.f_title, self.f_axis):
            try:
                if f:
                    gdi32.DeleteObject(f)
            except Exception:
                pass
        self.f_title = self.f_axis = None
        self.hwnd = None


# --------------------------------------------------------------------------
# 主程序
# --------------------------------------------------------------------------
class FOMO:
    def __init__(self, cfg):
        self.cfg = cfg
        self.hwnd = None
        self._proc = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._fetching = False
        self.quotes = []
        self.theme_name = cfg.get("theme", "graywhite")
        self.colors = self._theme_colors(self.theme_name)
        self.name_style = cfg.get("name_style", "pinyin")
        self.always_on_top = bool(cfg.get("always_on_top", True))
        self.collapsed = bool((cfg.get("window") or {}).get("collapsed", False))
        self.visible = not cfg.get("start_hidden", False)
        self.watchlist = cfg["watchlist"]
        self.font_scale = float(cfg.get("font_scale", 1.0) or 1.0)
        self.bg_opacity = int(cfg.get("bg_opacity", 100) or 100)
        self.font_opacity = int(cfg.get("font_opacity", 100) or 100)
        self.pct_mode = cfg.get("pct_mode", PCT_STEALTH)
        self.client = ProviderClient(cfg.get("provider") or {})

        self.status_label = market_status()[0]
        self.interval_key = market_status()[1]
        self.show_code = bool(cfg.get("show_code", True))
        self._drag_row = None          # 正在拖动的自选股行
        self._drag_to = 0
        self.ach = AchStore()
        if self.ach.data.get("ever_added") is None:
            # 已经有非指数自选股的人，视作"添加过代码"，不再累计
            self.ach.data["ever_added"] = any(
                not ach_is_index(w.get("code")) for w in self.watchlist)
            self.ach.save()
        self.auto_hide_minutes = int(cfg.get("auto_hide_minutes") or 0)
        self._auto_next = None            # 下一次自动隐蔽的时间戳
        self.last_ok_str = "--:--:--"
        self.clock_str = datetime.now().strftime("%H:%M:%S")
        self.fetch_error = False
        self.stale = False
        self.data_qtime = None

        self.hover_btn = None
        self.press_btn = None
        self.btn_rects = {}  # id -> (x, y, w, h)
        self.f_title = self.f_body = self.f_small = self.f_price = None
        self.dpi = 96
        self._cfg_mtime = self._config_mtime()
        # 用户拖拽缩放：只有真正用手拖过才把尺寸写进配置
        self.ui_scale = 1.0
        self._user_resizing = False
        self._size_dirty = False
        self.window_hover = False
        # 外观调节面板（右键菜单打开，替换行情区显示三个滑块）
        self.panel_open = False
        self._drag_slider = None
        # 当前窗口用的分层模式："ulw"（逐像素 alpha）或 "lwa"（整窗 alpha）；两者互斥
        self._layer_mode = None
        # 自选股输入框：弹框在后台线程里做，结果通过 WM_APP_INPUT 回到 UI 线程
        self._input_busy = False
        self._input_result = None
        self._input_mode = "watchlist"     # watchlist / autohide / alert
        self._alerting = {}            # code -> {until, kind, text}
        self._alert_hist = {}          # code -> [(ts, pct)] 用于判突刺
        self._alert_cool = {}          # code -> 冷却截止


        # 布局尺寸（按 DPI 缩放）
        self.header_h = 26
        self.status_h = 18
        self.row_h = 24
        self.width = int(cfg.get("width", 300))
        self.height = 0
        self._layout()

    # ---------------- 基础工具 ----------------
    def _config_mtime(self):
        try:
            return os.path.getmtime(CONFIG_PATH)
        except OSError:
            return None

    def _D(self, v):
        """只按屏幕 DPI 缩放（窗口自身的宽高用这个）。"""
        return max(1, int(round(v * self.dpi / 96.0)))

    def _S(self, v):
        """按屏幕 DPI × 用户拖拽缩放（内部尺寸与间距用这个）。"""
        return max(1, int(round(v * self.ui_scale * self.dpi / 96.0)))

    def _theme_colors(self, name):
        if name == "custom" and isinstance(self.cfg.get("custom_theme"), dict):
            src = self.cfg["custom_theme"]
        else:
            src = THEMES.get(name, THEMES["graywhite"])
        # 透明主题的附加属性
        self.transparent = bool(src.get("transparent"))
        self.window_alpha = int(src.get("window_alpha", 255) or 255)
        shadow = src.get("shadow")
        self.shadow_color = to_colorref(shadow) if shadow else None
        # 逐像素透明时，各区域的不透明度（0-255；只有透明主题用得上）
        self.alpha_header = int(src.get("alpha_header", ALPHA_HEADER) or 0)
        self.alpha_border = int(src.get("alpha_border", ALPHA_BORDER) or 0)
        self.alpha_grip = int(src.get("alpha_grip", ALPHA_GRIP) or 0)
        cols = {k: to_colorref(src[k]) for k in COLOR_KEYS}
        # 菜单栏文字 / 按钮可以单独指定：菜单栏是浅色时（比如透明主题）
        # 必须配深色字，否则标题和行情文字共用 text，必然出现"浅底白字"看不见
        cols["header_text"] = to_colorref(src.get("header_text") or src["text"])
        cols["header_sub"] = to_colorref(src.get("header_sub") or src.get("header_text") or src["sub"])
        cols["grip"] = to_colorref(src.get("grip") or src["sub"])
        return cols

    def _layout(self):
        """尺寸模型：
        宽 = 配置宽（用户拖出来的实际宽度，不受缩放影响）
        高 = 配置高；若为 null 则按行数自适应
        缩放 ui_scale = 配置高 / 基准高 → 标题栏、状态栏、行高、字体一起等比放大
        """
        prev_scale = (self.ui_scale, self.dpi)
        nrows = max(1, len(self.watchlist))
        base_logical = 26 + 2 + 18 + 24 * nrows
        self.width = self._D(int(self.cfg.get("width", 300)))
        h_cfg = self.cfg.get("height")
        if h_cfg:
            try:
                self.ui_scale = min(3.0, max(0.55, float(h_cfg) / float(base_logical)))
            except (TypeError, ValueError):
                self.ui_scale = 1.0
        else:
            self.ui_scale = 1.0

        self.header_h = self._S(26)
        self.status_h = self._S(18)
        self.row_h = self._S(24)
        self.foot_h = self._S(14)
        if self.collapsed:
            self.height = self.header_h
        elif self.panel_open:
            # 外观面板：高度按"当前 ui_scale"直接算。
            # 注意不能反过来让 ui_scale 依赖面板行数 —— 那会和 row_h 形成自激循环。
            self.height = (self.header_h + self._S(2) + self.status_h
                           + self.row_h * len(SLIDER_DEFS))
        elif h_cfg:
            self.height = self._D(h_cfg) + self.foot_h
        else:
            self.height = (self.header_h + self._S(2) + self.status_h
                           + self.row_h * nrows + self.foot_h)

        # 右上角按钮：＋ / 色 / 顶 / 藏 / ×（× 在最右）；
        # 外观面板打开时整条按钮区只剩一个"✓"，免得误点到"×"把程序关掉
        if self.panel_open:
            buttons = (("done", "✓"),)
        else:
            buttons = (("close", "×"), ("hide", "藏"), ("top", "顶"),
                       ("theme", "色"), ("add", "＋"))
        bw, bh, gap, margin = self._S(18), self._S(16), self._S(2), self._S(6)
        x = self.width - margin
        self.btn_rects = {}
        for bid, label in buttons:
            x -= bw
            self.btn_rects[bid] = (x, (self.header_h - bh) // 2, bw, bh, label)
            x -= gap

        # 缩放比例或 DPI 变了 → 字体要按新的比例重建（字体是创建时定死像素高的）
        if (self.ui_scale, self.dpi) != prev_scale and self.f_title is not None:
            self._rebuild_fonts()

    def _style_ov(self):
        # 主窗口没有 self.app，此时返回 {} 表示"不覆盖"
        app = getattr(self, "app", None)
        if app is None:
            return {}
        ov = getattr(app, "cfg", {}).get("chart_style")
        return ov if isinstance(ov, dict) else {}

    def _mkfont(self, pt, bold=False):
        """图表字体：字号/字体名可被「图表外观」单独覆盖（不动主窗口）。"""
        ov = self._style_ov()
        try:
            mul = float(ov.get("font_scale") or 1.0)
        except (TypeError, ValueError):
            mul = 1.0
        fam = str(ov.get("font") or "").strip() or FONT_NAME
        h = -int(round(pt * self.font_scale * mul * self.ui_scale * self.dpi / 72.0))
        # 透明底上 ClearType 会描出彩色边，改用灰度抗锯齿
        quality = 4 if self.transparent else 5
        return gdi32.CreateFontW(h, 0, 0, 0, 700 if bold else 400, 0, 0, 0, 1, 4, 0, quality, 0, fam)

    def _rebuild_fonts(self):
        for f in (self.f_title, self.f_body, self.f_price, self.f_small):
            try:
                if f:
                    gdi32.DeleteObject(f)
            except Exception:
                pass
        self.f_title = self._mkfont(9, bold=True)
        self.f_body = self._mkfont(9)
        self.f_price = self._mkfont(9, bold=True)
        self.f_small = self._mkfont(8)




    def _fill(self, hdc, x, y, w, h, color):
        r = RECT(x, y, x + w, y + h)
        br = gdi32.CreateSolidBrush(color)
        user32.FillRect(hdc, ctypes.byref(r), br)
        gdi32.DeleteObject(br)

    def _text(self, hdc, x, y, w, h, s, font, color, fmt):
        old = gdi32.SelectObject(hdc, font)
        gdi32.SetBkMode(hdc, TRANSPARENT)
        if self.shadow_color is not None:
            # 可选：给文字加一圈描边（四个斜方向各画一遍）。
            # 内置的透明主题不用它（干净、不糊字），但自定义主题里写了 shadow 就能启用
            off = self._D(1)
            gdi32.SetTextColor(hdc, self.shadow_color)
            for dx, dy in ((-off, -off), (off, -off), (-off, off), (off, off)):
                rs = RECT(x + dx, y + dy, x + w + dx, y + h + dy)
                user32.DrawTextW(hdc, s, -1, ctypes.byref(rs), fmt)
        r = RECT(x, y, x + w, y + h)
        gdi32.SetTextColor(hdc, color)
        user32.DrawTextW(hdc, s, -1, ctypes.byref(r), fmt)
        gdi32.SelectObject(hdc, old)

    def _invalidate(self):
        if self.hwnd:
            user32.InvalidateRect(self.hwnd, None, False)
            user32.UpdateWindow(self.hwnd)

    # ---------------- 透明 / 缩放 ----------------
    def _reset_layered(self):
        """SetLayeredWindowAttributes 与 UpdateLayeredWindow 是两条互斥的路：
        只要调用过前者，之后 ULW 一律失败（ERROR_INVALID_PARAMETER = 87）。

        所以"普通主题 ↔ 透明主题"切换时必须把 WS_EX_LAYERED 先摘掉再戴回去，
        否则从浅灰白切到"透明"会一直画不出来（窗口停在上一帧）。
        """
        try:
            ex = user32.GetWindowLongW(self.hwnd, GWL_EXSTYLE)
            user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, ex & ~WS_EX_LAYERED)
            user32.SetWindowLongW(self.hwnd, GWL_EXSTYLE, ex)
            user32.SetWindowPos(self.hwnd, None, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                                | SWP_FRAMECHANGED)
        except Exception as e:
            log("reset layered failed: %r" % e)

    def _apply_layered(self):
        """透明主题走 UpdateLayeredWindow（逐像素 alpha，绘制时提交，这里只做模式切换）；
        普通主题整体不透明，但"背景透明度"滑块会把它调成整窗半透明。

        只在"模式真的变了"的时候才重设 WS_EX_LAYERED ——
        否则拖背景透明度滑块时每动一下就闪一下。
        """
        if not self.hwnd:
            return
        want = "ulw" if self.transparent else "lwa"
        try:
            if want != self._layer_mode:
                self._reset_layered()
                self._layer_mode = want
            if not self.transparent:
                a = int(round(255.0 * max(10, min(100, self.bg_opacity)) / 100.0))
                user32.SetLayeredWindowAttributes(self.hwnd, 0, max(1, min(255, a)), LWA_ALPHA)
        except Exception as e:
            log("apply layered failed: %r" % e)

    def _on_size(self, w, h):
        """用户拖拽改变窗口大小 → 记下新尺寸并重排（换算回 96DPI 逻辑单位）。

        只在"尺寸真的变了"的那一维上写配置：单拖右边只会改宽度，
        高度仍保持自适应，后面加自选股还会自己长高。
        """
        if w <= 0 or h <= 0 or not self._user_resizing:
            return
        changed = False
        if w != self.width:
            self.cfg["width"] = int(round(w * 96.0 / self.dpi))
            changed = True
        # 外观面板打开时高度由面板内容决定，不接受拖动改高 ——
        # 否则"开着面板拉一下边框"，关掉面板后窗口会留住面板那个高度
        if not self.collapsed and not self.panel_open and h != self.height:
            self.cfg["height"] = int(round((h - self.foot_h) * 96.0 / self.dpi))
            changed = True
        self._layout()
        if self.panel_open and h != self.height and self.hwnd:
            user32.SetWindowPos(self.hwnd, None, 0, 0, self.width, self.height,
                                SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
        if not changed:
            return
        self._size_dirty = True
        self._layout()
        self._invalidate()

    def _on_minmaxinfo(self, lparam):
        """限制最小尺寸，避免拖成一条缝看不见。

        ★ 最小高度不能比"当前布局真正需要的高度"还大。否则系统的最小尺寸约束会顶在
        SetWindowPos 上：窗口被撑到 90，而 self.height 只有 64（外观面板在小字号下
        只需要 3 行滑轨的高度）。两者一旦对不上，_draw 只画到 self.height、
        多出来的那一截就留着上一帧的旧像素 —— 表现是"面板下面漏出半截行情行"。
        取 min(...) 之后窗口高度永远等于布局高度，这个问题就不存在了。
        """
        try:
            mmi = ctypes.cast(lparam, ctypes.POINTER(MINMAXINFO)).contents
            mmi.ptMinTrackSize.x = self._D(170)
            if self.collapsed:
                mmi.ptMinTrackSize.y = self._D(24)
            else:
                mmi.ptMinTrackSize.y = min(self._D(90), max(1, int(self.height)))
        except Exception:
            pass

    def _post_refresh(self):
        if self.hwnd:
            try:
                user32.PostMessageW(self.hwnd, WM_APP_REFRESH, 0, 0)
            except Exception:
                pass

    # ---------------- 窗口创建与消息循环 ----------------
    def run(self):
        # DPI 感知（失败则按 96 处理）
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                user32.SetProcessDPIAware()
            except Exception:
                pass
        hdc = user32.GetDC(None)
        self.dpi = gdi32.GetDeviceCaps(hdc, LOGPIXELSY) or 96
        user32.ReleaseDC(None, hdc)

        self._layout()
        self._rebuild_fonts()

        hinst = kernel32.GetModuleHandleW(None)
        self._proc = WNDPROC(self._wnd_proc)
        wc = WNDCLASSEX()
        wc.cbSize = ctypes.sizeof(WNDCLASSEX)
        wc.style = CS_HREDRAW | CS_VREDRAW
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.hIcon = user32.LoadIconW(None, IDI_APPLICATION)
        wc.hCursor = user32.LoadCursorW(None, IDC_ARROW)
        wc.lpszClassName = CLASS_NAME
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            log("RegisterClassExW failed")
            return 1

        # 窗口位置：配置优先；若配置为空或位置已不在任何屏幕内，则回落到主屏右下角
        win = self.cfg.get("window") or {}
        x, y = win.get("x"), win.get("y")
        need_w = min(self._S(140), self.width)
        need_h = min(self.header_h + self._S(4), self.height)
        if (x is None or y is None
                or not rect_visible_enough(int(x), int(y), self.width, self.height,
                                           need_w, need_h)):
            if x is not None and y is not None:
                log("saved window pos (%s,%s) is off-screen, fall back to default" % (x, y))
                win["x"] = win["y"] = None
                self.cfg["window"] = win
            x, y = default_window_pos(self.width, self.height, self._S(24))

        ex_style = WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
        if self.always_on_top:
            ex_style |= WS_EX_TOPMOST
        self.hwnd = user32.CreateWindowExW(
            ex_style, CLASS_NAME, APP_TITLE, WS_POPUP | WS_THICKFRAME,
            int(x), int(y), self.width, self.height,
            None, None, hinst, None)
        if not self.hwnd:
            log("CreateWindowExW failed")
            return 1

        self._apply_layered()
        self._apply_hotkeys()
        user32.SetTimer(self.hwnd, 1, 250, None)
        if not self.cfg.get("license_seen"):
            self.cfg["license_seen"] = True   # 只在首次启动提示一次
            save_config(self.cfg)
            ps_ach_panel(LICENSE_TEXT, "开源协议 · 免责声明")
        # 启动时把"自动隐蔽"状态写进日志：以后一看日志就知道这个实例到底开没开
        if self.auto_hide_minutes:
            log("auto hide: every %d min (%s to show again)"
                % (self.auto_hide_minutes, self._hotkey_label("toggle_hide")))
        else:
            log("auto hide: off")
        self._track_mouse()
        if self.visible:
            user32.ShowWindow(self.hwnd, SW_SHOW)
            user32.UpdateWindow(self.hwnd)
        self._start_worker()

        msg = MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        return 0

    def _track_mouse(self):
        try:
            tme = TRACKMOUSEEVENT(ctypes.sizeof(TRACKMOUSEEVENT), TME_LEAVE, self.hwnd, 0)
            ctypes.windll.comctl32.TrackMouseEvent(ctypes.byref(tme))
        except Exception:
            pass

    def _cleanup(self):
        try:
            user32.KillTimer(self.hwnd, 1)
            for hid in (HK_HIDE, HK_THEME, HK_TOP, HK_REFRESH, HK_NAME):
                user32.UnregisterHotKey(self.hwnd, hid)
        except Exception:
            pass
        self._stop.set()
        self._wake.set()
        # 若关窗时输入框还开着，顺手收掉子进程，免得留下一个孤儿对话框
        kill_ps_input_box()
        # K 线窗口是独立顶层窗口，不会随主窗口自动消失，必须逐个关掉
        for w in list(_charts):
            try:
                if w.hwnd:
                    user32.DestroyWindow(w.hwnd)
            except Exception:
                pass
        for f in (self.f_title, self.f_body, self.f_price, self.f_small):
            try:
                if f:
                    gdi32.DeleteObject(f)
            except Exception:
                pass
        self.hwnd = None

    # ---------------- 消息处理 ----------------
    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_PAINT:
                self._on_paint(hwnd)
                return 0
            if msg == WM_ERASEBKGND:
                return 1
            if msg == WM_TIMER:
                self._on_tick()
                return 0
            if msg == WM_NCCALCSIZE:
                # WS_THICKFRAME 让系统支持四边/四角原生缩放，但会附带一条系统非客户区边框；
                # 这里把客户区收成整个窗口 → 视觉上仍是纯无边框。
                # wParam 有两种，都要处理：
                #   TRUE  尺寸正在变化，返回 0 即"客户区 = 整个窗口矩形"；
                #   FALSE 只在窗口创建时来一次，此时 lParam 指向的建议客户区矩形用的是
                #         **屏幕坐标**，必须改写成窗口自身的屏幕矩形。若图省事写成
                #         (0,0,w,h)，客户区会被搬到屏幕左上角，ScreenToClient 随之整体
                #         错位（返回的客户区坐标仍是屏幕坐标），拖动/缩放判定全部失效。
                if not wparam:
                    try:
                        r = ctypes.cast(ctypes.c_void_p(lparam & 0xFFFFFFFFFFFFFFFF),
                                        ctypes.POINTER(RECT)).contents
                        wr = RECT()
                        if user32.GetWindowRect(hwnd, ctypes.byref(wr)):
                            r.left, r.top = wr.left, wr.top
                            r.right, r.bottom = wr.right, wr.bottom
                    except Exception as e:
                        log("nccalcsize adjust failed: %r" % e)
                return 0
            if msg == WM_NCHITTEST:
                return self._on_nchittest(hwnd, lparam)
            if msg == WM_MOUSEMOVE:
                self._on_mousemove(hwnd, lparam)
                return 0
            if msg == WM_MOUSELEAVE:
                changed = False
                if self.hover_btn is not None:
                    self.hover_btn = None
                    changed = True
                if self.window_hover:
                    self.window_hover = False
                    changed = True
                if changed:
                    self._invalidate()
                return 0
            if msg == WM_LBUTTONDOWN:
                self._on_lbuttondown(lparam)
                return 0
            if msg == WM_LBUTTONUP:
                self._on_lbuttonup()
                return 0
            if msg == WM_LBUTTONDBLCLK:
                self._toggle_collapse()
                return 0
            if msg == WM_NCLBUTTONDBLCLK:
                # 标题栏被拖的是"非客户区"（HTCAPTION），双击消息也走非客户区那套，
                # 所以折叠/展开要在这里接。其他位置双击一律吞掉，避免 WS_THICKFRAME
                # 默认的"双击边框最大化"把小组件撑满屏
                if self._on_nchittest(hwnd, lparam) == HTCAPTION:
                    self._toggle_collapse()
                return 0
            if msg == WM_RBUTTONUP:
                self._show_menu(hwnd)
                return 0
            if msg == WM_HOTKEY:
                self._on_hotkey(int(wparam))
                return 0
            if msg == WM_APP_REFRESH:
                self._on_data_refreshed()
                return 0
            if msg == WM_APP_INPUT:
                self._on_watchlist_input()
                return 0
            if msg == WM_COMMAND:
                self._on_command(int(wparam & 0xFFFF))
                return 0
            if msg == WM_GETMINMAXINFO:
                self._on_minmaxinfo(lparam)
                return 0
            if msg == WM_ENTERSIZEMOVE:
                self._user_resizing = True
                return 0
            if msg == WM_SIZE:
                self._on_size(_lo16(lparam), _hi16(lparam))
                return 0
            if msg == WM_EXITSIZEMOVE:
                self._user_resizing = False
                self._save_window_pos()
                return 0
            if msg == WM_CLOSE:
                self._save_window_pos()
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                self._cleanup()
                user32.PostQuitMessage(0)
                return 0
            return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        except Exception as e:
            log("wndproc error msg=0x%X err=%r\n%s" % (msg, e, traceback.format_exc()))
            try:
                return user32.DefWindowProcW(hwnd, msg, wparam, lparam)
            except Exception:
                return 0

    def _on_nchittest(self, hwnd, lparam):
        pt = POINT(_lo16(lparam), _hi16(lparam))
        user32.ScreenToClient(hwnd, ctypes.byref(pt))
        x, y = pt.x, pt.y
        w, h = self.width, self.height
        # 边框命中 → 交给系统做原生缩放（四边 + 四角）
        m = self._D(6)
        if (self.transparent and x >= w - self._D(16) and y >= h - self._D(16)):
            return HTBOTTOMRIGHT          # 透明主题下的右下角抓手块
        left, right = x < m, x >= w - m
        if not self.collapsed:
            top, bottom = y < m, y >= h - m
            if left and top:
                return HTTOPLEFT
            if right and top:
                return HTTOPRIGHT
            if left and bottom:
                return HTBOTTOMLEFT
            if right and bottom:
                return HTBOTTOMRIGHT
            if top:
                return HTTOP
            if bottom:
                return HTBOTTOM
        if left:
            return HTLEFT
        if right:
            return HTRIGHT
        if y < self.header_h:
            for bid, (bx, by, bw, bh, _label) in self.btn_rects.items():
                if bx <= x <= bx + bw and by <= y <= by + bh:
                    return HTCLIENT
            return HTCAPTION
        return HTCLIENT

    def _on_mousemove(self, hwnd, lparam):
        x, y = _lo16(lparam), _hi16(lparam)
        if self._drag_row is not None:
            self._drag_row_move(y)
            return
        # 正在拖滑块：只有它在动，别再走按钮 hover 那一套
        if self._drag_slider:
            self._slider_set(self._drag_slider, x)
            return
        changed = False
        if self.transparent and not self.window_hover:
            self.window_hover = True
            changed = True
        hid = None
        for bid, (bx, by, bw, bh, _label) in self.btn_rects.items():
            if bx <= x <= bx + bw and by <= y <= by + bh:
                hid = bid
                break
        if hid != self.hover_btn:
            self.hover_btn = hid
            changed = True
        # 异动提示：过期的清掉；有提示在场就每帧重绘（闪烁/故障动起来）
        now_ts = datetime.now().timestamp()
        for c in [c for c, v in self._alerting.items() if v["until"] <= now_ts]:
            self._alerting.pop(c, None)
            changed = True
        if self._alerting:
            changed = True
        if changed:
            self._invalidate()
        self._track_mouse()

    def _on_lbuttondown(self, lparam):
        x, y = _lo16(lparam), _hi16(lparam)
        if self.panel_open:
            key = self._slider_hit(x, y)
            if key:
                self._drag_slider = key
                try:
                    user32.SetCapture(self.hwnd)   # 鼠标拖出窗口也还能继续调
                except Exception:
                    pass
                self._slider_set(key, x)
                return
        for bid, (bx, by, bw, bh, _label) in self.btn_rects.items():
            if bx <= x <= bx + bw and by <= y <= by + bh:
                self.press_btn = bid
                return
        # 行情行：按住就能上下拖动排序
        if not self.panel_open and not self.collapsed and self.watchlist:
            ry = self._rows_top()
            if y >= ry:
                idx = int((y - ry) // max(1, self.row_h))
                if 0 <= idx < len(self.watchlist):
                    self._drag_row = idx
                    self._drag_to = idx
                    try:
                        user32.SetCapture(self.hwnd)
                    except Exception:
                        pass
                    return

    def _on_lbuttonup(self):
        if self._drag_row is not None:
            self._end_row_drag()
            return
        if self._drag_slider:
            self._drag_slider = None
            try:
                user32.ReleaseCapture()
            except Exception:
                pass
            self._save_appearance()
            return
        bid = self.press_btn
        self.press_btn = None
        if bid == "theme":
            self._cycle_theme()
        elif bid == "top":
            self._toggle_top()
        elif bid == "hide":
            self._toggle_hide()
        elif bid == "add":
            self._edit_watchlist()
        elif bid == "done":
            self._close_panel()
        elif bid == "close":
            self._save_window_pos()
            if self.hwnd:
                user32.DestroyWindow(self.hwnd)

    def _on_hotkey(self, hid):
        if hid == HK_HIDE:
            self._toggle_hide()
        elif hid == HK_THEME:
            self._cycle_theme()
        elif hid == HK_TOP:
            self._toggle_top()
        elif hid == HK_REFRESH:
            self._wake.set()
        elif hid == HK_NAME:
            self._toggle_name_style()

    def _on_command(self, cid):
        if cid == ID_REFRESH:
            self._wake.set()
        elif cid == ID_THEME:
            self._cycle_theme()
        elif cid == ID_TOP:
            self._toggle_top()
        elif cid == ID_COLLAPSE:
            self._toggle_collapse()
        elif cid == ID_NAME:
            self._toggle_name_style()
        elif cid == ID_PCT:
            self._toggle_pct_mode()
        elif cid == ID_LOOK:
            if self.panel_open:
                self._close_panel()
            else:
                self._open_panel()
        elif cid == ID_HOME:
            self._move_home()
        elif cid == ID_KLINE:
            self._open_chart()
        elif cid == ID_AUTO:
            self._set_auto_hide()
        elif cid == ID_CHART_STYLE_MAIN:
            self._chart_style_dialog()
        elif cid == ID_LICENSE:
            ps_ach_panel(LICENSE_TEXT, "开源协议 · 免责声明")
        elif cid == ID_HOTKEYS:
            self._set_hotkeys()
        elif cid == ID_ACH:
            self._ach_panel()
        elif cid == ID_SHOWCODE:
            self.show_code = not self.show_code
            self.cfg["show_code"] = self.show_code
            save_config(self.cfg)
            self._cfg_mtime = self._config_mtime()
            self._invalidate()
        elif cid == ID_ALERT_STYLE:
            self._toggle_alert_style()
        elif cid == ID_ALERT_SET:
            self._set_alerts()
        elif cid == ID_EDIT:
            self._edit_watchlist()
        elif cid == ID_OPEN:
            try:
                os.startfile(CONFIG_PATH)
            except Exception as e:
                log("open config failed: %r" % e)
        elif cid == ID_EXIT:
            self._save_window_pos()
            if self.hwnd:
                user32.DestroyWindow(self.hwnd)

    def _open_chart(self):
        """右键菜单 → K 线图：开一个独立的 K 线窗口；再点一次就是再开一个。"""
        wl = [w["code"] for w in self.watchlist] or ["sh000001"]
        code = getattr(self, "_last_chart_code", None) or wl[0]
        w = ChartWindow(self, code)
        if w.open():
            self._last_chart_code = code

    def _invalidate_charts(self):
        """主窗口的外观（主题 / 字号 / 透明度）变了 → 让所有 K 线窗口跟着重画。"""
        for w in list(_charts):
            try:
                w._rebuild_fonts()
                w._apply_alpha()
                w._invalidate()
            except Exception:
                pass

    # ---------------- 异动提示 ----------------
    def _alert_cfg(self):
        a = self.cfg.get("alert")
        return a if isinstance(a, dict) else {}

    def _alert_active(self, code):
        a = self._alerting.get(code)
        if not a:
            return None
        if a["until"] <= datetime.now().timestamp():
            self._alerting.pop(code, None)
            return None
        return a

    def _trigger_alert(self, code, kind, text, hold, cool, now):
        self._alerting[code] = {"until": now + hold, "kind": kind, "text": text}
        self._alert_cool[code] = now + cool
        log("alert: %s %s (%s)" % (code, kind, text))

    def _check_alerts(self):
        """每轮取数后检查：① 窗口内涨跌幅突刺 ② 跌破止损 / 突破目标。"""
        a = self._alert_cfg()
        if not a.get("enabled", True):
            return
        now = datetime.now().timestamp()
        win = max(30, int(a.get("surge_window") or 180))
        thr = abs(float(a.get("surge_pct") or 0.0))
        hold = max(2, int(a.get("hold") or 10))
        cool = max(0, int(a.get("cooldown") or 60))
        levels = a.get("levels") if isinstance(a.get("levels"), dict) else {}
        for i, wl in enumerate(self.watchlist):
            q = self.quotes[i] if i < len(self.quotes) else None
            if not q:
                continue
            code = wl.get("code") or ""
            pct, price = q.get("pct"), q.get("price")
            if pct is not None:
                hist = self._alert_hist.setdefault(code, [])
                hist.append((now, pct))
                while hist and now - hist[0][0] > win:
                    hist.pop(0)
                if thr and len(hist) >= 2 and now >= self._alert_cool.get(code, 0):
                    delta = pct - hist[0][1]
                    if abs(delta) >= thr:
                        word = "拉升" if delta > 0 else "下跌"
                        self._trigger_alert(code, "surge", "%d 分钟%s %.2f%%"
                                            % (win // 60, word, abs(delta)), hold, cool, now)
                        continue
            lv = levels.get(code) or {}
            if price is None:
                continue
            try:
                stop, target = lv.get("stop"), lv.get("target")
                if stop and price <= float(stop) and now >= self._alert_cool.get(code, 0):
                    self._trigger_alert(code, "stop", "跌破止损 %.2f" % float(stop), hold, cool, now)
                    continue
                if target and price >= float(target) and now >= self._alert_cool.get(code, 0):
                    self._trigger_alert(code, "target", "突破目标 %.2f" % float(target), hold, cool, now)
            except (TypeError, ValueError):
                pass

    def _alert_palette(self, t, code):
        """该行用的调色板。闪烁=只换相近底色（隐蔽）；故障=暗底霓虹字。"""
        tp = dict(t)
        if self._alert_cfg().get("style", "blink") == "glitch":
            tp["panel"] = 0x0A0A12
            tp["text"] = 0xF2FBFF
            tp["sub"] = 0x9BE8FF
            tp["up"] = 0x00E5FF
            tp["down"] = 0xFF2D95
        else:
            on = int(datetime.now().timestamp() * 4) % 2 == 0
            tp["panel"] = _mix_color(t["panel"], t["text"], 0.12 if on else 0.02)
        return tp

    def _glitch_fx(self, memdc, w, y0, code, t):
        """赛博朋克故障：霓虹彩条 + 上下撕裂线（每 80ms 变一次，随机但可复现）。"""
        seed = int(datetime.now().timestamp() * 12) ^ (hash(code) & 0xFFFF)

        def rnd(n):
            nonlocal seed
            seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
            return seed % n

        colors = (0x00E5FF, 0xFF2D95, 0xF7FF00)
        rh = self.row_h
        x0, ww = 1, w - 2
        self._fill(memdc, x0, y0, ww, 1, colors[rnd(3)])
        self._fill(memdc, x0, y0 + rh - 1, ww, 1, colors[rnd(3)])
        for _ in range(2 + rnd(3)):
            bw = self._S(18) + rnd(max(1, ww - self._S(18)))
            self._fill(memdc, x0 + rnd(max(1, ww - bw)), y0 + rnd(max(1, rh - 1)),
                       bw, 1 + rnd(2), colors[rnd(3)])

    def _text_width(self, hdc, s, font):
        """量一段文字的像素宽（用来把代码紧跟在名称后面）。"""
        old = gdi32.SelectObject(hdc, font)
        try:
            sz = wintypes.SIZE()
            gdi32.GetTextExtentPoint32W(hdc, s, len(s), ctypes.byref(sz))
            return int(sz.cx)
        except Exception:
            return len(s) * self._S(7)
        finally:
            gdi32.SelectObject(hdc, old)

    def _code_label(self, code):
        c = (code or "").strip().lower()
        num = c[2:] if c[:2] in ("sh", "sz", "bj") else c
        return "(%s)" % num if num else ""

    # ---------------- 成就 ----------------
    def _ach_cfg_ok(self):
        """成就3 要求的"高调设置"：完整名称 / 显示代码 / 正常涨跌幅 / 不自动隐蔽 / 全 100%+。"""
        try:
            return (self.name_style == "full" and bool(self.show_code)
                    and self.pct_mode == "normal"
                    and not int(self.cfg.get("auto_hide_minutes") or 0)
                    and int(self.cfg.get("bg_opacity", 100)) >= 100
                    and int(self.cfg.get("font_opacity", 100)) >= 100
                    and float(self.cfg.get("font_scale", 1.0)) >= 0.999)
        except (TypeError, ValueError):
            return False

    def _ach_tick(self, dt, t):
        """每 250ms 累计一次：摸鱼时长、当日状态（可见/隐藏/图表数/是否高调）。"""
        a = self.ach
        a.rollover(t)
        d = a.data.get("d") or {}
        if not d:
            a.save()
            return
        if self.visible:
            d["vis"] = float(d.get("vis", 0)) + dt
            if self.status_label == "Trading":
                a.data["fish_sec"] = float(a.data.get("fish_sec", 0)) + dt
        else:
            d["hid"] = float(d.get("hid", 0)) + dt
        d["chart_max"] = max(int(d.get("chart_max", 0)), len(_charts))
        if self.collapsed:
            d["collapsed"] = True
        if not self._ach_cfg_ok():
            d["bad"] = True
        hm = t.hour * 60 + t.minute
        if t.weekday() < 5 and 14 * 60 + 55 <= hm <= 15 * 60 + 10:
            d["close_alive"] = True
        d["arrogant_ok"] = (not d["bad"] and not d["collapsed"]
                            and int(d["chart_max"]) >= 4 and int(d.get("hides", 0)) == 0)
        d["sleep_ok"] = (float(d.get("vis", 0)) <= 0.5 and float(d.get("hid", 0)) > 0)
        if int(a.data.get("streak_arrogant", 0)) >= 5:
            self._ach_unlock("arrogant")
        if int(a.data.get("streak_sleep", 0)) >= 5:
            self._ach_unlock("sleep")
        if int(a.data.get("fish_sec", 0)) >= 240 * 3600:
            self._ach_unlock("salary")
        # 主打陪伴：从未添加过个股时，按运行时长累计 48 小时
        if not a.data.get("ever_added"):
            a.data["alone_sec"] = float(a.data.get("alone_sec", 0)) + dt
            if float(a.data["alone_sec"]) >= 48 * 3600:
                self._ach_unlock("alone")
        self._ach_save_at = getattr(self, "_ach_save_at", 0) + dt
        if self._ach_save_at >= 30:
            self._ach_save_at = 0
            a.save()

    def _ach_check_quotes(self):
        """每轮取数后判定“全跌停 / 全跌超 5%”。"""
        a = self.ach
        if ach_all_limit_down(self.watchlist, self.quotes):
            self._ach_unlock("calm")
        d = a.data.get("d") or {}
        t = datetime.now()
        hm = t.hour * 60 + t.minute
        if d.get("close_alive") and hm >= 15 * 60 and ach_all_drop(self.watchlist, self.quotes, -5.0):
            self._ach_unlock("immovable")

    def _ach_unlock(self, key):
        if not self.ach.unlock(key):
            return
        name = ACH_NAMES.get(key, key)
        # 弹窗第三行用条文最后一行（那句吐槽），而不是条件描述：
        # 《月薪三千》你已摸鱼240个小时，已从老板手上多赚了3000块
        _full = dict((k, f) for k, _n, _s, f in ACH_LIST).get(key) or ()
        desc = _full[-1] if _full else ""
        log("achievement unlocked: %s" % name)
        AchievementToast.show(self, name, desc)
        self._invalidate()

    def _ach_text(self):
        """成就栏正文：**只列已解锁的**，未达成的不显示、也不剧透条件。"""
        u = self.ach.data.get("unlocked", {})
        lines = []
        n = 0
        for key, name, _short, full in ACH_LIST:
            if key not in u:
                continue
            n += 1
            lines.append("★ %s" % name)
            for seg in full:
                lines.append("    %s" % seg)
            lines.append("    解锁于 %s" % u[key])
            lines.append("")
        if not lines:
            return "还没有解锁任何成就。" + chr(10) + chr(10) + \
                   "慢慢来，它们会在你不注意的时候自己出现。"
        return "已解锁 %d 个" % n + chr(10) + chr(10) + chr(10).join(lines).rstrip()

    def _ach_panel(self):
        ps_ach_panel(self._ach_text())
    # ---------------- 自选股排序 ----------------
    def _rows_top(self):
        return self.header_h + self._S(2) + self.status_h

    def _drag_row_move(self, y):
        ry = self._rows_top()
        n = len(self.watchlist)
        idx = int((y - ry) // max(1, self.row_h))
        self._drag_to = max(0, min(n - 1, idx))
        self._invalidate()

    def _end_row_drag(self):
        src, dst = self._drag_row, self._drag_to
        self._drag_row = None
        try:
            user32.ReleaseCapture()
        except Exception:
            pass
        if src is None or src == dst:
            self._invalidate()
            return
        self._move_watch_row(src, dst)

    def _move_watch_row(self, src, dst):
        """把第 src 行移到第 dst 位（行情数组同步搬，避免价格错位）。"""
        n = len(self.watchlist)
        if not (0 <= src < n and 0 <= dst < n):
            return
        self.watchlist.insert(dst, self.watchlist.pop(src))
        try:
            if len(self.quotes) == n:
                self.quotes.insert(dst, self.quotes.pop(src))
        except Exception:
            self.quotes = []
        self.cfg["watchlist"] = [dict(w) for w in self.watchlist]
        save_config(self.cfg)
        self._cfg_mtime = self._config_mtime()
        log("watchlist reorder: %d -> %d" % (src, dst))
        self._invalidate()

    # ---------------- 快捷键 ----------------
    def _chart_style_dialog(self):
        """主窗口入口：在已打开的 K 线窗口里展开外观滑块。"""
        if not _charts:
            self._msgbox("先打开一个 K 线图窗口（右键 → K 线图）。")
            return
        for w in list(_charts):
            try:
                w.panel_open = True
                w._invalidate()
            except Exception as e:
                log("open chart panel failed: %r" % e)

    def _set_hotkeys(self):
        hk = dict(self.cfg.get("hotkeys") or {})
        rows = [("隐藏 / 显示", hk.get("toggle_hide") or ""),
                ("切换主题", hk.get("cycle_theme") or ""),
                ("窗口置顶", hk.get("toggle_top") or ""),
                ("立即刷新", hk.get("refresh_now") or ""),
                ("名称显示", hk.get("cycle_name") or "")]
        vals = ps_field_form("快捷键设置", rows, "格式如 ctrl+alt+h；留空 = 不绑定")
        if vals is None:
            return
        keys = ["toggle_hide", "cycle_theme", "toggle_top", "refresh_now", "cycle_name"]
        new = {}
        for k, v in zip(keys, vals):
            v = v.strip().lower()
            if v:
                new[k] = v
        self.cfg["hotkeys"] = new
        save_config(self.cfg)
        self._cfg_mtime = self._config_mtime()
        self._apply_hotkeys()
        self._msgbox("快捷键已更新：\n" + ("\n".join("%s = %s" % (k, v) for k, v in new.items()) or "（全部留空）"))

    def _apply_chart_style(self):
        for w in list(_charts):
            try:
                w._reload_style()
            except Exception:
                pass

    def _draw_footer(self, memdc, w, h, t):
        """底部小栏：最后取数时间（右边留出透明主题的缩放手柄）。"""
        fh = self.foot_h
        y = h - fh
        self._fill(memdc, 0, y, w, fh, t["panel"])
        self._fill(memdc, 1, y, w - 2, 1, t["border"])
        txt = "最后取数 %s" % (self.last_ok_str or "--:--:--")
        pad = self._D(16) if self.transparent else self._S(8)
        self._text(memdc, self._S(8), y, w - self._S(8) - pad, fh, txt,
                   self.f_small, t["sub"],
                   DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS)

    def _paint_row(self, memdc, w, wl_item, q, y0, right_pad, t):
        code = wl_item.get("code") or ""
        a = self._alert_active(code)
        tp = t
        if a:
            tp = self._alert_palette(t, code)
            self._fill(memdc, 1, y0, w - 2, self.row_h, tp["panel"])
        raw_name = (wl_item.get("alias") or (q or {}).get("name") or wl_item["code"])
        name = display_name(raw_name, self.name_style)
        price = "--" if not q or q.get("price") is None else "%.2f" % q["price"]
        pct, pct_color = self._fmt_pct(q, tp)
        price_x = w - right_pad - self._S(56)
        pct_x = price_x - self._S(64)
        # 名称后面紧跟 (代码)：代码用小一号字；挤不下时先压缩名称（省略号），代码优先保留
        code_txt = self._code_label(wl_item.get("code")) if self.show_code else ""
        avail = max(self._S(30), pct_x - self._S(14) - self._S(10))
        name_w = self._text_width(memdc, name, self.f_body)
        code_w = self._text_width(memdc, code_txt, self.f_small)
        gap = self._S(3)
        rect_w = name_w + 2 if name_w + gap + code_w <= avail else max(self._S(16), avail - code_w - gap)
        self._text(memdc, self._S(10), y0, rect_w, self.row_h,
                   name, self.f_body, tp["text"],
                   DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS)
        if code_txt and code_w <= avail:
            self._text(memdc, self._S(10) + rect_w + gap, y0, code_w + self._S(4), self.row_h,
                       code_txt, self.f_small, tp["sub"],
                       DT_LEFT | DT_VCENTER | DT_SINGLELINE)
        self._text(memdc, price_x, y0, self._S(56), self.row_h,
                   price, self.f_price, tp["text"],
                   DT_RIGHT | DT_VCENTER | DT_SINGLELINE)
        self._text(memdc, pct_x, y0, self._S(64) - self._S(6), self.row_h,
                   pct, self.f_body, pct_color,
                   DT_RIGHT | DT_VCENTER | DT_SINGLELINE)
        if a and self._alert_cfg().get("style", "blink") == "glitch":
            self._glitch_fx(memdc, w, y0, code, t)

    def _toggle_alert_style(self):
        a = dict(self._alert_cfg())
        a["style"] = "blink" if a.get("style", "blink") == "glitch" else "glitch"
        self.cfg["alert"] = a
        save_config(self.cfg)
        self._cfg_mtime = self._config_mtime()
        self._invalidate()

    def _set_alerts(self):
        """5 个输入框的表单：代码 / 分钟 / 幅度% / 止损价 / 目标价。"""
        if self._input_busy:
            log("set alerts: skipped, another input dialog is still open")
            return
        a = self._alert_cfg()
        code = ""
        if self.watchlist:
            code = self.watchlist[0].get("code") or ""
        lv = (a.get("levels") or {}).get(code) or {}
        vals = (code,
                "%d" % max(1, int(a.get("surge_window", 180)) // 60),
                "%g" % float(a.get("surge_pct", 2.0)),
                "" if lv.get("stop") is None else "%g" % float(lv["stop"]),
                "" if lv.get("target") is None else "%g" % float(lv["target"]))
        self._input_busy = True
        self._input_mode = "alert"
        self._input_result = None
        self._set_topmost(False)
        threading.Thread(target=self._alert_form_worker, args=(vals,), daemon=True).start()

    def _alert_form_worker(self, vals):
        res = None
        try:
            res = ps_alert_form(vals)
        except Exception as e:
            log("alert form worker failed: %r" % e)
        self._input_result = res
        if self.hwnd:
            try:
                user32.PostMessageW(self.hwnd, WM_APP_INPUT, 0, 0)
            except Exception:
                pass

    def _apply_alert_form(self, v):
        """表单回来：前两项是全局阈值，后面是这只股票的止损/目标。"""
        if not v or len(v) < 5:
            return
        a = dict(self._alert_cfg())
        try:
            if v[1].strip():
                a["surge_window"] = max(1, int(float(v[1].strip()))) * 60
            if v[2].strip():
                a["surge_pct"] = abs(float(v[2].strip()))
        except ValueError:
            self._msgbox("分钟 / 幅度请填数字。")
            return
        code = normalize_code(v[0].strip()) if v[0].strip() else None
        levels = dict(a.get("levels") or {})
        if code:
            lv = {}
            for key, idx in (("stop", 3), ("target", 4)):
                txt = v[idx].strip()
                if txt:
                    try:
                        lv[key] = float(txt)
                    except ValueError:
                        self._msgbox("%s 价请填数字（或留空）。" % ("止损" if key == "stop" else "目标"))
                        return
            if lv:
                levels[code] = lv
            else:
                levels.pop(code, None)
        a["levels"] = levels
        a.setdefault("enabled", True)
        self.cfg["alert"] = a
        save_config(self.cfg)
        self._cfg_mtime = self._config_mtime()
        tail = ("；%s 已设 %s" % (code, "/".join(
            k for k in ("stop", "target") if levels.get(code, {}).get(k)))
            if code and levels.get(code) else "")
        self._msgbox("已保存：%d 分钟内涨跌 %g%% 触发%s"
                     % (max(1, int(a.get("surge_window", 180)) // 60),
                        float(a.get("surge_pct", 2.0)), tail))

    def _apply_alerts(self, s):
        if not s or not s.strip():
            return
        a = dict(self._alert_cfg())
        levels = dict(a.get("levels") or {})
        segs = [x.strip() for x in s.replace("，", ";").replace("；", ";").split(";") if x.strip()]
        first = True
        for seg in segs:
            f = seg.replace(",", " ").replace("，", " ").split()
            if first and len(f) >= 2:
                first = False
                try:
                    a["surge_window"] = max(1, int(float(f[0]))) * 60
                    a["surge_pct"] = abs(float(f[1]))
                    continue
                except ValueError:
                    pass
            if len(f) < 2:
                continue
            parts = seg.replace(",", " ").replace("，", " ").split()
            code = normalize_code(parts[0])
            if not code:
                continue
            lv = {}
            for key, idx in (("stop", 1), ("target", 2)):
                if len(parts) > idx and parts[idx] not in ("-", "0", ""):
                    try:
                        lv[key] = float(parts[idx])
                    except ValueError:
                        pass
            if lv:
                levels[code] = lv
            else:
                levels.pop(code, None)
        a["levels"] = levels
        a.setdefault("enabled", True)
        self.cfg["alert"] = a
        save_config(self.cfg)
        self._cfg_mtime = self._config_mtime()
        self._msgbox("异动提示已保存：\n突刺 %d 分钟 %g%%\n%d 只设了止损/目标"
                     % (max(1, int(a.get("surge_window", 180)) // 60),
                        float(a.get("surge_pct", 2.0)), len(levels)))

    def _show_menu(self, hwnd):
        hmenu = user32.CreatePopupMenu()
        user32.AppendMenuW(hmenu, MF_STRING, ID_REFRESH, "立即刷新")
        user32.AppendMenuW(hmenu, MF_STRING, ID_THEME, "切换主题")
        user32.AppendMenuW(hmenu, MF_STRING, ID_TOP, "置顶开关")
        user32.AppendMenuW(hmenu, MF_STRING, ID_COLLAPSE, "折叠 / 展开")
        name_label = ("名称显示：拼音首字母 ✓" if self.name_style == "pinyin"
                      else "名称显示：完整名称 ✓")
        user32.AppendMenuW(hmenu, MF_STRING, ID_NAME, name_label)
        user32.AppendMenuW(hmenu, MF_STRING, ID_SHOWCODE,
                           "代码显示：%s" % ("显示 ✓" if self.show_code else "隐藏"))
        pct_label = ("涨跌幅：隐蔽模式（灰黑 0.xxxx）✓" if self.pct_mode == PCT_STEALTH
                     else "涨跌幅：正常模式（红绿百分比）✓")
        user32.AppendMenuW(hmenu, MF_STRING, ID_PCT, pct_label)
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        look_label = "完成外观调节" if self.panel_open else "外观调节（透明度 / 字号）…"
        user32.AppendMenuW(hmenu, MF_STRING, ID_LOOK, look_label)
        user32.AppendMenuW(hmenu, MF_STRING, ID_HOME, "窗口归位（移到右下角）")
        user32.AppendMenuW(hmenu, MF_STRING, ID_KLINE, "K 线图（可多开）…")
        auto_label = ("自动隐蔽：每 %d 分钟…" % self.auto_hide_minutes
                      if self.auto_hide_minutes else "自动隐蔽：关闭…")
        user32.AppendMenuW(hmenu, MF_STRING, ID_AUTO, auto_label)
        style = self._alert_cfg().get("style", "blink")
        user32.AppendMenuW(hmenu, MF_STRING, ID_ALERT_STYLE,
                           "异动提示效果：%s" % ("赛博故障" if style == "glitch" else "隐蔽闪烁"))
        user32.AppendMenuW(hmenu, MF_STRING, ID_ALERT_SET, "设置异动提示（突刺/止损/目标）…")
        user32.AppendMenuW(hmenu, MF_STRING, ID_ACH, "成就（%d/%d）…" % (
            len(self.ach.data.get("unlocked", {})), len(ACH_LIST)))
        user32.AppendMenuW(hmenu, MF_STRING, ID_HOTKEYS, "快捷键设置…")
        user32.AppendMenuW(hmenu, MF_STRING, ID_LICENSE, "开源协议 / 免责声明…")
        user32.AppendMenuW(hmenu, MF_STRING, ID_CHART_STYLE_MAIN, "K 线图外观（字号/透明度/字体）…")
        user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(hmenu, MF_STRING, ID_EDIT, "添加 / 删除自选股…")
        user32.AppendMenuW(hmenu, MF_STRING, ID_OPEN, "打开配置文件")
        user32.AppendMenuW(hmenu, MF_STRING, ID_EXIT, "退出")
        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        cmd = user32.TrackPopupMenu(hmenu, TPM_RETURNCMD | TPM_LEFTBUTTON,
                                    pt.x, pt.y, 0, hwnd, None)
        user32.DestroyMenu(hmenu)
        if cmd:
            self._on_command(cmd)

    # ---------------- 动作 ----------------
    def _toggle_hide(self):
        if not self.hwnd:
            return
        if self.visible:
            user32.ShowWindow(self.hwnd, SW_HIDE)
            self.visible = False
            try:
                _d = (self.ach.data.get("d") or {})
                _d["hides"] = int(_d.get("hides", 0)) + 1
            except Exception:
                pass
            for w in list(_charts):        # K 线窗口跟着一起藏
                w.hide_by_auto()
        else:
            if self.always_on_top:
                user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            user32.ShowWindow(self.hwnd, SW_SHOW)
            self.visible = True
            for w in list(_charts):
                w.show_by_auto()
            self._auto_next = self._auto_deadline()   # 刚显示出来，自动隐蔽重新计时
            self._wake.set()

    def _auto_deadline(self):
        if not self.auto_hide_minutes:
            return None
        return datetime.now().timestamp() + self.auto_hide_minutes * 60

    def _auto_hide_all(self):
        """自动隐蔽：主窗口 + 所有 K 线窗口一起藏起来，只能靠"隐藏/显示"热键叫回来。"""
        if self.hwnd and self.visible:
            user32.ShowWindow(self.hwnd, SW_HIDE)
            self.visible = False
        for w in list(_charts):
            w.hide_by_auto()
        self._auto_next = self._auto_deadline()
        log("auto hide: all windows hidden (every %d min)" % self.auto_hide_minutes)

    def _hotkey_label(self, key):
        """把配置里的组合键写法变成人话：ctrl+alt+h → Ctrl+Alt+H"""
        v = (self.cfg.get("hotkeys") or {}).get(key) or ""
        names = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
        parts = [names.get(p.strip().lower(), p.strip().upper())
                 for p in v.split("+") if p.strip()]
        return "+".join(parts) if parts else "Ctrl+Alt+H"

    def _set_auto_hide(self):
        """弹出设置框。★ 提示里必须写出"显示回来要按哪个键" —— 藏起来以后没有别的入口。"""
        if self._input_busy:
            return
        hk = self._hotkey_label("toggle_hide")
        cur = ("关闭" if not self.auto_hide_minutes
               else "每 %d 分钟" % self.auto_hide_minutes)
        prompt = ("每隔多少分钟自动隐藏？（输入 0 = 关闭）\n\n"
                  "★ 隐藏之后，要按 %s 才能重新显示；\n"
                  "  主窗口和所有 K 线窗口会一起隐藏、一起恢复。\n"
                  "★ 可以填 1 – 240 之间的整数。\n\n"
                  "当前设置：%s" % (hk, cur))
        self._input_busy = True
        self._input_mode = "autohide"
        self._input_result = None
        self._set_topmost(False)
        threading.Thread(target=self._input_worker,
                         args=(prompt, "自动隐蔽设置"), daemon=True).start()

    def _apply_auto_hide(self, s):
        hk = self._hotkey_label("toggle_hide")
        if not s or not s.strip():
            return
        try:
            n = int(s.strip())
        except ValueError:
            self._msgbox("请输入 0 – 240 之间的整数（0 = 关闭自动隐蔽）。")
            return
        n = max(0, min(240, n))
        self.auto_hide_minutes = n
        self.cfg["auto_hide_minutes"] = n
        save_config(self.cfg)
        self._cfg_mtime = self._config_mtime()
        self._auto_next = self._auto_deadline()
        if n:
            self._msgbox("已设置：每 %d 分钟自动隐藏。\n\n显示回来请按 %s。\n\n"
                         "（主窗口和 K 线窗口会一起隐藏、一起恢复）" % (n, hk))
        else:
            self._msgbox("已关闭自动隐蔽。")

    def _cycle_theme(self):
        order = list(THEME_ORDER)
        if isinstance(self.cfg.get("custom_theme"), dict):
            order.append("custom")
        try:
            idx = order.index(self.theme_name)
        except ValueError:
            idx = -1
        self.theme_name = order[(idx + 1) % len(order)]
        self.colors = self._theme_colors(self.theme_name)
        self.cfg["theme"] = self.theme_name
        save_config(self.cfg)
        self._invalidate_charts()
        self._cfg_mtime = self._config_mtime()
        # 透明主题换了字体抗锯齿方式，并且要重设"透明键色"，所以得重建字体 + 重设层属性
        self._rebuild_fonts()
        self._apply_layered()
        self._invalidate()

    def _set_name_style(self, style):
        """切换名称显示形态：pinyin（拼音首字母大写）/ full（完整名称）。"""
        self.name_style = style if style in ("pinyin", "full") else "pinyin"
        self.cfg["name_style"] = self.name_style
        save_config(self.cfg)
        self._cfg_mtime = self._config_mtime()
        self._invalidate()

    def _toggle_name_style(self):
        self._set_name_style("full" if self.name_style == "pinyin" else "pinyin")

    def _set_pct_mode(self, mode):
        """切换涨跌幅显示模式：stealth=灰黑 0.xxxx，normal=红绿百分比。"""
        self.pct_mode = mode if mode in (PCT_STEALTH, PCT_NORMAL) else PCT_STEALTH
        self.cfg["pct_mode"] = self.pct_mode
        save_config(self.cfg)
        self._cfg_mtime = self._config_mtime()
        self._invalidate()

    def _toggle_pct_mode(self):
        self._set_pct_mode(PCT_NORMAL if self.pct_mode == PCT_STEALTH else PCT_STEALTH)

    # ---- 外观调节面板（三个滑块：背景透明度 / 字体透明度 / 字号）----
    def _slider_rows(self):
        """三个滑块的当前状态：(key, 标签, 当前值, 下限, 上限, 显示方式)。"""
        cur = {"bg_opacity": self.bg_opacity,
               "font_opacity": self.font_opacity,
               "font_scale": self.font_scale}
        return [(key, label, float(cur[key]), lo, hi, kind)
                for key, label, lo, hi, kind in SLIDER_DEFS]

    def _slider_geom(self):
        """每个滑块的 (key, 标签, 轨道左, 轨道右, 行上沿, 行下沿, 值, 下限, 上限, 显示方式)。"""
        tx0 = self._S(44)
        tx1 = max(tx0 + self._S(24), self.width - self._S(48))
        by = self.header_h + self._S(2) + self.status_h
        out = []
        for i, (key, label, cur, lo, hi, kind) in enumerate(self._slider_rows()):
            y0 = by + i * self.row_h
            out.append((key, label, tx0, tx1, y0, y0 + self.row_h, cur, lo, hi, kind))
        return out

    def _slider_hit(self, x, y):
        """命中哪个滑块（整行可点，标签右侧都算，容错高一些）。"""
        for key, _lb, tx0, _tx1, y0, y1, _c, _lo, _hi, _k in self._slider_geom():
            if y0 <= y < y1 and x >= tx0 - self._S(10):
                return key
        return None

    def _slider_set(self, key, x):
        """把鼠标 x 换算成滑块值（拖动过程中实时生效，松手才写盘）。"""
        for k, _lb, tx0, tx1, _y0, _y1, _c, lo, hi, _kind in self._slider_geom():
            if k != key:
                continue
            frac = (x - tx0) / float(max(1, tx1 - tx0))
            frac = max(0.0, min(1.0, frac))
            val = lo + frac * (hi - lo)
            if key == "font_scale":
                self.font_scale = max(lo, min(hi, round(val * 50) / 50.0))
                self.cfg["font_scale"] = self.font_scale
                self._rebuild_fonts()
            elif key == "bg_opacity":
                self.bg_opacity = int(max(lo, min(hi, round(val))))
                self.cfg["bg_opacity"] = self.bg_opacity
                self._apply_layered()
            else:
                self.font_opacity = int(max(lo, min(hi, round(val))))
                self.cfg["font_opacity"] = self.font_opacity
            self._invalidate_charts()
            self._invalidate()
            return

    def _save_appearance(self):
        self.cfg["font_scale"] = float(self.font_scale)
        self.cfg["bg_opacity"] = int(self.bg_opacity)
        self.cfg["font_opacity"] = int(self.font_opacity)
        save_config(self.cfg)
        self._cfg_mtime = self._config_mtime()
        log("appearance saved: bg=%d%% font=%d%% scale=%.2f"
            % (self.bg_opacity, self.font_opacity, self.font_scale))

    def _resize_to_layout(self):
        self._layout()
        if self.hwnd:
            user32.SetWindowPos(self.hwnd, None, 0, 0, self.width, self.height,
                                SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)

    def _open_panel(self):
        if self.collapsed:
            self._toggle_collapse()      # 先展开，折叠状态没地方放滑块
        self.panel_open = True
        self._resize_to_layout()
        self._invalidate()

    def _close_panel(self):
        if not self.panel_open:
            return
        if self._drag_slider:
            self._drag_slider = None
            try:
                user32.ReleaseCapture()
            except Exception:
                pass
            self._save_appearance()
        self.panel_open = False
        self._resize_to_layout()
        self._invalidate()

    def _set_topmost(self, on):
        """只做"置顶样式"的切换（不改配置）。"""
        if not self.hwnd:
            return
        try:
            # SetWindowPos 的 HWND_TOPMOST / HWND_NOTOPMOST 会自动增删 WS_EX_TOPMOST 样式
            user32.SetWindowPos(self.hwnd,
                                HWND_TOPMOST if on else HWND_NOTOPMOST,
                                0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception as e:
            log("set topmost failed: %r" % e)
        # K 线窗口跟着主窗口一起置顶/取消置顶，否则它们会立刻被别的窗口盖住
        for w in list(_charts):
            try:
                if w.hwnd:
                    user32.SetWindowPos(w.hwnd,
                                        HWND_TOPMOST if on else HWND_NOTOPMOST,
                                        0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
            except Exception:
                pass

    def _toggle_top(self):
        self.always_on_top = not self.always_on_top
        self.cfg["always_on_top"] = self.always_on_top
        save_config(self.cfg)
        self._cfg_mtime = self._config_mtime()
        self._set_topmost(self.always_on_top)

    def _toggle_collapse(self):
        if self.panel_open:
            # 面板开着的时候双击标题栏 = 退出外观调节（比折叠更符合直觉）
            self._close_panel()
            return
        self.collapsed = not self.collapsed
        self.cfg.setdefault("window", {})["collapsed"] = self.collapsed
        save_config(self.cfg)
        self._cfg_mtime = self._config_mtime()
        self._layout()
        if self.hwnd:
            user32.SetWindowPos(self.hwnd, None, 0, 0, self.width, self.height,
                                SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
        self._invalidate()

    def _save_window_pos(self):
        if not self.hwnd:
            return
        r = RECT()
        if user32.GetWindowRect(self.hwnd, ctypes.byref(r)):
            self.cfg.setdefault("window", {})["x"] = r.left
            self.cfg["window"]["y"] = r.top
            save_config(self.cfg)
            self._cfg_mtime = self._config_mtime()

    def _move_home(self):
        """把窗口搬回主屏工作区右下角（万一被挪到屏幕外、找不到时用）。"""
        if not self.hwnd:
            return
        x, y = default_window_pos(self.width, self.height, self._S(24))
        user32.SetWindowPos(self.hwnd,
                            HWND_TOPMOST if self.always_on_top else HWND_NOTOPMOST,
                            x, y, 0, 0, SWP_NOSIZE | SWP_NOACTIVATE)
        user32.ShowWindow(self.hwnd, SW_SHOW)
        self.visible = True
        self._save_window_pos()
        self._invalidate()

    def _msgbox(self, text, title=APP_NAME):
        try:
            user32.MessageBoxW(None, text, title, 0x40)  # MB_OK | MB_ICONINFORMATION
        except Exception:
            pass

    def _edit_watchlist(self):
        """点「＋」或菜单里的「添加 / 删除自选股…」时调用（UI 线程）。

        弹框交给后台线程去做：ps_input_box 会一直阻塞到用户点确定/取消为止，
        如果直接在 UI 线程里等，窗口在这段时间内不处理任何消息，
        看起来就是"程序崩溃 / 未响应"（也正因为如此，输入框被隐藏时用户会
        完全无感，只觉得一按就死）。结果通过 WM_APP_INPUT 回到 UI 线程应用。
        """
        if self._input_busy:
            return                        # 已经有一个输入框在等了，别叠第二个
        cur = "、".join(w["code"] for w in self.watchlist) or "（空）"
        prompt = ("输入股票代码以添加（空格 / 逗号分隔）：\n"
                  "例如：600519 000001 sz300750\n"
                  "加 - 前缀表示删除，例如：-600519\n\n"
                  "当前自选股：\n" + cur)
        self._input_busy = True
        self._input_result = None
        # FOMO 是置顶窗，输入框若被压在下面会"点了没反应" —— 弹框期间临时摘掉置顶，弹完恢复
        self._set_topmost(False)
        threading.Thread(target=self._input_worker, args=(prompt,), daemon=True).start()

    def _input_worker(self, prompt, title="编辑自选股"):
        """后台线程：弹输入框、拿结果，然后喊 UI 线程来收。"""
        result = None
        try:
            result = ps_input_box(prompt, title)
        except Exception as e:
            log("input dialog failed: %r" % e)
        self._input_result = result
        if self.hwnd:
            try:
                user32.PostMessageW(self.hwnd, WM_APP_INPUT, 0, 0)
            except Exception:
                pass

    def _on_watchlist_input(self):
        """UI 线程：应用后台线程带回来的输入（自选股 / 自动隐蔽两种模式）。"""
        self._input_busy = False
        self._set_topmost(self.always_on_top)
        s = self._input_result
        self._input_result = None
        mode = getattr(self, "_input_mode", "watchlist")
        self._input_mode = "watchlist"
        if mode == "autohide":
            self._apply_auto_hide(s)
            return
        if mode == "alert":
            self._apply_alert_form(s)
            return
        if not s:
            return
        added, removed = self._apply_watchlist_input(s)
        if added:
            self.ach.data["ever_added"] = True
            self.ach.save()
        if added or removed:
            self._msgbox("自选股已更新：新增 %d 只，删除 %d 只，当前共 %d 只。"
                         % (added, removed, len(self.watchlist)))
        else:
            self._msgbox("未发生变化（代码无效或已存在）。")

    def _apply_watchlist_input(self, text):
        text = (text or "").replace(",", " ").replace("，", " ").replace(";", " ") \
                   .replace("；", " ").replace("\n", " ").replace("\t", " ").strip()
        if not text:
            return 0, 0
        wl = [dict(w) for w in self.watchlist]
        codes = [w["code"] for w in wl]
        added = removed = 0
        for t in text.split():
            t = t.strip()
            if not t:
                continue
            delete = t.startswith("-")
            if delete:
                t = t[1:].strip()
            nc = normalize_code(t)
            if not nc:
                continue
            if delete:
                if nc in codes:
                    wl = [w for w in wl if w["code"] != nc]
                    codes = [w["code"] for w in wl]
                    removed += 1
            else:
                if nc not in codes:
                    wl.append({"code": nc, "alias": None})
                    codes.append(nc)
                    added += 1
        if added == 0 and removed == 0:
            return 0, 0
        self.cfg["watchlist"] = wl
        self.watchlist = wl
        self.quotes = []   # 清空旧行情，避免与新的行数/顺序短暂错位
        save_config(self.cfg)
        self._cfg_mtime = self._config_mtime()
        self._layout()
        if self.hwnd:
            user32.SetWindowPos(self.hwnd, None, 0, 0, self.width, self.height,
                                SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
        self._invalidate()
        if self.client:
            self._wake.set()
        log("watchlist updated: +%d -%d -> %d" % (added, removed, len(wl)))
        return added, removed

    def _apply_hotkeys(self):
        if not self.hwnd:
            return
        for hid in (HK_HIDE, HK_THEME, HK_TOP, HK_REFRESH, HK_NAME):
            user32.UnregisterHotKey(self.hwnd, hid)
        hk = self.cfg.get("hotkeys") or {}
        for hid, key in ((HK_HIDE, "toggle_hide"), (HK_THEME, "cycle_theme"),
                         (HK_TOP, "toggle_top"), (HK_REFRESH, "refresh_now"),
                         (HK_NAME, "cycle_name")):
            mods, vk = parse_hotkey(hk.get(key))
            if vk is None:
                continue
            if not user32.RegisterHotKey(self.hwnd, hid, mods | MOD_NOREPEAT, vk):
                log("RegisterHotKey failed: %s" % hk.get(key))

    # ---------------- 数据 ----------------
    def _start_worker(self):
        t = threading.Thread(target=self._worker, daemon=True)
        t.start()

    def _worker(self):
        while not self._stop.is_set():
            try:
                self._do_fetch()
            except Exception as e:
                log("worker error: %r" % e)
            interval = self._interval()
            self._wake.wait(timeout=interval)
            self._wake.clear()

    def _interval(self):
        return {"fast": self.cfg.get("refresh_fast", 3),
                "mid": self.cfg.get("refresh_midday", 15),
                "slow": self.cfg.get("refresh_slow", 30)}.get(self.interval_key, 30)

    def _do_fetch(self):
        codes = [w["code"] for w in self.watchlist]
        if not codes:
            self.quotes = []
            self._post_refresh()
            return
        try:
            data = self.client.fetch(codes)
            if data is None:
                # 未配置数据源 / provider 加载失败或抛异常：保留上一次数值，不崩
                self.fetch_error = (self.client.state == "error")
                self._post_refresh()
                return
            self.quotes = [data.get(c) for c in codes]
            self.fetch_error = False
            self._check_alerts()
            try:
                self._ach_check_quotes()
            except Exception as _e:
                log("ach check failed: %r" % _e)
            self.last_ok_str = datetime.now().strftime("%H:%M:%S")
            qt = next((q.get("qtime") for q in self.quotes
                       if q and q.get("qtime")), None)
            self.data_qtime = None
            if qt and len(qt) >= 14:
                try:
                    self.data_qtime = datetime.strptime(qt[:14], "%Y%m%d%H%M%S")
                except ValueError:
                    self.data_qtime = None
        except Exception as e:
            self.fetch_error = True
            log("fetch error: %r" % e)
        self._post_refresh()

    def _on_data_refreshed(self):
        self._invalidate()

    def _on_tick(self):
        label, key = market_status()
        changed = False
        if label != self.status_label:
            self.status_label = label
            changed = True
        if key != self.interval_key:
            self.interval_key = key
            changed = True

        # 时钟：每秒走一格（原来这里是"最后一次取数成功的时间"，只在刷新时变）
        now_s = datetime.now().strftime("%H:%M:%S")
        if now_s != self.clock_str:
            self.clock_str = now_s
            changed = True
        # 行情延迟检测（交易时段数据源超过 2 分钟无更新）
        stale = False
        if self.data_qtime and self.interval_key == "fast":
            age = (datetime.now() - self.data_qtime).total_seconds()
            stale = age > 120
        if stale != self.stale:
            self.stale = stale
            changed = True
        # 成就累计（摸鱼时长 / 当日状态）
        try:
            self._ach_tick(0.25, datetime.now())
        except Exception as _e:
            log("ach tick failed: %r" % _e)
        try:
            AchievementToast.pump()
        except Exception as _e:
            log("toast pump failed: %r" % _e)
        # 配置文件热加载
        if self._maybe_reload_config():
            changed = True
        # 自动隐蔽：到点就把所有窗口一起藏起来（只有热键能叫回来）
        if self.auto_hide_minutes:
            now = datetime.now().timestamp()
            if self._auto_next is None:
                self._auto_next = now + self.auto_hide_minutes * 60
            elif now >= self._auto_next:
                if self.visible or any(not getattr(w, "_hidden_by_auto", True) for w in _charts):
                    self._auto_hide_all()
                else:
                    self._auto_next = now + self.auto_hide_minutes * 60
        else:
            self._auto_next = None
        if changed:
            self._invalidate()

    def _maybe_reload_config(self):
        m = self._config_mtime()
        if m is None or m == self._cfg_mtime:
            return False
        self._cfg_mtime = m
        old = self.cfg
        new = load_config()
        # 保留当前窗口位置，避免与拖动互相覆盖
        new["window"] = dict(old.get("window") or {})
        self.cfg = new
        self.watchlist = new["watchlist"]
        self.quotes = []   # 自选可能变化，清旧行情避免错位
        self.font_scale = float(new.get("font_scale", 1.0) or 1.0)
        self.bg_opacity = int(new.get("bg_opacity", 100) or 100)
        self.font_opacity = int(new.get("font_opacity", 100) or 100)
        self.pct_mode = new.get("pct_mode", PCT_STEALTH)
        self.always_on_top = bool(new.get("always_on_top", True))
        self.name_style = new.get("name_style", "pinyin")
        self.show_code = bool(new.get("show_code", True))
        self.auto_hide_minutes = int(new.get("auto_hide_minutes") or 0)
        self._auto_next = self._auto_deadline()
        if new.get("theme") in THEMES or new.get("theme") == "custom":
            self.theme_name = new["theme"]
        self.colors = self._theme_colors(self.theme_name)
        self._rebuild_fonts()
        self._apply_layered()
        if (old.get("provider") or {}) != (new.get("provider") or {}):
            self.client = ProviderClient(new.get("provider") or {})
        old_h, old_w = self.height, self.width
        self._layout()
        if self.hwnd and (self.height != old_h or self.width != old_w):
            user32.SetWindowPos(self.hwnd, None, 0, 0, self.width, self.height,
                                SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE)
        self._apply_hotkeys()
        if self.always_on_top and self.hwnd:
            user32.SetWindowPos(self.hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        self._wake.set()
        self._invalidate_charts()
        return True

    # ---------------- 绘制 ----------------
    def _hover_color(self, header):
        """菜单栏按钮的 hover 底色：浅色菜单栏往深里变、深色的往亮里变。"""
        hb, hg, hr = (header >> 16) & 255, (header >> 8) & 255, header & 255
        step = -16 if (hr * 299 + hg * 587 + hb * 114) // 1000 > 140 else 18
        cl = lambda v: max(0, min(255, v))
        return (cl(hb + step) << 16) | (cl(hg + step) << 8) | cl(hr + step)

    def _fmt_pct(self, q, t):
        """涨跌幅的文案 + 配色，由 pct_mode 决定两种形态。

        隐蔽模式：只用灰/黑的 0.xxxx 小数（0.0081 = 涨 0.81%），旁边看得见也看不出涨跌。
        正常模式：红涨绿跌 + 百分比。

        两种模式都用主题里的相对色（up/down/text/sub），不写死 RGB ——
        否则深灰主题下会出现"黑字贴在黑底上"。
        """
        if not q or q.get("pct") is None:
            return "--", t["sub"]
        pv = q["pct"]
        if self.pct_mode == PCT_NORMAL:
            txt = "%+.2f%%" % pv
            if pv > 0.0001:
                return txt, t["up"]
            if pv < -0.0001:
                return txt, t["down"]
            return txt, t["sub"]
        return "%+.4f" % (pv / 100.0), (t["text"] if pv < -0.0001 else t["sub"])

    def _draw(self, memdc, w, h, t):
        """把整个组件画到 memdc 上。

        t 是一套调色板：普通主题直接用 self.colors；透明主题会画两遍
        （一遍出颜色、一遍出透明度），所以这里所有颜色都必须从 t 里取，不能直接用 self.colors。
        """
        self._fill(memdc, 0, 0, w, h, t["bg"])
        if not self.collapsed:
            body_y = self.header_h + self._S(2)
            body_h = h - body_y - self._S(1)
            if body_h > 0:
                self._fill(memdc, 1, body_y, w - 2, body_h, t["panel"])

        # 菜单栏
        self._fill(memdc, 0, 0, w, self.header_h, t["header"])
        self._fill(memdc, 0, self.header_h - self._S(1), w, self._S(1), t["border"])
        self._text(memdc, self._S(10), 0, self._S(184), self.header_h,
                   APP_TITLE, self.f_title, t["header_text"], DT_LEFT | DT_VCENTER | DT_SINGLELINE)

        # 右上角按钮
        for bid, (bx, by, bw, bh, label) in self.btn_rects.items():
            if self.hover_btn == bid or self.press_btn == bid:
                self._fill(memdc, bx, by, bw, bh, t["hover"])
            color = t["up"] if bid == "close" else t["header_sub"]
            self._text(memdc, bx, by, bw, bh, label, self.f_small, color,
                       DT_CENTER | DT_VCENTER | DT_SINGLELINE)

        if not self.collapsed:
            # 状态栏（外观面板打开时，这一行让给面板说明）
            sy = self.header_h + self._S(2)
            if self.panel_open:
                self._text(memdc, self._S(8), sy, w - self._S(16), self.status_h,
                           "外观调节 · 拖动滑块，点右上角 ✓ 完成",
                           self.f_small, t["sub"], DT_LEFT | DT_VCENTER | DT_SINGLELINE)
            else:
                dot_color = t["up"] if self.fetch_error else t["sub"]
                self._text(memdc, self._S(8), sy, self._S(12), self.status_h,
                           "●", self.f_small, dot_color, DT_LEFT | DT_VCENTER | DT_SINGLELINE)
                left = self.status_label
                if self.fetch_error:
                    left = "连接失败 · 重试中"
                self._text(memdc, self._S(20), sy, self._S(120), self.status_h,
                           left, self.f_small, t["text"], DT_LEFT | DT_VCENTER | DT_SINGLELINE)
                if self.client.state == "no_source":
                    src = "No source"
                elif self.client.state == "error":
                    src = "Source error"
                else:
                    src = self.client.name
                right = "%s · %s" % (self.clock_str, src)
                if self.stale:
                    right += " · 延迟"
                self._text(memdc, self._S(150), sy, w - self._S(158), self.status_h,
                           right, self.f_small, t["sub"],
                           DT_RIGHT | DT_VCENTER | DT_SINGLELINE | DT_END_ELLIPSIS)

            if self.panel_open:
                self._draw_sliders(memdc, w, t)
            else:
                # 行情行（以自选股为准，行情数组可能因增删短暂不同步）
                # 透明主题右下角有缩放手柄，价格列往左让出一点，避免压字
                right_pad = self._D(16) if self.transparent else self._S(10)
                ry = sy + self.status_h
                for i, wl_item in enumerate(self.watchlist):
                    q = self.quotes[i] if i < len(self.quotes) else None
                    y0 = ry + i * self.row_h
                    if i > 0:
                        self._fill(memdc, self._S(10), y0, w - self._S(20), self._S(1), t["border"])
                    # 整行交给 _paint_row：内部按需铺异动底色、画字、叠故障特效
                    self._paint_row(memdc, w, wl_item, q, y0, right_pad, t)
                if self._drag_row is not None:
                    ty2 = self._rows_top() + self._drag_to * self.row_h
                    self._fill(memdc, 1, ty2 - 1, w - 2, 2, t["up"])
                # 底部小栏：最后取数时间
                self._draw_footer(memdc, w, h, t)

        # 外框：普通主题 1px；透明主题 4px（半透明的一圈淡线，兼作抓取区）
        b = self._D(4) if self.transparent else self._D(1)
        self._fill(memdc, 0, 0, w, b, t["border"])
        self._fill(memdc, 0, h - b, w, b, t["border"])
        self._fill(memdc, 0, 0, b, h, t["border"])
        self._fill(memdc, w - b, 0, b, h, t["border"])

        # 透明主题：右下角补一块实心三角当缩放手柄
        if self.transparent:
            g = self._D(14)
            step = max(1, self._D(2))
            for i in range(max(1, g // step)):
                rw = g - i * step
                self._fill(memdc, w - rw, h - (i + 1) * step, rw, step, t["grip"])

    def _draw_sliders(self, memdc, w, t):
        """外观调节面板：三行「标签 · 滑轨 · 数值」。

        滑轨用主题里的相对色画，并且**不随"字体透明度"变淡** ——
        否则把字体调到 10% 之后，滑块本身也跟着看不见，就没法调回来了。
        """
        track = t.get("track", t["border"])
        fillc = t.get("fill", t["sub"])
        knobc = t.get("knob", t["text"])
        th = max(1, self._S(2))
        for _key, label, tx0, tx1, y0, y1, cur, lo, hi, _kind in self._slider_geom():
            cy = (y0 + y1) // 2
            frac = 0.0 if hi <= lo else (cur - lo) / float(hi - lo)
            frac = max(0.0, min(1.0, frac))
            kx = tx0 + int(round(frac * (tx1 - tx0)))
            self._text(memdc, self._S(10), y0, self._S(34), y1 - y0, label,
                       self.f_body, t["text"], DT_LEFT | DT_VCENTER | DT_SINGLELINE)
            self._fill(memdc, tx0, cy - th // 2, tx1 - tx0, th, track)
            if kx > tx0:
                self._fill(memdc, tx0, cy - th // 2, kx - tx0, th, fillc)
            kw = max(2, self._S(3))
            kh = max(8, self._S(13))
            self._fill(memdc, kx - kw // 2, cy - kh // 2, kw, kh, knobc)
            # 透明度本来就是百分数，字号是 0.6~1.8 的倍率 —— 两种显示方式不一样
            txt = ("%d%%" % int(round(cur * 100))) if _kind == "x" else ("%d%%" % int(round(cur)))
            vx = tx1 + self._S(6)
            self._text(memdc, vx, y0, max(1, w - self._S(8) - vx), y1 - y0,
                       txt, self.f_small, t["sub"],
                       DT_RIGHT | DT_VCENTER | DT_SINGLELINE)

    def _dib(self, hdc, w, h):
        """建一张 32 位、自上而下的 DIB 当画布，返回 (dc, 位图句柄, 原句柄, 像素指针)。"""
        bi = BITMAPINFO()
        bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.bmiHeader.biWidth = w
        bi.bmiHeader.biHeight = -h          # 负数 = 自上而下，内存行序和屏幕一致
        bi.bmiHeader.biPlanes = 1
        bi.bmiHeader.biBitCount = 32
        bi.bmiHeader.biCompression = 0      # BI_RGB
        bits = ctypes.c_void_p()
        bmp = gdi32.CreateDIBSection(hdc, ctypes.byref(bi), DIB_RGB_COLORS,
                                     ctypes.byref(bits), None, 0)
        memdc = gdi32.CreateCompatibleDC(hdc)
        old = gdi32.SelectObject(memdc, bmp)
        return memdc, bmp, old, bits

    def _faded(self, t):
        """普通主题的「字体透明度」：底色是不透明实色时，
        "60% 不透明的字"和"把字色按 60% 混向底色"看起来完全一样，
        所以这里用混色等价实现，不必为了文字 alpha 把整套渲染换成逐像素合成。
        （透明主题背景没有实色，只能走真 alpha，在 _paint_layered 里单独处理。）
        """
        fo = max(0.0, min(1.0, self.font_opacity / 100.0))
        if fo >= 0.999:
            return t
        def mix(c, bg):
            cb, cg, cr = (c >> 16) & 255, (c >> 8) & 255, c & 255
            bb, bgc, br = (bg >> 16) & 255, (bg >> 8) & 255, bg & 255
            f = lambda a, b: int(round(b + (a - b) * fo))
            return (f(cb, bb) << 16) | (f(cg, bgc) << 8) | f(cr, br)
        t = dict(t)
        t["header_text"] = mix(t["header_text"], t["header"])
        t["header_sub"] = mix(t["header_sub"], t["header"])
        for k in ("text", "sub", "up", "down"):
            t[k] = mix(t[k], t["panel"])
        # 滑块控件的轨道/圆钮不参与变淡：否则字体调到极淡后，滑块本身也看不见，没法调回来
        return t

    def _paint_plain(self, hdc, w, h):
        """普通主题：画完 BitBlt 到窗口 DC。"""
        memdc = gdi32.CreateCompatibleDC(hdc)
        bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
        old = gdi32.SelectObject(memdc, bmp)
        t = dict(self.colors)
        t["hover"] = self._hover_color(self.colors["header"])
        self._draw(memdc, w, h, self._faded(t))
        gdi32.BitBlt(hdc, 0, 0, w, h, memdc, 0, 0, SRCCOPY)
        gdi32.SelectObject(memdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(memdc)

    def _paint_layered(self, hwnd, hdc, w, h):
        """透明主题：逐像素 alpha。

        画两张 32 位图再合成：
          颜色图 —— 背景涂纯黑（= 完全透明），菜单栏/边框/手柄涂"按 alpha 预乘过的颜色"，
                    文字照常画（GDI 抗锯齿会把它和底色线性混合，得到的正好是预乘值）
          透明度图 —— 背景涂黑（alpha 0），菜单栏/边框/手柄涂各自的 alpha 灰度，文字涂纯白
                     （GDI 抗锯齿算出的灰度就是覆盖率，正好是 alpha）
        两者按像素拼成 BGRA 交给 UpdateLayeredWindow。这样菜单栏可以是一层薄纱而不是
        一个硬方块，文字边缘的 alpha 也是干净的（不会有 COLORKEY 那种黑色毛边）。
        """
        base = self.colors
        k = max(0.0, min(1.0, self.bg_opacity / 100.0))
        ta = int(round(255.0 * max(0, min(100, self.font_opacity)) / 100.0))
        ah = int(round(self.alpha_header * k))
        ab = int(round(self.alpha_border * k))
        ag = int(round(self.alpha_grip * k))
        # 鼠标移进窗口时把轮廓提亮：平时只是一圈很淡的影，不至于形成一个"框"，
        # 要找窗口边界或拖边框缩放时又看得见
        if self.window_hover:
            ab = max(ab, int(round(ALPHA_BORDER_HOVER * k)))
        hover = self._hover_color(base["header"])
        # 滑块控件的可见度：跟随背景透明度，但不受"字体透明度"影响（不然调淡后没法调回来）
        sa = max(ah, int(round(ALPHA_PANEL * k)))

        col = dict(base)
        col["bg"] = col["panel"] = 0
        col["header"] = premult_color(base["header"], ah)
        col["border"] = premult_color(base["border"], ab)
        col["grip"] = premult_color(base["grip"], ag)
        col["hover"] = premult_color(hover, ah)
        # 文字：真 alpha —— 颜色按 ta 预乘，遮罩里也按 ta 上灰度，
        # GDI 抗锯齿两处都按同一个覆盖率缩放，最后得到的正好是 (字色 × ta × 覆盖率)
        for kk in ("text", "sub", "header_text", "header_sub", "up", "down"):
            col[kk] = premult_color(base[kk], ta)
        col["track"] = premult_color(base["border"], sa)
        col["fill"] = premult_color(base["sub"], sa)
        col["knob"] = premult_color(base["text"], sa)
        if self.panel_open:
            # 面板打开时给"身体"垫一层薄纱：透明主题下这块本来是 alpha=0 的，
            # 不垫的话鼠标直接穿过去，滑块根本点不到
            col["panel"] = premult_color(base["header"], sa)

        mask = {kk: alpha_gray(ta) for kk in col}
        mask["bg"] = 0
        mask["header"] = mask["hover"] = alpha_gray(ah)
        mask["border"] = alpha_gray(ab)
        mask["grip"] = alpha_gray(ag)
        mask["track"] = mask["fill"] = mask["knob"] = alpha_gray(sa)
        mask["panel"] = alpha_gray(sa) if self.panel_open else 0

        col_dc, col_bmp, col_old, col_bits = self._dib(hdc, w, h)
        msk_dc, msk_bmp, msk_old, msk_bits = self._dib(hdc, w, h)
        try:
            self._draw(col_dc, w, h, col)
            self._draw(msk_dc, w, h, mask)
            n = w * h * 4
            buf = bytearray(ctypes.string_at(col_bits, n))
            # DIB 内存是 BGRA；透明图的灰度写在 B 通道上，正好当 alpha 用
            buf[3::4] = ctypes.string_at(msk_bits, n)[0::4]
            ctypes.memmove(col_bits, bytes(buf), n)
            size = wintypes.SIZE(w, h)
            pt = POINT(0, 0)
            blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
            if not user32.UpdateLayeredWindow(hwnd, None, None, ctypes.byref(size), col_dc,
                                              ctypes.byref(pt), 0, ctypes.byref(blend), ULW_ALPHA):
                log("UpdateLayeredWindow failed err=%s" % ctypes.GetLastError())
        finally:
            for dc, bmp, old in ((col_dc, col_bmp, col_old), (msk_dc, msk_bmp, msk_old)):
                gdi32.SelectObject(dc, old)
                gdi32.DeleteObject(bmp)
                gdi32.DeleteDC(dc)

    def _on_paint(self, hwnd):
        ps = PAINTSTRUCT()
        hdc = user32.BeginPaint(hwnd, ctypes.byref(ps))
        try:
            w, h = max(1, self.width), max(1, self.height)
            if self.transparent:
                self._paint_layered(hwnd, hdc, w, h)
            else:
                self._paint_plain(hdc, w, h)
        finally:
            user32.EndPaint(hwnd, ctypes.byref(ps))


def _fatal_dialog(tb):
    """启动失败时，必须让用户**看得见**。

    本程序用 pythonw.exe 启动（无控制台）。pythonw 的进程没有 stderr：
    异常既不会打印到任何地方、也不会自动弹窗，进程直接消失 ——
    用户看到的就只是「双击了一下，什么都没发生」，无从判断原因。
    所以这里主动弹一个对话框，把最内层异常和日志路径直接摆到他面前。
    """
    last = ""
    for line in reversed(tb.strip().splitlines()):
        if line.strip():
            last = line.strip()
            break
    text = ("FOMO 启动失败，窗口没能显示出来。\n\n"
            "%s\n\n"
            "完整信息已写入日志：\n%s\n\n"
            "常见原因：watch_config.json 内容损坏、程序目录缺少文件、"
            "或运行库缺失。" % (last, LOG_PATH))
    try:
        user32.MessageBoxW(None, text, "FOMO · 启动失败", 0x10)  # MB_OK | MB_ICONERROR
    except Exception:
        pass                          # 连弹窗都失败就只能靠日志了


def main():
    # 单实例检测
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        existing = user32.FindWindowW(CLASS_NAME, None)
        if existing:
            # 重复启动时不仅是"唤起"，还会检查窗口是否跑到屏幕外并搬回来
            bring_into_view(existing)
        return 0
    try:
        app = FOMO(load_config())
        return app.run()
    except BaseException:
        # 注意：这里不能再 raise —— pythonw 下 raise 等于"静默死亡"
        tb = traceback.format_exc()
        log("fatal:\n" + tb)
        _fatal_dialog(tb)
        return 1


if __name__ == "__main__":
    main()
