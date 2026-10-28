# -*- coding: utf-8 -*-
"""桌面通知（Windows 右下角 toast）：出事了要能"看得见"，不能只写日志。

为什么需要：
  10-27 早上 QQ 掉登录，日志里只有一行 WARN，人不在电脑前就永远不知道。
  巡检/看门狗写日志是给复盘用的，**通知**才是给当下用的。

用法：
    python ops\\deploy\\notify.py "标题" "内容"
    from notify import toast; toast("标题", "内容")

实现：优先用 Windows 的 ToastNotification（WinRT），失败退回 msg.exe，
再失败退回一个简单的弹窗（不阻塞）。
"""
import os
import subprocess
import sys

_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

PS_TOAST = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$tpl = @"
<toast>
  <visual>
    <binding template="ToastGeneric">
      <text>{0}</text>
      <text>{1}</text>
    </binding>
  </visual>
</toast>
"@
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml(($tpl -f $env:YY_TOAST_TITLE, $env:YY_TOAST_MSG))
$toast = [Windows.UI.Notifications.ToastNotification]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Bot').Show($toast)
"""


def toast(title: str, message: str) -> bool:
    """弹一个 Windows 通知；成功返回 True。"""
    import os

    env = dict(os.environ)
    env["YY_TOAST_TITLE"] = (title or "")[:64]
    env["YY_TOAST_MSG"] = (message or "")[:200]
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", PS_TOAST],
            capture_output=True, text=True, timeout=25, env=env,
            encoding="utf-8", errors="replace", creationflags=_NO_WINDOW)
        if r.returncode == 0:
            return True
    except Exception:  # noqa: BLE001
        pass
    # 退路 1：msg.exe（终端服务消息，本机会弹窗）
    try:
        r = subprocess.run(["msg.exe", "*", f"{title}：{message}"],
                           capture_output=True, timeout=20, creationflags=_NO_WINDOW)
        if r.returncode == 0:
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    title = sys.argv[1]
    msg = sys.argv[2] if len(sys.argv) > 2 else ""
    ok = toast(title, msg)
    print("已发送通知" if ok else "通知发送失败（toast 和 msg.exe 都没成）")
    sys.exit(0 if ok else 1)
