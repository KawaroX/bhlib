# LCC (Library Control CLI)

一个简单的命令行工具，对接 **北京航空航天大学（BUAA）图书馆** 的预约系统 `booking.lib.buaa.edu.cn`（座位查询/预约、灯光亮度、番茄钟等）。

工具默认倾向于学院路校区图书馆一层西阅学空间（area_id=8，作者常用）；沙河校区、特色阅览室等其他区域通过 `--area-id` 指定（支持 id 或名字，见下文「区域编号参考」）。

## 免责声明

- 请确保你的使用符合学校/图书馆的服务条款与相关规定。
- `token` / `cookie` 属于敏感凭证，请勿泄露；建议定期更新，且不要提交到 Git（本项目已忽略 `.lcc.json`）。

## 安装

推荐用 [pipx](https://pipx.pypa.io/) 一键安装（需要 Python 3.9+，以及系统有 `openssl` 命令——macOS/Linux 默认都有）：

```bash
# 没有 pipx 的话先装一次：
#   macOS:  brew install pipx && pipx ensurepath
#   Linux:  python3 -m pip install --user pipx && python3 -m pipx ensurepath

pipx install git+https://github.com/KawaroX/lcc.git
```

装完后终端里直接用 `lcc`：

```bash
lcc --help
```

升级 / 卸载：

```bash
pipx upgrade lcc
pipx uninstall lcc
```

### 开发模式（在仓库目录里跑）

不想安装、直接用脚本：

```bash
python3 lcc.py --help
```

或者 editable 安装到 venv：

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## macOS 菜单栏工具（Swift）

在 `macos/LCCMenuBar` 提供了一个基于本 CLI 的菜单栏小工具：番茄钟 + 临时离开/签到/完全离开 + 空闲座位查询与预约。

```bash
cd macos/LCCMenuBar
swift run LCCMenuBar
```

## 配置认证信息

推荐方式：在当前目录创建 `.env`（参考 `.env.example`），写入 `LCC_USERNAME/LCC_PASSWORD`，这样 CLI 会在需要时自动登录/刷新 token（默认按每天 18:05 刷新策略）。

如果你仍然想手动填（抓包得到的 `authorization` 里的 token（不含前缀 `bearer`）和 cookie）也可以保存到当前目录的 `.lcc.json`：

```bash
lcc auth set --token 'YOUR_TOKEN' --cookie 'PHPSESSID=...; _zte_cid_=...'
```

如果你想在终端里直接用账号密码自动获取 token（CAS/SSO 登录），用：

```bash
lcc auth login --username '你的学号/工号'
```

会提示你输入密码（不会回显），登录成功后同样会写入 `.lcc.json`。

也可以用环境变量临时覆盖：

- `LCC_TOKEN`
- `LCC_COOKIE`
- `LCC_NO_PROXY=1`（不走系统代理，忽略 HTTP(S)_PROXY 等环境变量）
- `LCC_INSECURE=1`（跳过 HTTPS 证书校验，不推荐）
- `LCC_DEFAULT_AREA_ID=8`（默认区域，用于 `seat list` 不带参数时）

`--no-proxy` 也可以作为全局命令行 flag 放在任何位置（与 `LCC_NO_PROXY=1` 等价）；遇到「代理/中间人截断 TLS」导致的 `EOF occurred in violation of protocol` 时加上它通常就行：

```bash
lcc --no-proxy seat list --status 1
lcc area list --no-proxy
```

## 调节灯光亮度

```bash
lcc light set --brightness 19
```

默认会从 `/v4/index/subscribe` 取你当前记录对应的设备 id/area_id（所以只能控制你自己的灯）。

亮度范围取决于系统规则（通常是 0-100 或 0-20），以接口返回为准。

## 番茄钟（时间到闪烁灯光）

开始一个番茄钟（默认 25 分钟）。时间到了后会按 `20 -> 40 -> 20` 闪烁 2 次（即到达 40 两次）：

```bash
lcc pomo start
```

自定义时长 / 闪烁参数（秒数便于测试）：

```bash
lcc pomo start --minutes 30 --low 20 --high 40 --cycles 2 --interval 0
lcc pomo start --seconds 10
```

立即测试闪烁（不等计时结束）：

```bash
lcc pomo flash
```

## 查询“我当前的状态/座位”

很多情况下“我现在有没有占座/预约、当前座位号”等信息会在 `/v4/index/subscribe` 里（具体字段以返回为准）：

```bash
lcc me subscribe
```

精简摘要（便于脚本使用）：

```bash
lcc me current
```

## 列出全部区域（`area list`）

```bash
lcc area list              # 树形：校区 → 楼层 → 区域（id、free/total）
lcc area list --flat       # 扁平：id  完整路径  free/total（便于 grep / 管道）
lcc area list --json       # 原始结构
lcc area list --refresh    # 跳过本地缓存（24h TTL）重新拉取
```

底层调的是 `/v4/space/pcTopFor` + 每个校区的 `/v4/space/pick`，拉完后写入 `.lcc.json`（`area_tree_cache`），24 小时内不重复请求。

## 按名字指定区域

所有接受 `--area-id` 的命令（`seat list`、`space book`、`light list`、`prefs set --default-area-id`）都同时接受「id」或「名字」：

- 纯数字直接当 id 用（不走网络）
- 非数字会在区域树里做大小写不敏感的子串匹配（先精确名字，再 `name`/`nameMerge` 包含）
- 唯一命中 → 用它；多命中 → 列出候选让你缩小范围；零命中 → 报错并提示 `lcc area list`

例子：

```bash
lcc seat list --area-id 一层西        # → 匹配到学院路一层西阅学空间 (id=8)
lcc seat list --area-id 102阅学       # → 沙河一楼 102 阅学空间 (id=63)
lcc space book --area-id 六层西       # → 学院路六层西中文借阅室 (id=29)
lcc prefs set --default-area-id 一层西  # 存入 .lcc.json 时会先解析成 id
```

模糊词可能匹配多个（例如单独写 `三楼`），这时 CLI 会列出候选，你再精确一点就行。

## 查询区域座位列表

复刻你抓包的 `/v4/Space/seat`：

```bash
lcc seat list --area-id 8 --day 2026-04-08 --start-time 19:00 --end-time 23:00
lcc seat list --area-id 一层西 --status 1   # 按名字 + 只看空闲
```

如果你不想输入参数，默认会：

- `area-id`：使用默认 `LCC_DEFAULT_AREA_ID`（来自 `.env` 或 `.lcc.json`）
- `day`：今天
- `start-time`：当前时间
- `end-time`：23:00

```bash
lcc seat list
```

如果你还没设置默认区域，可以写入一次：

```bash
lcc prefs set --default-area-id 8
```

## 预约座位（交互选择）

先列出默认区域的空闲座位（status=1），然后输入你要的 `seat id` 或 `seat no` 进行预约：

```bash
lcc space book
```

不交互直接指定：

```bash
lcc space book --seat-id 276
```

交互输入默认按 `no`（座位号）理解；如果要按 `id` 指定，用前缀：`id:131`（也可以显式 `no:003`）。

备注：`/v4/space/confirm` 的加密明文里需要 `segment`。CLI 会尝试从 seat 接口响应里自动提取；如果提取不到，会提示你用 `--segment ...` 手动传入（可从浏览器抓包里得到，或先 `lcc seat list --json` 看返回里有没有相关字段）。

获取 `segment` 的最快方式（只做一次就行）：

1) 在 H5 里随便点一次“预约”，在 Network 里找到 `/v4/space/confirm` 请求的 `aesjson`；
2) 本地解密：

