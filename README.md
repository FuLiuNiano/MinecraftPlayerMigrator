# Minecraft Player Migrator v1.1.0

Minecraft 人物迁移工具是一个 Windows 优先的 Minecraft Java 版 Player NBT 迁移工具。它适合把服务器 ZIP 或 world 文件夹中的人物，迁移到隔离的本地单人 world。

## 普通用户使用

1. 关闭 Minecraft、PCL2/HMCL/Prism 等启动器和 Java 游戏进程。
2. 启动 `MinecraftPlayerMigrator.exe`。
3. 第一步选择服务器 world/ZIP、本地目标 world；也可以直接拖入路径。
4. 扫描后选择人物，检查用户名、UUID、背包、装备、等级、坐标、ForgeCaps 和 Curios。
5. 确认本地 UUID 证据、迁移预览和备份空间后开始迁移。
6. 程序会先创建完整 world 备份，再同步 `playerdata/<目标 UUID>.dat` 和 `level.dat -> Data -> Player`。
7. 看到成功校验后，再启动 Minecraft 测试目标 world。

目标 world 写入前必须退出游戏。程序不会上传存档，也不会执行 ZIP 中的程序。

## v1.1 GUI 与版本规则

v1.1 GUI 使用五步流程：来源扫描、服务器人物、目标 world 与版本、兼容性报告、事务进度与结果。来源和目标的 `level.dat -> Data -> Version -> Name` 优先用于版本比较，缺少名称时才使用 `DataVersion`；`MATCH` 以外的 `MISMATCH`、`UNKNOWN`、`CONFLICT` 会阻止迁移，不会猜测版本。

迁移按钮只调用核心层的 `build_full_plan()` 和 `execute_full_migration()`。GUI 不直接写 NBT、UUID、备份或任务文件。ForgeCaps、Curios/Accessories 等属于完整 Player NBT，随人物整体迁移，不作为会丢失数据的白名单字段。

正常模式包含 Player、advancements、stats；FTB Quests、FTB Teams、Waystones、Ending Library、Cosmetic Armor 属于实验模块，只有扫描为可验证时才默认启用，否则明确跳过并显示原因。当前已针对 Forge 1.20.1 隔离测试环境完成核心与事务闭环验证；其他版本必须先经过只读兼容性检查和隔离测试。

## 当前迁移范围

迁移以源 Player NBT 为主体，保留完整人物字段，包括 Inventory、ArmorItems、HandItems、EnderItems、位置、维度、旋转、经验、生命值、饥饿值、abilities、出生点、ForgeCaps、Curios/Accessories 和未知的模组人物字段；只修改 Player 自身 UUID。

未通过只读兼容性检查的进度模块不会被迁移；无论模块是否启用，以下内容都不会被改写：

- 未验证或不支持的 FTB Quests、FTB Teams、Waystones、Ending Library、Cosmetic Armor 结构
- 世界区块、serverconfig、任务数据库
- 其他玩家、宠物 owner UUID、实体 UUID 或未知模组内部 UUID

## 安全与恢复

- ZIP 路径会进行 ZIP Slip 和符号链接检查。
- 正式文件使用临时文件重新解析后原子替换。
- 迁移前创建带时间戳的完整 world 备份，并拒绝覆盖已有目标备份。
- 写入失败时保留完整备份并尽力恢复两个正式文件。
- 日志位于 `%LOCALAPPDATA%\\MinecraftPlayerMigrator\\logs\\minecraft_player_migrator.log`。

## 高级信息

高级模式可以查看 NBT 来源、UUID 证据、背包和 capability 摘要。身份或版本证据冲突时必须修正路径或存档，GUI 不提供绕过安全阻断的强制迁移按钮。NBTExplorer 只是可选的人工作品查看工具，不是本程序依赖；请只从可信来源安装。

## 开发测试

```powershell
.venv\Scripts\python.exe -m pytest -v
.venv\Scripts\python.exe -m ruff check .
```

开发阶段不使用正式 Minecraft world 做自动测试；GUI smoke test 使用 offscreen Qt。发布前请在隔离副本中完成真实游戏加载、保存和退出后的只读复检。

## 发布包

`dist\\MinecraftPlayerMigrator\\` 是 Windows x64 one-folder 发布目录，包含程序及第三方依赖，不需要用户安装 Python 或项目 `.venv`。v1.1 GUI 完成后再单独执行打包验收；当前开发阶段不自动覆盖旧发布包。

请完整解压发布 ZIP 后运行，不能只复制 `MinecraftPlayerMigrator.exe`。程序完全在本地处理，不上传任何存档。

核心状态：V1 Core Verified；v1.1.0 发布候选包已完成 onedir、三种 Windows 路径和无 Python 环境自检。正式存档仍建议先创建额外备份并在隔离副本上验证。
