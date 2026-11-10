# 远端桌面捕捉

AstrBot 在 Docker、WSL 或服务器里时，可以让原生 Screen 从另一台桌面电脑获取画面。采集端只负责截图；聊天、识屏、观察记录、日记和主动联系仍由陪伴插件处理，不新增聊天入口或定时任务。

## 选择截图来源

在已登录的 AstrBot 管理界面打开「陪伴小屋」，找到屏幕读取权限区域，展开「截图来源」。只有核心管理员可以查看和修改来源。

- **AstrBot 所在电脑**：默认行为，使用 AstrBot 进程所在桌面的截图能力。
- **远端电脑**：填写采集端的 `/capture` 地址、设备名称、连接密钥和等待时间。密钥留空表示保留已保存值。

每个安装共用一个明确的设备来源。切换到另一台电脑需要保存新的来源；不会自动挑选设备。来源配置不会开启私聊授权、角色开关或主动联系。

设备配置单独保存在运行时的 `extensions/screen/capture-source.json`，不包含在模型工具参数里。模型看不到密钥，页面也不会读取已保存的密钥。

## Windows 采集端

在 Windows 已登录的桌面会话中准备 Python 3.10+ 和 Pillow：

```powershell
python -m pip install Pillow
python tools/remote_screen_collector.py --config "$env:LOCALAPPDATA/CompanionScreen/collector.json" --init --device-id windows-desktop
python tools/remote_screen_collector.py --config "$env:LOCALAPPDATA/CompanionScreen/collector.json"
```

配置中的 `token` 是自动生成的连接密钥。把它复制到截图来源设置，勿放进聊天、截图或代码仓库。配置目录应只允许当前用户和系统账户读取。启动时可以使用 `pythonw.exe`；计划任务必须选择当前登录用户的交互会话，系统服务通常看不到用户桌面。

默认只监听 `127.0.0.1:6191`。WSL 镜像网络配合使用宿主网络的 Docker 时可尝试 `http://127.0.0.1:6191/capture`；桥接网络不能直接把容器的 localhost 当成 Windows，需要设置可达的宿主地址或隧道。

锁屏、退出登录、受保护画面或桌面访问被系统拒绝时，截图可能失败或不可见。

## Arch Linux 与 niri

在 **niri 桌面终端**安装依赖，再运行本仓库提供的安装脚本：

```sh
sudo pacman -S python python-pillow grim
sh tools/install_screen_collector_linux.sh arch-niri
systemctl --user status companion-screen-collector.service
```

采集端在 niri 会话中自动使用 `grim`，不打开选区窗口，也不替换剪贴板。该方式需要当前 niri 支持 screencopy；旧版本不支持时应更新 niri。安装脚本导入桌面环境变量，服务随图形会话结束而停止。更换桌面会话后若无法截图，可在新桌面终端重跑安装脚本刷新环境。

配置位于 `~/.config/companion-screen/collector.json`，日志位于同目录的 `collector.log`。不要以 root 或在没有桌面环境变量的普通 SSH 会话里启动。

从 AstrBot 所在电脑连接 Linux，可以保持采集端仅监听 Linux 本机，并建立 SSH 转发：

```sh
ssh -N -L 127.0.0.1:6192:127.0.0.1:6191 your-user@linux-address
```

随后将陪伴插件来源改成 `http://127.0.0.1:6192/capture`，设备名称改为 `arch-niri`，填写 **Linux 采集端**的密钥。转发必须运行在 AstrBot 能访问的网络环境；Docker 桥接模式还需配置宿主访问。Windows 采集端继续占用 6191，两者不会混淆。

也可将采集端 `listen` 改为 Tailscale 或内网的具体地址，限制防火墙只允许 AstrBot 来源。远端公网地址必须使用 HTTPS；脚本本身不提供 TLS，可通过 HTTPS 反向代理或 SSH 私密通道连接。不要把裸 HTTP 采集端直接暴露到公网。

## 其他 Linux 桌面

- **X11**：`backend: "auto"` 使用 Pillow，服务需要登录用户的 `DISPLAY` 与 X11 访问权限。此时应额外向用户服务导入 `DISPLAY`、必要时导入 `XAUTHORITY`。
- **其他支持 screencopy 的 Wayland 桌面**：安装 `grim`，把配置中的 `backend` 改为 `"grim"`。
- **GNOME/KDE Wayland**：默认通过桌面截图门户。需要 `dbus-next`、可用的桌面门户服务以及桌面用户授权；不同桌面可能每次都弹出确认，不能保证无人值守。若系统不允许，应保留该限制。

Windows/Pillow 和 Arch + niri/grim 已验证实机采集，并从使用宿主网络的 AstrBot 容器取回图片；niri 使用 SSH 本地转发连接。其他 Linux 桌面仍需实机验证兼容性。

## 沿用原有主动观察

先恢复或装配屏幕陪伴、为对应角色启用，并保留当前私聊的屏幕授权。随后在原屏幕设置开启解读，按需要开启日记素材；在主动联系里开启主动观察及对应情景。

当前自主采集主要由原来的两种情景触发：晚安后经过设定延迟、用户仍未返回；或者主动联系发出后用户未回应。继续遵守原有静默、暂停和冷却规则。原有跟进路径具有每日额度豁免，本补丁不改变这项规则。它不会每隔固定分钟采集，也不会保证每张截图都发出 QQ 消息；模型仍按原规则决定是否联系。

原图进入原有 Screen 保存与授权流程，观察记录和日记素材使用同一来源。采集失败不会截另一台设备，也不会复用上次画面作为新截图。

## 传输与故障排查

采集端提供带认证的 `GET /health` 和 `POST /capture`。请求指定协议、请求编号与设备名，响应包含本次 JPEG、采集时间和相同设备名。原生提供器继续保留持久操作回执；请求结果不确定时不自动再次截图。

服务不接受浏览器跨站请求、不提供任意命令或文件读取；客户端禁用环境代理、重定向和重试，并校验设备、请求编号、时间与图像大小。采集端只在内存缓存最近八次结果；持久去重由 AstrBot 原生提供器负责。

两台电脑应保持系统时间同步。截图时间比 AstrBot 当前时间超前超过30秒、或落后超过60秒时会被拒收；可用 `timedatectl status` 检查 Linux 是否正常对时。实际 niri 联调曾遇到采集端时钟快约36秒，健康检查正常但截图被拒收；校准时间后原流程通过，无需放宽校验。

无法采集时依次检查：采集端进程和图形会话、设备名称与密钥、AstrBot 容器到采集地址的连通性、两机时间，最后检查屏幕角色开关、私聊授权与主动情景。日志只记录设备、大小和异常类型。

停止 Linux 服务：`systemctl --user disable --now companion-screen-collector.service`。回退本机来源可直接在页面选择「AstrBot 所在电脑」；数据和私聊授权保留。升级插件前请保留本地补丁备份，官方更新可能覆盖修改。
