# -*- coding: utf-8 -*-
"""华为运动健康 · 认证层（浏览器 + Cookie → accessToken）

链路（2026-10-22 实机验证通过）：
    1. 起一个 headless Edge/Chrome，独立 user-data-dir + remote-debugging-port
    2. 通过 CDP 把 cookie 文件里的 cookie 注入浏览器
    3. 导航到 health.cloud.huawei.com/TrainingCamp
    4. sessionStorage 无 accessToken → 点「登录」→ 自动走 OAuth
       （华为认「信任设备」，无二次验证，实测 2026-10-22）
    5. 读回 accessToken（180 字符，约 1 小时有效）
    6. 把服务端新下发的 cookie 回写文件（滚续：只要在 TTL 内用过一次就不会断）

关键坑（务必保留注释）：
    - 华为登录态的核心是 session cookie（CASTGC 等），**关浏览器即失效**，
      所以不能只靠持久化 profile，必须每次都注入 cookie 文件。
    - 但每次成功登录后服务端会下发新 cookie，回写即可滚动续期。
    - 本模块是**同步阻塞**代码，调用方必须放到线程里跑（asyncio.to_thread）。

v0.4.1 变更（对应检测报告 P1-1 / P1-9 / P1-11 / P1-12 / P2-9 / P2-15 / P2-16 / P3-4）：
    - cookie 文件用 Windows DPAPI 加密（``HWCOOK1\\0`` + 密文），旧明文文件仍可读；
    - 导入时不再整份覆盖：与磁盘上已有的 cookie 合并，并按 MIN_COOKIE_SPECS 补齐
      CASTGC 的 /CAS + /UnifiedIDMPortal 两条；缺关键 cookie 直接报错；
    - 插件启动时的「配置导入」用指纹判重，配置没变就不覆盖认证回写出来的滚续结果；
    - 调试端口改由浏览器自己挑（``--remote-debugging-port=0`` + DevToolsActivePort），
      消除 free_port() 的 TOCTOU 与「连到假 CDP 服务」；
    - CDP 连接断开立刻上抛，不再空转烧 CPU；
    - 失败冷却只对「真的认证失败」生效，导入 cookie 会清掉冷却。
"""

import hashlib
import json
import os
import signal
import socket
import subprocess
import tempfile
import threading
import time
import urllib.request
import shutil

try:  # 延迟到真正需要浏览器时才失败，缺包不应该让整个插件加载不了
    import websocket
except ImportError:  # pragma: no cover - 取决于部署环境
    websocket = None

try:
    from .hw_credentials import harden_file_permissions, _protect, _unprotect
    from .hw_credentials import DPAPIUnavailableError
except ImportError:
    from hw_credentials import harden_file_permissions, _protect, _unprotect
    from hw_credentials import DPAPIUnavailableError

TRAINING_CAMP_URL = "https://health.cloud.huawei.com/TrainingCamp"

#: cookie 文件加密后的魔数；没有这个前缀的文件按旧版明文 JSON 读取（平滑升级）。
COOKIE_MAGIC = b"HWCOOK1\0"
#: 记录「上一次从插件配置导入了什么」，用来区分「用户改了配置」和「认证回写滚续了 cookie」。
CONFIG_MARKER_SUFFIX = ".config_applied"

DEFAULT_BROWSERS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/microsoft-edge",
    "/usr/bin/microsoft-edge-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
    "/opt/google/chrome/chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]


def compute_expire_at(token_expire_at: float, ttl: int, now: float = None) -> float:
    """把「页面给的 expireTime」和「配置的 token_ttl」合成缓存过期时间。

    v0.4.0 有两条写 token 的路径（``main._token`` 和 ``hw_login._finish``），
    两处逻辑不一致：前者在 ``expireTime`` 距现在不足 30 秒时写入一个**过去**的
    过期时间（导致每次调用都重新认证），在 ``expireTime`` 已过期时反而缓存
    ``now + ttl``。这里统一成一份：
      - 缺失 / 已过期 / 剩余不足 30 秒 → 回退 ``now + max(60, ttl)``
      - 否则取 ``min(expireTime - 30, now + ttl)``
    """
    now = time.time() if now is None else now
    try:
        ttl = int(ttl or 0)
    except (TypeError, ValueError):
        ttl = 3000
    fallback = now + max(60, ttl or 3000)
    try:
        actual = float(token_expire_at or 0.0)
    except (TypeError, ValueError):
        actual = 0.0
    if actual != actual:  # NaN
        actual = 0.0
    expire_at = actual - 30
    if expire_at <= now:
        return fallback
    if ttl > 0:
        return min(expire_at, now + ttl)
    return expire_at


