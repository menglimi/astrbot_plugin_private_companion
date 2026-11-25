"""Find a launchable Android app store and guide the store's own download flow."""

import re


STORES = {
    "com.huawei.appmarket": "华为应用市场",
    "com.hihonor.appmarket": "荣耀应用市场",
    "com.xiaomi.market": "小米应用商店",
    "com.heytap.market": "OPPO 软件商店",
    "com.oppo.market": "OPPO 软件商店",
    "com.bbk.appstore": "vivo 应用商店",
    "com.meizu.mstore": "魅族应用商店",
    "com.sec.android.app.samsungapps": "Galaxy Store",
    "com.android.vending": "Google Play",
}
KNOWN_APPS = {"淘宝": "com.taobao.taobao", "手机淘宝": "com.taobao.taobao"}
DOWNLOAD = re.compile(
    r"(?P<store>在(?:手机)?应用(?:商店|市场)(?:里|中)?(?:搜索并)?)?"
    r"(?:下载并安装|下载安装|下载|安装)\s*(?P<app>[\w .+\-]{1,80})"
)


def app_from_goal(goal: str) -> str:
    """Recognize legacy, explicit store goals and the short known-app request."""
    match = DOWNLOAD.fullmatch(goal.strip().rstrip("。！!").strip())
    if match:
        app = match["app"].strip()
        if match["store"] or app in KNOWN_APPS:
            return app
    return ""


INSTRUCTION = """此任务是用户交办的应用下载。优先用当前手机已找到的应用商店。进入商店后搜索目标应用全名，
核对官方应用条目，再使用该条目的「安装」或「下载」。若用户要求停在详情页，按要求停下并如实说明当前页面。
下载或安装进行中时观察进度，看到应用条目变成「打开」或「已安装」后再报告完成。只有明确的下载完成提示时，说明下载已完成、安装状态待核对。
遇到付费、系统要求允许未知来源、账号登录或安全验证时，保留当前界面并说明具体需要用户做哪一步。
商店搜索无匹配时，说明已核对的商店和应用名称。考虑使用界面上的搜索建议，避免反复输入或打开桌面文件夹。"""
