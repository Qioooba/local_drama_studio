# Windows 服务端路径契约

本文是 Windows 服务器部署与远程浏览器访问场景的路径审计基线。所有新功能都必须遵守这些约束，不能在业务模块中自行发明另一套路径拼接规则。

## 1. 路径所有权

| 数据 | 权威根目录 | 持久化/API 表示 | 远程浏览器行为 |
| --- | --- | --- | --- |
| 数据库、全局状态 | `data_root` | 服务端绝对根 + 内部固定相对名 | 不接触路径 |
| 项目、图片、角色、音视频、交付物 | `projects_root/<project root_rel>` | 数据库只存规范 POSIX 相对路径 | 通过 `/api/...` 上传、预览、下载 |
| Job、临时上传、运行中产物 | `work_root` | UUID/Job ID 组成的相对路径 | 只使用 artifact API |
| 缩略图、波形、代理视频 | `cache_root` | hash/UUID 组成的相对路径 | 只使用派生内容 API |
| 日志与备份 | `logs_root`、`backups_root` | 服务端内部路径 | 不作为浏览器文件路径 |
| 外部模型库 | `model_library_roots` | 唯一允许保留绝对路径的业务资源 | 只能选择管理员配置根内的普通文件 |
| FFmpeg、Python、ComfyUI 等运行时 | release/instance 配置 | 服务端绝对路径 | 远程浏览器不能任意配置 |

打包安装时，immutable release 位于安装目录，mutable roots 默认位于 instance 目录（Windows 安装器为 ProgramData）。相对配置值以 `INSTANCE_ROOT` 为基准；`${RELEASE_ROOT}` 和 `${INSTANCE_ROOT}` 在加载时展开。进程当前工作目录不是路径契约的一部分。

## 2. 数据库相对路径格式

- 一律使用 `/` 分隔的 POSIX 相对文本；读取时再转换为当前 Windows 文件系统路径。
- 禁止盘符、UNC、开头 `/`、空路径段、`.`、`..`、反斜杠、控制字符和 Windows 非法字符。
- 禁止 `CON`、`PRN`、`AUX`、`NUL`、`COM1`—`COM9`、`LPT1`—`LPT9`，包括带扩展名的形式。
- 禁止尾部空格或点。
- 数据库路径解析必须经过统一的 `controlled_path`；项目根必须经过 `Settings.resolve_project_root`。
- 读取与遍历不能经过 symlink 或 Windows junction/reparse point。

## 3. 上传和文件名

浏览器上传的是字节，不是客户端路径。上传文件名只作为显示提示，进入服务端前执行 Unicode NFKC 和 Windows 可移植文件名规范化；所有实际文件都带 UUID 或内容 hash，不能把客户端文件名当作身份或目标目录。

远程客户端在 `LAN_SERVICE` 下不能调用以下能力：

- 提交服务器任意 `source_path` 导入媒体或文档；
- 打开服务器桌面文件选择器；
- 提交任意 Python/ComfyUI 根目录并修改服务端运行时；
- 登记配置模型库以外的模型文件。

这些操作仍可由服务器本机 loopback 会话完成，以保留桌面部署能力。

## 4. HTTP 内容契约

- UI 的 `<img>`、`<video>`、下载链接只使用同源 `/api/...` URL，不拼接 `D:\...`、`file://` 或后端工作目录。
- API 可返回项目相对位置用于审计和显示；需要向管理员展示服务器落盘位置时，绝对路径必须放在明确命名的 `server_absolute_path` 字段并在 UI 标注为“服务器绝对路径”。内容访问始终另给 `content_url` 或专用下载端点。
- 文档导入使用统一的 `LocalArtifactReference`（`scope/kind/display_name/server_absolute_path/rel_path/download_url/download_filename`）；不得再使用含义模糊的顶层 `stored_source_path`，也不得把服务器绝对路径当作客户端文件路径或内容 URL。
- 项目详情中的 `absolute_root_path` 只表示服务器位置，不能被 UI 当作内容 URL。

## 5. 创建与恢复不变量

- 六个 mutable storage roots 解析后必须互不相同。
- 新项目 code、workflow code、交付 path、项目包成员和上传名必须满足 Windows 可移植规则。
- 项目包展开、导出复验、交付复验、Job artifact、媒体版本、剧本提取文本、许可证证据都按“受控根 + 规范相对路径 + 无 reparse point”重新解析，不能信任数据库或 manifest 中的历史文本。
- 模型扫描使用安全遍历，不进入 symlink/junction；LAN 模式下模型登记和后续许可证冻结都重新核对管理员 allowlist。
- Runtime Host 把相对 install/instance/config/python 路径锚定到明确根目录，Windows 服务的启动目录变化不会改变资源位置。

## 6. 运维检查

迁移或换机时至少确认：

1. `config/config.json` 的 storage roots 指向 instance 数据盘，模型与运行时绝对路径存在于服务器本机。
2. 服务账户对 data/projects/work/cache/logs/backups 有所需权限，对 release 目录只读。
3. `local-drama-host doctor` 与 ready/health 检查通过。
4. 从另一台机器实际完成一次文档、图片和视频上传，以及一次预览与下载；浏览器网络请求只能出现 HTTP URL。
5. 旧数据库中的 `projects.root_rel`、media/artifact/export/delivery 相对路径通过项目健康检查；异常路径应阻断而不是自动越界修复。