def browser_launch_args(browser_path: str, profile_dir: str, headless: bool, port: int = 0) -> list:
    """构造浏览器启动参数。

    ``port=0`` 让浏览器自己挑调试端口并写进 ``<profile>/DevToolsActivePort``。
    v0.4.0 用 ``free_port()``（bind → close）自己挑，端口在浏览器真正 bind 之前
    是空的，本机任意进程都能抢；抢到就能收到插件发来的明文 cookie 与账号密码。
    """
    args = [
        browser_path,
        "--remote-debugging-port=%d" % int(port),
        "--user-data-dir=" + profile_dir,
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-blink-features=AutomationControlled",
    ]
    if headless:
        args.append("--headless=new")
    # root（Docker / NAS）下 Chromium 无 --no-sandbox 会静默退出，
    # 表现成「调试端口 40 秒未就绪」，而 stderr 又被丢进 DEVNULL。
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        args += ["--no-sandbox", "--disable-dev-shm-usage"]
    args.append("about:blank")
    return args


def read_devtools_port(profile_dir: str, timeout: float = 40.0, cancel=None, proc=None) -> int:
    """等浏览器写出 DevToolsActivePort 并返回端口；超时/进程已退出返回 0。

    ``proc`` 给了就同时检查进程还活着——否则上一轮崩溃留下的陈旧
    ``DevToolsActivePort`` 会被当成这一轮的端口（只能靠调用方先删文件来兜）。
    """
    path = os.path.join(profile_dir, "DevToolsActivePort")
    deadline = time.monotonic() + max(1.0, float(timeout))
    while time.monotonic() < deadline:
        if cancel is not None and cancel.is_set():
            return 0
        if proc is not None and proc.poll() is not None:
            return 0
        try:
            with open(path, "r", encoding="utf-8") as f:
                first = f.read().splitlines()[0].strip()
            if first.isdigit() and int(first) > 0:
                return int(first)
        except (OSError, IndexError):
            pass
        time.sleep(0.1)
    return 0


def clear_devtools_port_file(profile_dir: str) -> None:
    """启动前删掉上一轮遗留的 DevToolsActivePort，避免读到过期端口。"""
    try:
        os.unlink(os.path.join(profile_dir, "DevToolsActivePort"))
    except OSError:
        pass


# Cookie-Editor / CDP 两种导出的 sameSite 写法 → CDP 规范值
_SAMESITE = {
    "no_restriction": "None",
    "unspecified": None,
    "lax": "Lax",
    "strict": "Strict",
    "none": "None",
    "None": "None",
    "Lax": "Lax",
    "Strict": "Strict",
}

# 设置页提供的最小可用 cookie 集；CASTGC 必须按两个路径各生成一条。
MIN_COOKIE_SPECS = (
    ("CASTGC", ".id1.cloud.huawei.com", ("/CAS", "/UnifiedIDMPortal")),
    ("sid", ".id1.cloud.huawei.com", ("/",)),
    ("hwid_cas_sid", ".id1.cloud.huawei.com", ("/",)),
    ("HuaweiID_CAS_ISCASLOGIN", ".huawei.com", ("/",)),
    ("CASLOGINSITE", ".huawei.com", ("/",)),
    ("LOGINACCSITE", ".huawei.com", ("/",)),
)

#: 缺了这两个就一定登不上，导入时直接报错；其余缺了只告警（兼容老用户的精简导出）。
REQUIRED_COOKIES = ("CASTGC", "sid")


def _cookie_field_key(value) -> str:
    """把配置键/行首名称归一化，兼容 HuaweiID 与 HUAWEI_ID 两种写法。"""
    return "".join(ch for ch in str(value).strip().lower() if ch.isalnum())


def cookies_from_fields(fields) -> list:
    """把六条分项值转换为 CDP cookie；空值跳过，便于只补失效项。"""
    if not isinstance(fields, dict):
        return []
    values = {_cookie_field_key(k): str(v).strip() for k, v in fields.items() if v is not None}
    out = []
    for name, domain, paths in MIN_COOKIE_SPECS:
        value = values.get(_cookie_field_key(name), "")
        if not value:
            continue
        for path in paths:
            out.append(normalize_cookie({
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                "secure": True,
            }))
    return out


