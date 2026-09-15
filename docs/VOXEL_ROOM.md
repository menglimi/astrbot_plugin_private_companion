# 体素小屋测试版

版本：6.6.3b1。打开 AstrBot 的「陪伴面板 → 小屋」即可进入。升级安装后重载陪伴主插件，再刷新面板。该测试版基于当前主分支，独立提供下载；游戏、共处业务和门外空间仍待接入。

## 看看小屋

小屋使用本地 Three.js 绘制体素，没有人物模型。鼠标悬停家具才显示名称，点击打开相应内容；拖动空白处旋转，滚轮缩放。手机和键盘可使用场景下方的家具目录。2D 图片场景与切换入口已移除，旧浏览器视图偏好不再影响打开小屋。

| 家具 | 对应功能 |
| --- | --- |
| 生活日历 | 近期安排与生活日历编辑入口 |
| 手账与便签 | 日记翻页、正文，以及便签新增、完成与恢复 |
| 故事书架 | 创作扩展的公开作品及原密码抽屉入口 |
| 今日衣柜 | 柜门开合、当日穿搭照片及角色衣柜入口 |
| 睡眠与梦境 | 睡眠状态、梦境和余韵 |
| 见闻电台 | 新闻印象、原文和探索记录 |
| 成长花架 | 目标进度与技能成长入口 |
| 游戏、相机、电话等拓展物件 | 扩展状态、形态与摆放；相机可进入现有生图面板 |

生活内容读取插件已有记录，无数据时显示空状态；便签沿用原接口。物件外观不代表扩展已启用或正在运行，私密资料仍使用原夹层权限。

## 布置房间

点击场景右上角的「布置房间」。选择家具后可拖动、输入坐标、调整朝向或使用方向按钮。墙面物件可切换墙面、调整高度；家具摆放检查重叠与门口空间，地毯可铺在家具下。

家具提供八个系列：原木日常、奶油轻居、墨蓝复古、日式藤编、胡桃中古、工业拼搭、樱色软装、北欧几何。可逐件混搭；游戏物件还可切换棋桌、街机、挂屏，相机可切换拍立得展台和照片挂架，电话可改为壁挂款。

展开编辑面板顶部的「房屋样式」，墙面、地板、窗框和门板会一起更换，家具位置和款式保持独立。

| 房屋 | 建筑细节 |
| --- | --- |
| 原木小屋 | 木地板、绿色护墙板与布帘 |
| 日式和室 | 榻榻米、木格窗与纸格门 |
| 红砖阁楼 | 错缝砖墙、混凝土地砖与钢窗 |
| 法式奶油 | 墙面线框、拼花地板与拱形窗饰 |
| 海边白屋 | 白色灰泥、蓝边地砖与百叶窗 |
| 林间木屋 | 横向木墙、宽木地板与外露边梁 |

方向键微调，R / Shift R 旋转，Ctrl / Command Z 撤销，Shift Z 或 Y 重做。「默认布局」也能撤销；「取消」或 Escape 恢复进入编辑前的布置。「保存布置」才提交；保存期间继续调整的内容保留为新草稿。

## 保存与恢复

布局按人格保存到当前 AstrBot 的插件 KV 存储，等待数据库确认后才显示保存成功。同一 AstrBot 的其他浏览器可恢复布局；已有浏览器布局在服务端尚无记录时仍会读取，点击保存后迁移。失败保留草稿供重试，迟到响应不会串入另一个人格。

内嵌插件页的沙箱限制浏览器存储，因此布局使用现有页面桥接访问 `GET /home-room/layout` 和 `POST /home-room/layout/update`。`home_room_layout_v1:<personaId>` 只保存建筑和家具数据，不修改生活资料。旧格式缺失的字段由场景兼容恢复。

## 镜头、房门与氛围

电影巡游包含入室俯冲、桌边推拉、书脊横移、床边环绕、叶间取景和螺旋拉远。「下一镜」跳转段落，拖动接管视角；右上角「复位」停止巡游、恢复全景、缩放和水平视角，不撤销家具草稿。减少动态效果时使用静态取景。

房门可通过门扇或开关按钮独立开合。根节点发出 `companion:room-door` 事件，`detail` 为 `{version:1, doorId:"entrance", open, personaId}`，供后续功能接入。此测试版开门不会导航到门外空间或触发陪伴消息。

时间与天气沿用当前人格的环境设置及已有天气来源。可跟随时间切换日间、晨昏和夜晚；雨雪、茶杯热气、飞鸟、蝴蝶和萤火会按环境安静出现。动态效果与自动小事件可分别关闭，跟随系统减少动态效果；布置房间、巡游或后台暂停小事件，后台停止天气轮询。天气未配置或读取失败时明确提示，不编造天气。

## 加载与验证

浏览器需要支持 WebGL。场景加载失败时提供重试，家具目录仍可打开日记、日历等内容。场景与家具样式按需加载，隐藏页面暂停动画；厂商文件随插件提供，不需要下载外部模型。

针对性检查：

```text
python -m pytest tests/test_home_room_ui.py tests/test_home_room_environment.py tests/test_home_room_layout.py tests/test_release_layout.py tests/test_page_api_route_bindings.py -q
node --test --test-concurrency=1 tests/home_room_browser.cjs tests/home_room_styles_browser.cjs tests/home_room_extensions_browser.cjs tests/home_room_atmosphere_browser.cjs tests/home_room_storage_browser.cjs
```

Python 检查使用 AstrBot 运行环境。浏览器检查可用 `PC_PLAYWRIGHT_DIR` 指定 Playwright，`PC_ROOM_ARTIFACTS` 指定截图目录；API 均为隔离测试数据，不写真实陪伴资料。

维护时编辑 `pages/companion-panel/js/panels/home-room-3d.source.js`，使用以下命令打包。关闭模板字面量输出会保留着色器的内容，同时避免生成文件携带上游着色器的行尾空白。两个面板目录 `pages/companion-panel` 与 `pages/陪伴面板` 必须逐字节一致；Three.js r183 的许可随厂商文件附带。

```text
npx --yes --registry=https://registry.npmjs.org esbuild@0.25.10 pages/companion-panel/js/panels/home-room-3d.source.js --bundle --format=iife --platform=browser --target=es2020 --minify --charset=utf8 --supported:template-literal=false --outfile=pages/companion-panel/js/panels/home-room-3d.js
```
