# 远端桌面捕捉配置

采集端在需要截图的电脑上运行，使用已登录的桌面会话。在 AstrBot 管理界面打开「陪伴小屋」的屏幕读取权限，展开「截图来源」，由核心管理员选择远端电脑，填写采集地址、设备名称和连接密钥。

## Windows

安装 Python 3.10+，在本仓库目录执行：

```powershell
python -m pip install Pillow
python tools/remote_screen_collector.py --config "$env:LOCALAPPDATA/CompanionScreen/collector.json" --init --device-id windows-desktop
python tools/remote_screen_collector.py --config "$env:LOCALAPPDATA/CompanionScreen/collector.json"
```

采集端配置中的 `token` 是自动生成的连接密钥。将设备名称 `windows-desktop` 和密钥填入截图来源；配置文件应仅允许当前用户和系统账户读取。自动启动时使用当前登录用户的交互会话，系统服务通常无法访问桌面。

## Linux

使用发行版的软件包管理器为系统 Python 3.10+ 安装 Pillow，再按桌面环境准备截图依赖：X11 使用 Pillow，需要当前用户的 `DISPLAY` 和桌面访问权限；Wayland 可使用桌面截图门户，需要 `dbus-next` 和可用的门户服务，可能弹出授权。支持 screencopy 的桌面也可安装 `grim`，在采集端配置中设置 `"backend": "grim"`。

在已登录的桌面终端执行以下命令，安装 systemd 用户服务：

```sh
sh tools/install_screen_collector_linux.sh linux-desktop
systemctl --user status companion-screen-collector.service
```

配置位于 `~/.config/companion-screen/collector.json`，设备名称为 `linux-desktop`，其中的 `token` 填入截图来源。X11 会话还需向用户服务导入 `DISPLAY`，必要时导入 `XAUTHORITY`，然后重启服务；更换桌面会话后可重跑安装脚本刷新环境。不要以 root 身份或在缺少桌面环境变量的 SSH 会话中启动采集端。

没有 systemd 用户服务时，可在桌面终端手动初始化并启动：

```sh
python3 tools/remote_screen_collector.py --config "$HOME/.config/companion-screen/collector.json" --init --device-id linux-desktop
python3 tools/remote_screen_collector.py --config "$HOME/.config/companion-screen/collector.json"
```

## 连接与启用

默认仅监听 `127.0.0.1:6191`，截图地址为 `http://127.0.0.1:6191/capture`。该地址必须能从 AstrBot 所在环境访问；Docker 桥接网络里的 localhost 指向容器自身，需要可达的宿主地址或隧道。连接另一台 Linux 电脑时，可以在 AstrBot 能访问的环境建立 SSH 转发：

```sh
ssh -N -L 127.0.0.1:6192:127.0.0.1:6191 your-user@linux-address
```

此时来源地址改为 `http://127.0.0.1:6192/capture`，使用远端采集端的设备名称和密钥。采集端本身不提供 TLS；跨公网应使用 SSH 隧道或 HTTPS 反向代理，不要直接暴露裸 HTTP 服务，也不要把密钥放入聊天或代码仓库。

保存来源后，仍需为对应角色启用屏幕功能、保留当前会话的屏幕授权，并按需要开启解读、日记素材和主动观察。原有主动情景、静默和冷却规则继续生效，不新增周期截图任务，也不保证每次观察都发送消息。来源由整个安装共用，设备之间不会自动切换。

无法截图时检查桌面会话、系统授权、设备名称与密钥、网络连通和两端系统时间。锁屏或受保护画面可能无法采集；不同 Linux 桌面的权限与无人值守能力取决于系统支持。可在页面切回「AstrBot 所在电脑」停用远端来源；Linux 服务可用 `systemctl --user disable --now companion-screen-collector.service` 停止。