def parse_cookie_fields(text: str) -> dict:
    """解析「名称=值 / 名称:值」或按六项顺序填写的多行文本。

    v0.4.1 起对模糊输入**直接报错**，不再静默错位/丢弃（检测报告 P1-9）：
      - 纯值按序：行数必须恰好等于 6，少一行会让后面的值整体前移；
      - 命名行与纯值行不能混写（v0.4.0 会把纯值行全部丢掉）。

    报错文案**只给行号，不回显内容**——被丢掉的行往往就是 cookie 值本身，
    把它们写进异常文案会经由日志和 WebUI 响应泄露出去。
    """
    lines = [line.strip() for line in str(text or "").splitlines()]
    numbered = [(idx, line) for idx, line in enumerate(lines, 1)
                if line and not line.startswith("#")]
    if not numbered:
        return {}
    fields = {}
    named = False
    ordered = []
    known = {_cookie_field_key(n) for n, _, _ in MIN_COOKIE_SPECS}
    for lineno, line in numbered:
        # 只有分隔符左侧归一化后命中已知名称，才把该行视为键值行；
        # 否则整行保留为纯值，避免 CASTGC 等带 base64 padding 的值错位。
        handled = False
        for separator in ("=", ":", "："):
            if separator not in line:
                continue
            key, value = line.split(separator, 1)
            if _cookie_field_key(key) in known:
                fields[key.strip()] = value.strip()
                named = True
                handled = True
                break
        if handled:
            continue
        ordered.append((lineno, line))
    if named:
        if ordered:
            raise HuaweiAuthError(
                "cookie 输入不能混写：请全部用「名称=值」，或全部按六项顺序只填值。"
                "第 %s 行既没有可识别的名称、也不会被使用。"
                % "、".join(str(n) for n, _ in ordered[:6]))
        return fields
    if len(ordered) != len(MIN_COOKIE_SPECS):
        raise HuaweiAuthError(
            "按序填写时必须恰好 %d 行（CASTGC / sid / hwid_cas_sid / "
            "HuaweiID_CAS_ISCASLOGIN / CASLOGINSITE / LOGINACCSITE），当前 %d 行"
            "（第 %s 行）。少一行会让后面的值整体错位，多一行会被丢弃——"
            "请改用「名称=值」写法。"
            % (len(MIN_COOKIE_SPECS), len(ordered),
               "、".join(str(n) for n, _ in ordered[:6])))
    return {name: value for (name, _, _), (_, value) in zip(MIN_COOKIE_SPECS, ordered)}


def parse_cookie_input(text: str) -> list:
    """兼容旧 JSON，同时接受设置页的六行分项输入。"""
    stripped = str(text or "").strip()
    if not stripped:
        return []
    if stripped.startswith("[") or stripped.startswith("{"):
        return parse_cookie_payload(stripped)
    return cookies_from_fields(parse_cookie_fields(stripped))


def _merge_cookies(*groups) -> list:
    merged = {}
    for group in groups:
        for cookie in group or []:
            key = (cookie.get("name"), cookie.get("domain"), cookie.get("path", "/"))
            merged[key] = cookie
    return list(merged.values())


def _canonical_complete(cookies) -> list:
    """按 MIN_COOKIE_SPECS 补齐缺失的 (name, domain, path) 组合。

    v0.4.0 只有「六条分项」通道会用 MIN_COOKIE_SPECS 补域/路径，「整段 JSON」
    通道原样导入——于是老用户按 README 用 JSON 导入时，CASTGC 只有 /CAS 一条，
    少了 /UnifiedIDMPortal（检测报告 P2-15）。这里统一补齐。
    """
    out = list(cookies)
    by_name = {}
    for cookie in out:
        by_name.setdefault(cookie.get("name"), cookie)
    for name, domain, paths in MIN_COOKIE_SPECS:
        src = by_name.get(name)
        if not src:
            continue
        have = {(c.get("domain"), c.get("path", "/")) for c in out if c.get("name") == name}
        for path in paths:
            if (domain, path) in have:
                continue
            clone = dict(src)
            clone["domain"] = domain
            clone["path"] = path
            out.append(normalize_cookie(clone))
    return out


def _missing_names(cookies) -> list:
    have = {c.get("name") for c in cookies}
    return [name for name, _, _ in MIN_COOKIE_SPECS if name not in have]


def find_browser(explicit: str = "") -> str:
    """定位本机浏览器可执行文件。"""
    if explicit and os.path.exists(explicit):
        return explicit
    for p in DEFAULT_BROWSERS:
        if os.path.exists(p):
            return p
    # PATH 安装（Docker、Nix、Homebrew、snap）不一定落在上面的固定路径。
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                 "microsoft-edge", "microsoft-edge-stable", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    return ""


def free_port() -> int:
    """取一个空闲本地端口。

    仅保留给外部调用方/测试；本模块已改用 ``--remote-debugging-port=0``。
    """
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def normalize_cookie(raw: dict) -> dict:
    """把 Cookie-Editor / CDP 两种导出格式统一成 CDP Network.setCookies 格式。"""
    out = {
        "name": str(raw.get("name", "")),
        "value": str(raw.get("value", "")),
        "domain": str(raw.get("domain", "")),
        "path": str(raw.get("path") or "/"),
        "secure": bool(raw.get("secure", False)),
        "httpOnly": bool(raw.get("httpOnly", False)),
    }
    exp = raw.get("expires", raw.get("expirationDate", -1))
    try:
        exp = float(exp)
    except (TypeError, ValueError):
        exp = -1
    if exp > 0:
        out["expires"] = exp
    ss = _SAMESITE.get(str(raw.get("sameSite", "")), None)
    if ss:
        out["sameSite"] = ss
    return out


