# -*- coding: utf-8 -*-
"""
SRun 深澜校园网自动认证（通用版）
- 支持任意使用深澜 SRun portal 的校园网（配置 host / 账号@运营商域名 / ac_id）
- 有线优先：插着网线时优先用有线网卡认证，没网线用无线
- 配合 install_task.ps1 可实现事件驱动全自动认证（插线/拔线/开机/唤醒自动触发）
- 支持 --watch 保活：掉线自动重连（解决多设备互顶）

用法:
    python srun_auth.py            # 检查状态，掉了就认证
    python srun_auth.py --login    # 强制重新认证
    python srun_auth.py --status   # 只查状态
    python srun_auth.py --watch    # 后台保活（掉线自动重连）
    python srun_auth.py --adapter "以太网"  # 指定网卡

配置（任选其一）:
    1. 在同目录创建 config.json（推荐，见 config.example.json）
    2. 命令行参数 --host / --username / --password / --ac-id
"""
import sys, os, json, hmac, time, base64, hashlib, argparse, subprocess, re
import http.client, urllib.parse, socket

QUIET = False
LOG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth.log")

def log(msg):
    line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    if not QUIET:
        print(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

# 默认占位，实际值来自 config.json 或命令行参数（见 load_config）
HOST = ""
USERNAME = ""
PASSWORD = ""
AC_ID = "2"
OS_NAME, PLATFORM = "Windows 10", "Windows"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

def load_config(args):
    """config.json < 命令行参数；缺少账号/密码/portal 地址时直接退出并提示"""
    global HOST, USERNAME, PASSWORD, AC_ID
    cfg = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception as e:
            sys.exit(f"config.json 解析失败: {e}")
    HOST = args.host or cfg.get("host") or HOST
    USERNAME = args.username or cfg.get("username") or USERNAME
    PASSWORD = args.password or cfg.get("password") or PASSWORD
    AC_ID = str(args.ac_id or cfg.get("ac_id") or AC_ID)
    if not HOST:
        sys.exit("未配置 portal 地址：请在 config.json 中填写 host（认证页 IP/域名），或加 --host")
    if not USERNAME or not PASSWORD:
        sys.exit("未配置账号密码：请编辑 config.json（参考 config.example.json），或用 --username/--password")
B64_ALPHA = "LVoJPiCN2R8G90yg+hmFHuacZ1OWMnrsSTXkYpUq/3dlbfKwv6xztjI7DeBE45QA"
_STD_B64 = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
TRANS = str.maketrans(_STD_B64, B64_ALPHA)

# ---------------- SRun srun_bx1 加密 ----------------

def _words(data: bytes, append_len: bool):
    v = []
    for i in range(0, len(data), 4):
        w = 0
        for j, b in enumerate(data[i:i + 4]):
            w |= b << (8 * j)
        v.append(w)
    if append_len:
        v.append(len(data))
    return v

def _bytes(v):
    out = bytearray()
    for w in v:
        for j in range(4):
            out.append((w >> (8 * j)) & 0xFF)
    return bytes(out)

def x_encode(data: bytes, key: bytes) -> bytes:
    """与 portal.js 中 srun_bx1 的 encode() 完全一致（XXTEA 变体）"""
    if not data:
        return b""
    v = _words(data, True)
    k = _words(key, False)
    n = len(v) - 1
    z, y = v[n], v[0]
    q = 6 + 52 // (n + 1)
    d = 0
    M = 0xFFFFFFFF
    while q > 0:
        q -= 1
        d = (d + 0x9E3779B9) & M
        e = (d >> 2) & 3
        for p in range(n):
            y = v[p + 1]
            m = ((z >> 5) ^ ((y << 2) & M)) & M
            m = (m + ((((y >> 3) ^ ((z << 4) & M)) & M) ^ ((d ^ y) & M))) & M
            m = (m + ((k[(p & 3) ^ e] ^ z) & M)) & M
            z = v[p] = (v[p] + m) & M
        y = v[0]
        m = ((z >> 5) ^ ((y << 2) & M)) & M
        m = (m + ((((y >> 3) ^ ((z << 4) & M)) & M) ^ ((d ^ y) & M))) & M
        m = (m + ((k[(n & 3) ^ e] ^ z) & M)) & M
        z = v[n] = (v[n] + m) & M
    return _bytes(v)

def encode_userinfo(info: dict, token: str) -> str:
    s = json.dumps(info, separators=(",", ":"))
    enc = x_encode(s.encode(), token.encode())
    return "{SRBX1}" + base64.b64encode(enc).decode().translate(TRANS)

def hmd5(password: str, token: str) -> str:
    """portal.js: md5(password, token) —— blueimp md5 的 HMAC 模式"""
    return hmac.new(token.encode(), password.encode(), hashlib.md5).hexdigest()

# ---------------- 网络请求（可绑定源 IP，区分有线/无线） ----------------

def http_get(path, params, src_ip=None, timeout=8):
    conn = http.client.HTTPConnection(HOST, 80, timeout=timeout,
                                      source_address=(src_ip, 0) if src_ip else None)
    qs = urllib.parse.urlencode(params)
    conn.request("GET", f"{path}?{qs}", headers={"User-Agent": "Mozilla/5.0", "Referer": f"http://{HOST}/"})
    r = conn.getresponse()
    body = r.read().decode("utf-8", "ignore")
    conn.close()
    return body

def jsonp(body):
    m = re.search(r"\((.*)\)", body, re.S)
    return json.loads(m.group(1)) if m else None

# ---------------- 网卡发现 ----------------

def netsh(cmd):
    r = subprocess.run(["netsh"] + cmd, capture_output=True, text=True,
                       encoding="utf-8", errors="ignore", timeout=15,
                       creationflags=0x08000000)  # CREATE_NO_WINDOW：后台运行，不弹黑框
    return r.stdout

def get_adapters():
    """返回 [{name, type, state, ip, gateway}]，有线排在无线前"""
    out = []
    # 接口状态与类型：列 = 管理状态 状态 类型 接口名称（名称可能含空格）
    st = {}
    for line in netsh(["interface", "show", "interface"]).splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[1] in ("已连接", "已断开连接", "Connected", "Disconnected"):
            st[" ".join(parts[3:])] = (parts[2], parts[1])
    # IPv4 地址与网关
    cur = None
    for line in netsh(["interface", "ipv4", "show", "addresses"]).splitlines():
        m = re.search(r'"([^"]+)"', line)
        if m and "配置" in line:
            cur = {"name": m.group(1), "ip": None, "gateway": None}
            out.append(cur)
        elif cur is not None:
            m = re.search(r"IP 地址[:：]\s*([0-9.]+)", line)
            if m and not m.group(1).startswith("127."):
                cur["ip"] = m.group(1)
            m = re.search(r"默认网关[:：]\s*([0-9.]+)", line)
            if m:
                cur["gateway"] = m.group(1)
    res = []
    for a in out:
        if not a["ip"]:
            continue
        typ, state = st.get(a["name"], ("", ""))
        connected = state in ("已连接", "Connected")
        wired = "以太网" in a["name"] or "Ethernet" in a["name"]
        # 过滤明显无关的虚拟网卡（VPN、虚拟交换机、蓝牙、回环）
        if re.search(r"VPN|vgate|VMware|VirtualBox|Hyper-V|蓝牙|Loopback|vEthernet", a["name"], re.I):
            continue
        a["type"] = "有线" if wired else ("无线" if re.search(r"WLAN|Wi-?Fi|无线", a["name"], re.I) else "其他")
        a["connected"] = connected
        a["wired"] = wired
        if connected:
            res.append(a)
    res.sort(key=lambda x: 0 if x["wired"] else 1)
    return res

# ---------------- 认证流程 ----------------

def logout(ip, src_ip=None):
    """注销指定 IP 的会话（等价于 portal 页面上点注销）"""
    body = http_get("/cgi-bin/srun_portal",
                    {"action": "logout", "username": USERNAME, "ac_id": AC_ID,
                     "ip": ip, "callback": "jQuery"}, src_ip)
    return jsonp(body)

def release_wireless(except_ip=None):
    """有线在线时，注销本机其他（无线）网卡的会话，释放设备名额。
    只动本机物理网卡、且只注销本账号的会话，绝不碰其他设备。
    注意：不绑定源 IP——此时默认路由走有线，绑定无线源 IP 的请求会被黑洞。"""
    for a in get_adapters():
        if a["wired"] or a["ip"] == except_ip:
            continue
        try:
            info = online_info(a["ip"])
        except Exception:
            continue
        if is_ours(info):
            try:
                r = logout(a["ip"])
                if r and r.get("error") == "ok":
                    log(f"[i] 有线已在线，已释放 {a['name']}({a['ip']}) 的无线会话，腾出设备名额")
                else:
                    log(f"[i] 释放 {a['name']} 无线会话失败: {r}")
            except Exception as e:
                log(f"[i] 释放 {a['name']} 无线会话异常: {e}")

def get_challenge(ip, src_ip=None):
    body = http_get("/cgi-bin/get_challenge",
                    {"username": USERNAME, "ip": ip or "", "double_stack": "0",
                     "callback": "jQuery"}, src_ip)
    return jsonp(body)

def login(ip, src_ip=None):
    ch = get_challenge(ip, src_ip)
    if not ch or "challenge" not in ch:
        return {"ok": False, "msg": f"获取 challenge 失败: {ch}"}
    token = ch["challenge"]
    ip = ip or ch.get("client_ip") or ""
    hm = hmd5(PASSWORD, token)
    info = encode_userinfo({"username": USERNAME, "password": PASSWORD, "ip": ip,
                            "acid": AC_ID, "enc_ver": "srun_bx1"}, token)
    chk = hashlib.sha1("".join([token, USERNAME, token, hm, token, AC_ID, token,
                                ip, token, "200", token, "1", token, info]).encode()).hexdigest()
    body = http_get("/cgi-bin/srun_portal", {
        "action": "login", "username": USERNAME, "password": "{MD5}" + hm,
        "os": OS_NAME, "name": PLATFORM, "nas_ip": "", "double_stack": "0",
        "chksum": chk, "info": info, "ac_id": AC_ID, "ip": ip,
        "n": "200", "type": "1", "callback": "jQuery"}, src_ip)
    res = jsonp(body) or {}
    msg = res.get("suc_msg") or res.get("error_msg") or ""
    if msg == "ip_already_online_error":
        # IP 已在线：确认是本账号的会话则视为成功，否则说明被别的账号占用
        info = online_info(ip, src_ip)
        if is_ours(info):
            return {"ok": True, "ip": ip, "already": True, "res": res}
        return {"ok": False, "ip": ip, "msg": "该 IP 已被其他账号占用在线", "res": res}
    if res.get("error") == "ok" or res.get("res") in ("ok", "success"):
        return {"ok": True, "ip": ip, "res": res}
    return {"ok": False, "ip": ip, "msg": msg or str(res)[:200], "res": res}

def online_info(ip="", src_ip=None):
    body = http_get("/cgi-bin/rad_user_info", {"ip": ip, "callback": "jQuery"}, src_ip)
    return jsonp(body)

def current_online():
    """当前默认路由出口是否已用本账号在线"""
    try:
        info = online_info()
    except Exception:
        return None
    if info and info.get("user_name"):
        return info
    return None

# ---------------- 主流程 ----------------

def is_ours(info):
    """rad_user_info 的 user_name 不带域名，需分开比对"""
    if "@" not in USERNAME:
        return bool(info) and info.get("user_name") == USERNAME
    return bool(info) and info.get("user_name") == USERNAME.split("@")[0] \
        and info.get("domain") == USERNAME.split("@")[1]

def attempt_login(force=False, adapter=None):
    ads = get_adapters()
    if adapter:
        ads = [a for a in ads if a["name"] == adapter] or ads
    if not ads:
        return {"ok": False, "msg": "没有已连接的网络适配器"}
    log("[i] 检测到网卡: " + ", ".join(f"{a['name']}({a['type']} {a['ip']})" for a in ads))
    for a in ads:
        log(f"[i] 尝试通过 {a['name']}({a['type']}, {a['ip']}) 认证...")
        try:
            # 自检：以该网卡为源 IP 请求，portal 看到的必须是同一个 IP，
            # 否则流量实际走了别的网卡（如媒体断开但路由残留），跳过
            ch = get_challenge(a["ip"], a["ip"])
            if not ch or ch.get("client_ip") != a["ip"]:
                log(f"[×] {a['name']} 流量实际未走此网卡 (portal 看到 {ch and ch.get('client_ip')})，跳过")
                continue
            info = online_info(a["ip"], a["ip"])
            if not force and is_ours(info):
                log(f"[✓] {a['name']} 已在线，账号 {info.get('user_name')}@{info.get('domain')}，无需重复认证")
                if a["wired"]:
                    release_wireless(except_ip=a["ip"])
                return {"ok": True, "ip": a["ip"], "already": True}
            r = login(a["ip"], a["ip"])
        except Exception as e:
            r = {"ok": False, "msg": f"请求异常: {e}"}
        if r.get("ok"):
            if r.get("already"):
                log(f"[✓] 已在线（{r['ip']}），无需认证")
            else:
                log(f"[✓] 认证成功！IP: {r['ip']}，出口: 中国移动")
            if a["wired"]:
                release_wireless(except_ip=a["ip"])
            return r
        log(f"[×] {a['name']} 失败: {r.get('msg')}")
    return {"ok": False, "msg": "所有网卡认证均失败"}

def do_login(force=False, adapter=None, retries=1, retry_delay=10):
    """带重试的认证（计划任务触发时网络可能尚未就绪）"""
    last = {}
    for i in range(retries):
        last = attempt_login(force, adapter)
        if last.get("ok"):
            return last
        if i < retries - 1:
            log(f"[i] {retry_delay}s 后重试 ({i + 2}/{retries})...")
            time.sleep(retry_delay)
    return last

def do_status():
    info = current_online()
    if info:
        print(f"[✓] 在线  账号: {info.get('user_name')}  IP: {info.get('online_ip') or info.get('ip')}")
        print(f"    已用流量: {info.get('sum_bytes')} B   已用时长: {info.get('sum_seconds')} s")
    else:
        print("[×] 当前未在线")

def do_watch(interval=30):
    print(f"[i] 保活模式启动，每 {interval}s 检查一次，Ctrl+C 退出")
    while True:
        try:
            info = current_online()
            if is_ours(info):
                print(f"[✓] {time.strftime('%H:%M:%S')} 在线正常 ({info.get('online_ip')})")
            else:
                print(f"[!] {time.strftime('%H:%M:%S')} 检测到掉线或被顶下线，开始重连...")
                r = do_login()
                if not r.get("ok"):
                    print(f"[×] 重连失败: {r.get('msg')}，{interval}s 后重试")
        except Exception as e:
            print(f"[×] 检查异常: {e}")
        time.sleep(interval)

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="西南民族大学校园网认证")
    p.add_argument("--login", action="store_true", help="强制重新认证")
    p.add_argument("--status", action="store_true", help="查询在线状态")
    p.add_argument("--watch", action="store_true", help="保活模式，掉线自动重连")
    p.add_argument("--interval", type=int, default=30, help="保活检查间隔秒数")
    p.add_argument("--adapter", help="指定网卡名称")
    p.add_argument("--quiet", action="store_true", help="静默模式（写日志文件，不弹输出）")
    p.add_argument("--host", help="portal 地址（IP 或域名），默认读 config.json")
    p.add_argument("--username", help="账号（可带运营商域名后缀，如 学号@cmcc）")
    p.add_argument("--password", help="密码")
    p.add_argument("--ac-id", help="ac_id 参数（默认 2，见认证页 URL）")
    p.add_argument("--retries", type=int, default=1, help="失败重试次数")
    args = p.parse_args()
    QUIET = args.quiet
    load_config(args)
    if args.status:
        do_status()
    elif args.watch:
        do_watch(args.interval)
    else:
        r = do_login(force=args.login, adapter=args.adapter,
                     retries=args.retries, retry_delay=10)
        sys.exit(0 if r.get("ok") else 1)