```bash
lcc crypto decrypt --aesjson '...你抓到的...' --json
```

3) 把输出里的 `segment` 写到 `.env`：`LCC_DEFAULT_SEGMENT=...`（或每次运行 `space book --segment ...`）。

另外：CLI 也会把已知的 `segment` 按 `(area_id,start_time,end_time)` 缓存到 `.lcc.json`，下次同样的时间段会自动复用。

筛选例子（排除“使用中/临时离开”这种 status）：

```bash
lcc seat list --area-id 8 --start-time 19:00 --end-time 23:00 --not-status 6 --not-status 7
```

## 临时离开（需要 `aesjson`）

已加了 `/v4/space/leave` 的命令，但由于该接口需要加密参数，不同学校部署/版本可能字段名不一样，我做成了两步更稳：

1) 先 dry-run 看一下会发什么（包含明文 payload + 生成的 aesjson）：

```bash
lcc space leave --dry-run
```

2) 确认无误后再真正发送：

```bash
lcc space leave
```

默认会从 subscribe 取当前记录，并用抓包一致的明文 payload `{"id":"<smartDeviceId>","points":{}}` 去加密。

如果服务器提示参数字段名不对，可以切换成基于座位 id 的 payload：

```bash
lcc space leave --style space_id
```

## 完全离开（结束使用，需要接口路径）

不同学校/部署“完全离开/结束使用”的接口路径可能不一样；CLI 提供了一个可配置的封装：

1) 先从浏览器抓包确认接口路径（例如你在 Network 里看到 `/v4/space/finish` / `/v4/space/stop` 之类）；
2) 写入 `.env`：

```bash
LCC_SPACE_FINISH_PATH=/v4/space/finish
```

3) 先 dry-run 确认加密明文结构无误：

```bash
lcc space finish --dry-run
```

4) 真正发送：

```bash
lcc space finish
```

如果你想调用任意其他写接口（同样是 `{"aesjson":"..."}` 的形式），也可以用通用命令：