def parse_cookie_payload(text: str):
    """解析 cookie JSON：支持数组、{cookies:[...]}、以及 CDP 原始返回。"""
    if not text or not text.strip():
        return []
    try:
        data = json.loads(text)
    except ValueError as exc:
        # 不要把 "Expecting property name..." 这种原文回显给前端
        raise HuaweiAuthError("cookie JSON 格式错误，请检查是否粘贴完整") from exc
    if isinstance(data, dict):
        for key in ("cookies", "Cookies", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = []
    if not isinstance(data, list):
        return []
    return [normalize_cookie(c) for c in data if isinstance(c, dict) and c.get("name")]


class HuaweiAuthError(Exception):
    """认证流程失败。"""


class HuaweiAuth:
    """负责「拿 token」这一件事。"""

    # 认证对象会按调用临时创建；按 cookie 文件共享锁和失败时间，才能让
    # 导入、回写、清除以及连续认证失败在不同对象之间也保持串行/冷却。
    _state_lock = threading.Lock()
    _cookie_locks = {}
    _failure_state = {}
    #: 同一时刻只允许一个浏览器认证在跑（工具超时被取消后，线程仍会继续跑，
    #: 没有这把锁就会在同一个 profile 上再起一个浏览器，互相抢 profile / 互相 taskkill）。
    _auth_mutex = threading.Lock()

    def __init__(
        self,
        cookie_file: str,
        profile_dir: str,
        browser_path: str = "",
        headless: bool = True,
        page_timeout: int = 90,
        logger=None,
    ):
        self.cookie_file = cookie_file
        self.profile_dir = profile_dir
        self.browser_path = browser_path
        self.headless = headless
        # 统一成正数，确保底层 urllib/websocket/CDP 都得到可用的超时值。
        try:
            self.page_timeout = max(1.0, float(page_timeout))
        except (TypeError, ValueError):
            self.page_timeout = 90.0
        self.log = logger
        # 共享文件锁避免导入、认证回写和清除同时修改 cookie 文件。
        lock_key = os.path.abspath(cookie_file)
        with self._state_lock:
            self._cookie_lock = self._cookie_locks.setdefault(lock_key, threading.RLock())
            state = self._failure_state.setdefault(lock_key, {"lock": threading.Lock(), "last": 0.0})
        self._failure_lock = state["lock"]
        self._failure_state_item = state
        self._last_failure = state["last"]
        self.failure_cooldown = 180
        self.token_expire_at = 0.0
        self._plaintext_warned = False

    # ---------- cookie 读写 ----------

    def _serialize_cookies(self, cookies) -> bytes:
        payload = json.dumps(cookies, ensure_ascii=False, indent=1).encode("utf-8")
        try:
            return COOKIE_MAGIC + _protect(payload)
        except DPAPIUnavailableError:
            # 非 Windows：DPAPI 不可用，退回明文（下面会 chmod 0600）。
            # 这是 Linux/Docker 的正常路径，不是故障，用 info 免得住刷警告。
            if not self._plaintext_warned:
                self._plaintext_warned = True
                self._log("info", "当前系统非 Windows，cookie 文件以 0600 权限保存（不使用 DPAPI）")
            return payload
        except Exception as exc:
            if not self._plaintext_warned:
                self._plaintext_warned = True
                self._log("warning", "cookie 加密失败（%s），退回明文保存" % exc)
            return payload

    def _deserialize_cookies(self, raw: bytes):
        if raw.startswith(COOKIE_MAGIC):
            try:
                payload = _unprotect(raw[len(COOKIE_MAGIC):])
            except Exception as exc:
                raise HuaweiAuthError(
                    "cookie 文件无法解密（换过 Windows 用户或换过机器），请重新导入 cookie"
                ) from exc
        else:
            payload = raw
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HuaweiAuthError("cookie 文件编码异常，请重新导入 cookie") from exc
        try:
            return parse_cookie_payload(text)
        except ValueError as exc:
            raise HuaweiAuthError("cookie 文件不是合法 JSON，请重新导入 cookie") from exc

    def _load_cookie_snapshot(self):
        with self._cookie_lock:
            if not os.path.exists(self.cookie_file):
                return [], None
            with open(self.cookie_file, "rb") as f:
                raw = f.read()
            try:
                mtime_ns = os.stat(self.cookie_file).st_mtime_ns
            except OSError:
                mtime_ns = None
            return self._deserialize_cookies(raw), mtime_ns

    def load_cookies(self):
        return self._load_cookie_snapshot()[0]

    def save_cookies(self, cookies, expected_mtime_ns=None):
        # 原子替换防止进程中断留下半份 JSON，且与导入/清除使用同一把锁。
        with self._cookie_lock:
            if expected_mtime_ns is not None:
                if not os.path.exists(self.cookie_file):
                    # 用户在认证期间清除了 cookie，不能用旧认证结果把它恢复。
                    self._log("warning", "检测到 cookie 文件已清除，跳过旧认证结果回写")
                    return False
                try:
                    current_mtime_ns = os.stat(self.cookie_file).st_mtime_ns
                except OSError:
                    current_mtime_ns = None
                if current_mtime_ns != expected_mtime_ns:
                    # 用户在认证期间导入了新 cookie，不能用旧认证结果覆盖它。
                    self._log("warning", "检测到 cookie 文件已变化，跳过旧认证结果回写")
                    return False
            parent = os.path.dirname(self.cookie_file) or "."
            os.makedirs(parent, exist_ok=True)
            blob = self._serialize_cookies(cookies)
            fd, tmp = tempfile.mkstemp(prefix=".hw_cookies.", suffix=".tmp", dir=parent)
            try:
                with os.fdopen(fd, "wb") as f:
                    f.write(blob)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, self.cookie_file)
                harden_file_permissions(self.cookie_file)
            finally:
                try:
                    if os.path.exists(tmp):
                        os.unlink(tmp)
                except OSError:
                    pass
            return True

    # ---------- 配置指纹：区分「用户改了配置」和「认证回写滚续了 cookie」 ----------

    def _marker_path(self) -> str:
        return self.cookie_file + CONFIG_MARKER_SUFFIX

    @staticmethod
    def _config_digest(cookies) -> str:
        payload = json.dumps(
            sorted([c.get("name"), c.get("domain"), c.get("path", "/"), c.get("value")]
                   for c in cookies),
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _read_config_marker(self) -> str:
        try:
            with open(self._marker_path(), "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError:
            return ""

    def _write_config_marker(self, digest: str) -> None:
        try:
            with open(self._marker_path(), "w", encoding="utf-8") as f:
                f.write(digest)
            harden_file_permissions(self._marker_path())
        except OSError as exc:
            self._log("warning", "写入 cookie 配置指纹失败：%s" % exc)

    def purge_stale_tmp(self) -> int:
        """清掉崩溃残留的临时文件（里面是明文 cookie，clear_cookies 也不清）。"""
        parent = os.path.dirname(self.cookie_file) or "."
        base = os.path.basename(self.cookie_file)
        removed = 0
        try:
            names = os.listdir(parent)
        except OSError:
            return 0
        for name in names:
            if not (name.startswith(base + ".tmp.") or name.startswith(".hw_cookies.")):
                continue
            try:
                os.unlink(os.path.join(parent, name))
                removed += 1
            except OSError:
                pass
        return removed

    # ---------- 导入 / 清除 ----------

    def import_cookie_text(self, text: str) -> int:
        """导入一份 Cookie-Editor / CDP 导出的 cookie 文本，返回条数。"""
        cookies = parse_cookie_input(text)
        if not cookies:
            raise HuaweiAuthError("cookie 文本解析为空，请确认导出内容正确")
        self._apply_import(cookies)
        return len(cookies)

    def import_cookie_sources(self, json_text: str = "", fields=None,
                              only_if_changed: bool = False) -> int:
        """合并旧 JSON 和设置页分项 cookie，兼容两种配置入口。

        v0.4.1：
          - 与磁盘上已有的 cookie **合并**，不再整份覆盖（否则 OAuth 期间下发的
            __uni__uid / JSESSIONID 会被删掉，滚续结果也被旧快照覆盖）；
          - 按 MIN_COOKIE_SPECS 补齐 CASTGC 的两条路径；
          - 缺 CASTGC / sid 直接报错，缺其余只告警；
          - ``only_if_changed=True`` 时用配置指纹判重，配置没变就什么都不做。
        """
        groups = []
        if str(json_text or "").strip():
            groups.append(parse_cookie_input(json_text))
        groups.append(cookies_from_fields(fields))
        incoming = _merge_cookies(*groups)
        if not incoming:
            raise HuaweiAuthError("cookie 内容为空，请至少填写一条有效 cookie")

        digest = None
        if only_if_changed:
            digest = self._config_digest(incoming)
            if digest == self._read_config_marker():
                return 0

        try:
            existing = self.load_cookies()
        except HuaweiAuthError:
            existing = []
        merged = _merge_cookies(existing, incoming)
        merged = _canonical_complete(merged)

        missing = _missing_names(merged)
        fatal = [n for n in missing if n in REQUIRED_COOKIES]
        if fatal:
            raise HuaweiAuthError(
                "缺少必要的 cookie：%s。关键项在登录域 .id1.cloud.huawei.com 下，"
                "不会出现在 health 域的导出里；请切到该域导出，或改用六项分填。"
                % "、".join(fatal))
        if missing:
            self._log("warning", "导入的 cookie 里缺少：%s（如登录失败请补齐）" % "、".join(missing))

        self.save_cookies(merged)
        if digest is not None:
            self._write_config_marker(digest)
        self.clear_failure()
        return len(incoming)

    def _apply_import(self, cookies) -> None:
        try:
            existing = self.load_cookies()
        except HuaweiAuthError:
            existing = []
        merged = _canonical_complete(_merge_cookies(existing, cookies))
        missing = _missing_names(merged)
        fatal = [n for n in missing if n in REQUIRED_COOKIES]
        if fatal:
            raise HuaweiAuthError("缺少必要的 cookie：%s" % "、".join(fatal))
        self.save_cookies(merged)
        self.clear_failure()

    def clear_cookies(self):
        """清除 cookie 文件并与导入、认证回写串行，避免并发恢复旧文件。"""
        with self._cookie_lock:
            self.purge_stale_tmp()
            try:
                os.unlink(self.cookie_file)
            except FileNotFoundError:
                pass
            for suffix in (CONFIG_MARKER_SUFFIX,):
                try:
                    os.unlink(self.cookie_file + suffix)
                except OSError:
                    pass

    # ---------- 失败冷却 ----------

    def _mark_failure(self):
        """记录失败时间，统一让早期失败也进入冷却窗口。"""
        with self._failure_lock:
            self._last_failure = time.time()
            self._failure_state_item["last"] = self._last_failure

    def clear_failure(self):
        """导入/清除 cookie 后清掉冷却——用户已经修好了，不该再被拦 180 秒。"""
        with self._failure_lock:
            self._last_failure = 0.0
            self._failure_state_item["last"] = 0.0

    def _cleanup_orphan_browsers(self):
        """清理命令行带本插件 profile 的孤儿浏览器，避免下一轮认证被 profile 锁住。"""
        if os.name != "nt":
            self._cleanup_orphan_browsers_posix()
            return
        needle = os.path.abspath(self.profile_dir).replace("'", "''")
        script = (
            "$needle='%s'; "
            "Get-CimInstance Win32_Process | "
            "Where-Object { ($_.Name -eq 'msedge.exe' -or $_.Name -eq 'chrome.exe') "
            "-and $_.CommandLine -and $_.CommandLine.ToLower().Contains($needle.ToLower()) } | "
            "ForEach-Object { taskkill /PID $_.ProcessId /T /F 2>$null | Out-Null }"
        ) % needle
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", script],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
            time.sleep(0.2)
        except Exception as exc:
            self._log("warning", "孤儿浏览器清理失败：%s" % exc)

    def _cleanup_orphan_browsers_posix(self):
        """Linux/macOS：v0.4.0 这里是空实现，孤儿浏览器会占住 SingletonLock。"""
        needle = os.path.abspath(self.profile_dir)
        try:
            out = subprocess.run(
                ["pgrep", "-f", needle],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=10,
                check=False,
            )
        except Exception:
            return
        for token in (out.stdout or b"").split():
            try:
                pid = int(token)
            except (TypeError, ValueError):
                continue
            if pid == os.getpid():
                continue
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass

    # ---------- 主流程 ----------

    def fetch_token(self) -> str:
        """起浏览器 → 注入 cookie → 自动登录 → 返回 accessToken。阻塞调用。"""
        with self._failure_lock:
            self._last_failure = self._failure_state_item["last"]
            remain = self.failure_cooldown - (time.time() - self._last_failure)
        if remain > 0:
            raise HuaweiAuthError("认证失败冷却中，请 %.0f 秒后重试" % remain)
        if websocket is None:
            raise HuaweiAuthError("缺少 websocket-client，无法完成浏览器认证（pip install websocket-client）")
        # 锁等待用固定小预算：page_timeout 是「单次 CDP 调用」的上限，不是「等别人认证完」
        # 的上限。用它当锁预算会在 page_timeout=600 时把工具调用拖到 5 倍于 120 秒超时。
        if not self._auth_mutex.acquire(timeout=5):
            raise HuaweiAuthError("另一个认证流程正在进行，请稍后重试（约 1 分钟内会结束）")
        try:
            return self._fetch_token_locked()
        finally:
            self._auth_mutex.release()

    def _fetch_token_locked(self) -> str:
        started = time.monotonic()
        browser = find_browser(self.browser_path)
        if not browser:
            # 纯配置错误：用户填好路径立刻就能用，不该进 180 秒冷却。
            raise HuaweiAuthError("找不到 Edge/Chrome，请在插件配置里填写浏览器路径")

        cookies, cookie_mtime_ns = self._load_cookie_snapshot()
        if not cookies:
            raise HuaweiAuthError(
                "没有 cookie。请先用 Cookie-Editor 从已登录的华为运动健康网页端导出，"
                "再把 JSON 导入插件。"
            )
        # Only a real browser + cookie attempt reaches this point; configuration
        # errors above no longer print "认证阶段开始" once per parallel request.
        self._log("info", "认证阶段开始")
        # 上一次异常退出可能留下 Edge 子进程，启动前先清理本插件自己的 profile。
        self._cleanup_orphan_browsers()

        os.makedirs(self.profile_dir, exist_ok=True)
        clear_devtools_port_file(self.profile_dir)
        args = browser_launch_args(browser, self.profile_dir, self.headless, port=0)

        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        popen_kwargs = {
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "creationflags": creationflags,
        }
        if os.name != "nt":
            # Unix 下建立独立进程组，和 Windows taskkill /T 对称清理整个浏览器树。
            popen_kwargs["start_new_session"] = True
        try:
            proc = subprocess.Popen(args, **popen_kwargs)
        except Exception as exc:
            self._log("warning", "浏览器启动失败：%s" % exc)
            raise HuaweiAuthError("浏览器进程启动失败") from exc
        ws = None
        try:
            port = read_devtools_port(
                self.profile_dir, timeout=min(40, self.page_timeout), proc=proc)
            if not port:
                raise HuaweiAuthError(
                    "浏览器调试端口未就绪；可能是上次浏览器未退出，请重试或手动结束 msedge 进程")
            self._wait_cdp(port)
            ws = self._attach(port)
            self._call(ws, "Network.setCookies", {"cookies": cookies})
            self._call(ws, "Page.enable")
            self._call(ws, "Runtime.enable")
            self._call(ws, "Page.navigate", {"url": TRAINING_CAMP_URL})

            token = self._wait_token(ws, timeout=min(25, self.page_timeout))
            if not token:
                self._log("info", "未持 token，点击「登录」走 OAuth 自动授权…")
                self._click_login(ws)
                token = self._wait_token(ws, timeout=min(40, self.page_timeout))

            if not token:
                raise HuaweiAuthError(
                    "自动登录失败：页面未拿到 accessToken。"
                    "多半是 cookie 已过期，请重新从浏览器导出 cookie 导入插件。"
                )

            # 回写新 cookie（滚续）
            try:
                fresh = self._call(ws, "Network.getAllCookies").get("result", {}).get("cookies", [])
                if len(fresh) >= max(5, len(cookies) // 2):
                    if self.save_cookies([normalize_cookie(c) for c in fresh], cookie_mtime_ns):
                        self._log("info", "cookie 已回写：%d → %d 条" % (len(cookies), len(fresh)))
            except Exception as exc:  # 回写失败不影响本次取数
                self._log("warning", "cookie 回写失败：%s" % exc)

            # 页面提供的过期时间优先用于后续缓存 TTL；旧页面没有该字段时保留安全默认值。
            expire = self._js(ws, "sessionStorage.getItem('expireTime')")
            try:
                self.token_expire_at = float(expire) / (1000 if float(expire) > 10**11 else 1)
            except (TypeError, ValueError):
                self.token_expire_at = time.time() + 3000
            self.clear_failure()
            self._log("info", "认证成功，耗时 %.1f 秒" % (time.monotonic() - started))
            return token
        except Exception as exc:
            self._mark_failure()
            self._log("warning", "认证失败，耗时 %.1f 秒：%s" % (time.monotonic() - started, exc))
            raise
        finally:
            try:
                if ws is not None:
                    self._call(ws, "Browser.close", timeout=5)
                    ws.close()
            except Exception:
                pass
            self._stop_process(proc)

    # ---------- CDP 细节 ----------

    def _wait_cdp(self, port: int, timeout: int = 40):
        # 页面超时是整个 CDP 初始化阶段的上限，避免单独的端口轮询越界。
        deadline = time.monotonic() + min(max(1, timeout), max(1, self.page_timeout))
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:%d/json/version" % port,
                    timeout=min(2, max(1, self.page_timeout)),
                ) as r:
                    info = json.load(r)
                    # 只校验「返回是 JSON」不够：本机任意进程都能在这个端口起个假服务。
                    if isinstance(info, dict) and info.get("webSocketDebuggerUrl"):
                        return
            except Exception:
                pass
            time.sleep(0.5)
        raise HuaweiAuthError("浏览器调试端口未就绪（%d）" % port)

    def _attach(self, port: int):
        if websocket is None:
            raise HuaweiAuthError("缺少 websocket-client，无法完成浏览器认证")
        with urllib.request.urlopen("http://127.0.0.1:%d/json" % port, timeout=min(5, self.page_timeout)) as r:
            tabs = json.load(r)
        pages = [t for t in tabs if t.get("type") == "page"]
        if not pages:
            raise HuaweiAuthError("浏览器里没有可用页面目标")
        url = pages[0].get("webSocketDebuggerUrl") or ""
        if not url.startswith("ws://127.0.0.1:") and not url.startswith("ws://localhost:"):
            raise HuaweiAuthError("CDP 目标地址异常：%s" % url[:80])
        ws = websocket.create_connection(
            url, timeout=self.page_timeout, suppress_origin=True
        )
        ws.settimeout(min(3, self.page_timeout))
        return ws

    def _call(self, ws, method: str, params=None, timeout: int = None):
        # 所有 CDP 请求统一受 page_timeout 约束，超时抛明确异常供上层记录。
        timeout = min(timeout or self.page_timeout, max(1, self.page_timeout))
        self._seq = getattr(self, "_seq", 0) + 1
        mid = self._seq
        try:
            ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        except Exception as exc:
            # 对端已关时 send() 也会立刻抛。这里不分类的话，调用方拿到的是裸
            # WebSocketConnectionClosedException，"浏览器已关闭"分支永远不可达。
            raise HuaweiAuthError("CDP 连接已断开：%s: %s" % (type(exc).__name__, exc)) from exc
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                raw = ws.recv()
            except Exception as exc:
                # v0.4.0 这里裸 continue：连接已断时 recv() 立刻抛，形成纯忙等，
                # 实测 426 万次/秒、单核 100% 直到 page_timeout（默认 90 秒）。
                name = type(exc).__name__
                if "Timeout" in name:
                    continue
                raise HuaweiAuthError("CDP 连接已断开：%s: %s" % (name, exc)) from exc
            try:
                msg = json.loads(raw)
            except Exception:
                continue
            if msg.get("id") == mid:
                if msg.get("error"):
                    raise HuaweiAuthError("CDP 调用失败：%s" % msg["error"])
                return msg
        raise HuaweiAuthError("CDP 调用超时：%s（%.1f 秒）" % (method, timeout))

    def _stop_process(self, proc, sweep_profile: bool = True):
        """先优雅退出并等待，失败时杀掉整个进程组，避免孤儿浏览器。

        ``sweep_profile=False`` 时不做「按 profile 路径兜底清扫」——登录会话在
        已经被新会话取代时用它，否则会把新会话刚起的浏览器一起 taskkill。
        """
        if proc is None:
            return
        try:
            proc.terminate()
        except Exception:
            pass
        # Windows 上 Popen 常拿到 Edge 启动器，terminate 只会结束父进程；
        # 无条件 taskkill /T /F 才能同步清理真正持有 profile 锁的子进程。
        if os.name == "nt":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    timeout=10,
                )
            except Exception:
                pass
        else:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, OSError):
                pass
        try:
            proc.wait(timeout=3)
        except Exception:
            try:
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        timeout=10,
                    )
                else:
                    os.killpg(proc.pid, signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            try:
                proc.wait(timeout=3)
            except Exception:
                pass
        # Edge 的启动器可能在 taskkill 前已自行退出，按 profile 路径补清真正的浏览器进程。
        # 注意：登录会话在「自己已被新会话取代」时必须传 sweep_profile=False。
        if sweep_profile:
            try:
                self._cleanup_orphan_browsers()
            except Exception:
                pass

    def _js(self, ws, expr: str):
        r = self._call(ws, "Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
        })
        return r.get("result", {}).get("result", {}).get("value")

    def _wait_token(self, ws, timeout: int = 30) -> str:
        """轮询 sessionStorage.accessToken，避免死等固定秒数。"""
        deadline = time.monotonic() + min(timeout, max(1, self.page_timeout))
        while time.monotonic() < deadline:
            try:
                tok = self._js(ws, "sessionStorage.getItem('accessToken')")
            except HuaweiAuthError:
                # 连接已断：立刻放弃，别把 90 秒空转吃满。
                raise
            except Exception:
                tok = None
            if tok:
                return tok
            time.sleep(2)
        return ""

    def _click_login(self, ws) -> bool:
        """点掉页面上的「登录」按钮（前端随后会跳 OAuth）。"""
        expr = (
            "(()=>{const t='登录';"
            "const els=[...document.querySelectorAll('*')]"
            ".filter(e=>e.children.length===0&&e.innerText&&e.innerText.trim()===t);"
            "if(els.length){els[0].click();return 'clicked';}return 'notfound';})()"
        )
        # 点击轮询也必须受 page_timeout 约束，避免 CDP 正常超时后仍长时间阻塞。
        deadline = time.monotonic() + max(1, self.page_timeout)
        while time.monotonic() < deadline:
            try:
                if self._js(ws, expr) == "clicked":
                    return True
            except HuaweiAuthError:
                raise
            except Exception:
                pass
            time.sleep(min(3, max(0.1, deadline - time.monotonic())))
        return False

    def _log(self, level: str, msg: str):
        if self.log:
            getattr(self.log, level, self.log.info)("[华为健康] " + msg)
