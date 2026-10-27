# YouTube 下载助手

一个基于 Python / Tkinter 的 Windows 桌面客户端。输入 YouTube 视频链接后，可下载视频、音频以及可用字幕，并在窗口中管理任务。

当前版本：初版。任务逻辑已通过自动化测试，尚未完成全部真实网络场景的端到端验证。

## 功能

- 首次运行自动下载 yt-dlp、FFmpeg / FFprobe 和 Deno，无需手动配置 PATH。
- 支持单个 YouTube 视频链接；包含播放列表参数时只下载当前视频。
- 视频与音频合并保存为 MKV，支持最佳画质或指定画质上限。
- 支持视频与字幕同时下载，或只下载字幕；字幕转换为 SRT。
- 选择中文、英文或所有可用字幕，包括自动字幕。
- 串行下载队列，显示进度、速度和剩余时间。
- 支持暂停、继续 / 重试、取消、打开文件夹、调用默认播放器、移除历史记录。
- 保存任务历史，重启后可恢复中断任务。
- 可为视频下载填写 HTTP / SOCKS 代理。

## 使用

1. 双击打包后的 `YouTubeDownloadAssistant.exe`，或从源码启动。
2. 等待“下载工具已就绪”。首次下载组件可能需要几分钟。
3. 输入 YouTube 视频链接，选择保存文件夹、下载内容、画质和字幕语言。
4. 点击“＋ 添加下载”。选中队列中的任务后，可暂停、重试或打开文件夹。

保存路径支持中文和空格，不需要自行添加引号。仅字幕模式不会下载视频。字幕语言取决于视频实际提供的内容；客户端不会自行生成翻译。

## 从源码运行

环境：Windows 10 / 11，64 位；Python 3.9 或更新版本，需包含 Tkinter。推荐 Python 3.12。

```powershell
python app.py
```

程序自身只使用 Python 标准库。首次启动时通过 HTTPS 从以下官方 GitHub Release 获取外部工具：

- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [FFmpeg Builds](https://github.com/yt-dlp/FFmpeg-Builds)
- [Deno](https://github.com/denoland/deno)

如果 GitHub Release 元数据提供 SHA-256 摘要，安装器会验证下载文件。所有组件在落盘前执行版本检查。

## 打包 EXE

```powershell
python -m pip install -r requirements-build.txt
python -m PyInstaller --noconfirm --clean --onefile --windowed --name YouTubeDownloadAssistant app.py
```

生成文件：`dist/YouTubeDownloadAssistant.exe`。目标电脑不需要另外安装 Python。

仓库提供 Windows GitHub Actions 工作流：手动运行或向 main 推送后，执行测试并构建 EXE。成功后可从对应运行的 Artifacts 下载 `YouTubeDownloadAssistant-windows-x64`。

## 测试

```powershell
python -m unittest test_app -v
```

覆盖路径与参数处理、URL 校验、历史恢复、进程输出的成功 / 失败状态、字幕缺失提示及暂停状态。下载进程使用模拟对象；测试通过不代表已验证 YouTube 当前网络访问、所有格式或字幕接口。

## 数据与工具位置

```text
%LOCALAPPDATA%\YouTubeDownloadAssistant\
├── tools\          # 自动安装的外部工具
├── state.json      # 设置与下载历史
└── download.log    # 下载日志
```

设置和历史可能包含视频地址、保存路径及代理地址，只保存在本机。不要在代理地址中放入不必要的凭据，也不要公开上传这些数据文件。

## 当前限制与排错

- 组件安装要求网络可访问 GitHub；视频下载要求网络可访问 YouTube。
- 窗口中的代理设置目前只用于视频下载；组件安装使用系统 / 环境代理。
- 暂停会终止当前下载进程并保留已有文件，继续时由 yt-dlp 尝试断点续传。合并或转换阶段可能需要重新执行。
- 取消和移除记录均不会删除已下载文件。
- 目前不提供批量播放列表、账号登录、cookies 导入、自动翻译或下载限速。
- 工具已存在时不会自动升级；工具版本失效时，在关闭客户端后备份并移走 tools 目录，重新启动后会下载新版本。
- 工具安装失败时检查网络后点击“安装 / 检查工具”；失败任务需选择“继续 / 重试”。
- 如字幕没有匹配语言，会提示检查日志及保存目录。

仅下载你有权保存的内容，并遵守相关平台条款。外部工具仍受各自项目许可证约束；本仓库没有将它们的二进制文件打包进源码。
