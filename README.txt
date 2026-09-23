Minecraft Player Migrator v1.1.0
================================

这是一个 Windows x64 的 Minecraft Java 版人物迁移工具。请在使用前关闭 Minecraft、启动器和 Java 游戏进程。

正式版本：Minecraft Player Migrator v1.1.0
状态：Release Verified
Full Player Progress Migration = End-to-End Game Verified
GUI Migration Flow = Real-World End-to-End Verified

Windows 下载：GitHub Releases -> v1.1.0
发布包：MinecraftPlayerMigrator-v1.1.0-win-x64.zip
请完整解压 ZIP 后使用，不能只复制 MinecraftPlayerMigrator.exe。

使用步骤：
1. 启动 MinecraftPlayerMigrator.exe。
2. 选择服务器 world 文件夹或 ZIP，并选择服务器中的玩家。
3. 选择本地目标 world。
4. 确认服务器与本地 Minecraft Java 版本一致，并查看兼容性报告。
5. 点击开始迁移；程序先创建完整备份，再同步目标 playerdata 和 level.dat 的 Data -> Player。
6. 看到成功校验后再进入 Minecraft 检查目标 world。

v1.1 GUI 使用五步流程，并在迁移前比较来源与目标 level.dat 的 Minecraft 版本。只有 MATCH 允许迁移；MISMATCH、UNKNOWN、CONFLICT 会阻止操作，不会猜测版本。GUI 只调用核心层迁移计划和完整事务 API，不直接写 NBT。

版本要求：服务器 Minecraft Java 版本必须与本地 Minecraft Java 版本完全一致。当前完整实机验证环境为 Minecraft Java 1.20.1、Forge、Windows x64；暂不支持跨 Minecraft 版本转换。

迁移范围：完整 Player NBT，包括背包、装备、EnderItems、位置、经验、生命值、饥饿值、ForgeCaps、Curios/Accessories 和未知人物字段；只修改 Player 自身 UUID。

通用核心包括 Player、Advancements、Stats。FTB Quests、FTB Teams、Waystones、Ending Library、Cosmetic Armor 属于实验性扩展，仅在只读兼容性检查通过时处理。

未通过只读兼容性检查的 FTB Quests、FTB Teams、Waystones、Ending Library、Cosmetic Armor 不会被迁移；世界区块、serverconfig、任务数据库和其他玩家数据也不会被改写。程序不上传数据，也不执行 ZIP 中程序。

迁移前会创建带时间戳的完整 world 备份。日志位置：
%LOCALAPPDATA%\\MinecraftPlayerMigrator\\logs\\minecraft_player_migrator.log

如果迁移失败，程序会尝试自动回滚。若提示“自动恢复未完全成功”，不要启动该 world，应从完整备份恢复。

开发测试：`.venv\\Scripts\\python.exe -m pytest -v` 和 `.venv\\Scripts\\python.exe -m ruff check .`。自动测试只使用隔离测试数据，不使用正式 world。v1.1.0 已完成完整玩家进度和 GUI 实机端到端验证。

发布包说明：本程序是 onedir 发布版。请完整解压发布 ZIP 后运行，不能只复制 MinecraftPlayerMigrator.exe；程序不需要安装 Python、PySide6 或项目 `.venv`。

程序完全在本地处理，不上传任何存档或 UUID。当前版本支持完整 Player NBT，并按只读兼容性报告处理 advancements、stats 及可验证的实验进度模块；不支持或结构异常的模块会明确跳过。

正式状态：Minecraft Player Migrator v1.1.0 Release Verified。请先创建额外备份，并在隔离测试 world 上验证重要存档。