```bash
lcc space action --path /v4/space/xxx --dry-run
lcc space action --path /v4/space/xxx
```

## 签到（需要 `aesjson`）

你贴的抓包里 `/v4/space/signin` 同样是传 `{"aesjson":"..."}`，且明文 payload 与临时离开一致（默认 `{"id":"<smartDeviceId>","points":{}}`）：

```bash
lcc space signin --dry-run
lcc space signin
```

## 加/解密工具（调试用）

你可以把浏览器里抓到的 `aesjson` 拿来本地解密，确认明文结构（不用把敏感数据贴到聊天里）：

```bash
lcc crypto decrypt --aesjson '...'
```

## `device_id` 从哪来？

它一般不是你“猜”的，而是前端先调用某个“设备列表/区域设备”接口拿到的（返回 JSON 里会有 `id`、`area_id`、名称等字段），然后你调节滑块时把其中某个设备的 `id` 填到 `setLightBrightness` 里。

最快的获取方式：

1. 浏览器打开图书馆 H5 页面，进入灯光控制页面；
2. 开发者工具 → Network，筛选关键词 `smartDevice` / `Brightness`；
3. 找到返回设备列表的那条请求，点开 Response，里面的 `id` 就是 `device_id`。

如果你已经在 Network 里找到了“设备列表”接口的路径（例如 `/reserve/smartDevice/xxx`），可以用 CLI 先把它打出来：

```bash
lcc light list --path '/reserve/smartDevice/xxx' --area-id 8
```

## 安全提示

- `.lcc.json` 和 `.env` 里是你的 token/cookie/账号密码，千万别提交到 Git（本仓库已在 `.gitignore` 里忽略）。
- Token 是 JWT，里面包含学号、姓名等个人信息；调试或贴抓包时请脱敏（只贴 `code`/`message`，不要贴完整 `authorization`/`Cookie`）。
- 如果不小心泄露了 token，重新登录一次（`lcc auth login`）会让旧 token 作废；必要时修改 SSO 密码。

## SSL 证书校验失败怎么办？

如果你遇到类似 `CERTIFICATE_VERIFY_FAILED`，通常是你本机 Python 没有正确安装/找到系统根证书。

优先建议（更安全）：

- 在 macOS 上，如果你用的是 python.org 的安装包，运行一次 `Install Certificates.command`；
- 或者在你的 Python 环境里安装/更新 `certifi` 并确保 `SSL_CERT_FILE` 指向它的证书包。

临时绕过（不推荐，但能用）：

```bash
lcc auth login --username '你的学号/工号' --insecure
```

## 区域编号参考

权威来源是实时接口——直接 `lcc area list` / `lcc area list --flat` 看当前数据。下面是一个快速速查（以 2026-04 一次实采为准，仅用于了解结构和 id 取值范围，数量可能随图书馆调整增减）：

**校区（premise）**

| id | 名称 |
|---:|---|
| 9 | 学院路校区图书馆 |
| 55 | 沙河校区图书馆 |
| 2 | 沙河校区特色阅览室 |

**楼层（storey）**

- 学院路：`10` 一楼、`11` 二楼、`12` 三楼、`13` 四楼、`14` 五楼、`15` 六楼
- 沙河：`57` 一楼、`58` 二楼、`59` 三楼、`62` 六楼
- 特色：`118` 西区5公寓B1、`51` 西区6公寓B2、`54` 科研楼3#B110

**区域（area，`seat list`/`space book` 用的就是这个 id）**

学院路校区图书馆（采样常见项，共约 13 个 普通座位区 + 研习/考研若干）：

| id | 区域 |
|---:|---|
| 8 | 一楼/一层西阅学空间 |
| 16 | 一楼/一层东报刊阅览室 |
| 18 | 二楼/二层东中文借阅室 |
| 19 | 二楼/二层西知行书斋 |
| 20/21/22 | 三楼/三层南中文借阅室 东/中/西区 |
| 23/24/25 | 四楼/四层中文借阅室 东/中/西区 |
| 27 | 五楼/五层西新书借阅室 |
| 28/29 | 六楼/六层 东/西 中文借阅室 |

沙河校区图书馆：

| id | 区域 |
|---:|---|
| 63/117/64 | 一楼/102、103、104 阅学空间 |
| 65/68/67 | 二楼/201南、二层中央、二层西 |
| 69/71/72/73 | 三楼/301南、314北、三层西、三层中央 |
| 82/83 | 六楼/601南、613北 |

沙河特色阅览室：`52` 西区阅览室B1、`53` 西区阅览室B2、`6` 科研楼阅览室。

> 忘了 id 没关系，直接输名字也行：`lcc seat list --area-id 三层西 --status 1`。多匹配时 CLI 会列出候选。
