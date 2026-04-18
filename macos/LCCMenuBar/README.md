# LCCMenuBar（macOS 菜单栏）

基于本仓库 `lcc` CLI 的菜单栏小工具：番茄钟 + 临时离开/签到/完全离开 + 空闲座位查询与预约。

## 运行

先确保 CLI 能在某个目录里正常工作（该目录有你的 `.lcc.json` / `.env`）。

在本仓库根目录执行：

```bash
cd macos/LCCMenuBar
swift run LCCMenuBar
```

首次启动后，点菜单栏弹窗里的 **设置**（或主窗口工具栏的设置）里填：

- **工作目录**：放 `.lcc.json` / `.env` 的目录
- **调用方式**：优先选 `lcc(已安装)`；没装就选 `python 脚本` 并填 `lcc.py` 路径

备注：番茄钟的“灯光闪烁提示”完全由 `lcc pomo start` 负责；菜单栏应用只负责启动/停止该进程，以及可选的到点系统通知。

## UI 说明

- 菜单栏弹窗：只放快捷操作 + “打开主窗口”
- 主窗口：包含番茄钟/座位/操作的多页界面（正常 Tab）

## “完全离开”

CLI 侧的 `space finish` 需要你提供真实的接口路径：

- 在菜单栏工具里：设置 `完成离开接口路径`（会用 `lcc space finish --path ...` 调用）
- 或者在 CLI 里：写到 `.env` 的 `LCC_SPACE_FINISH_PATH`
