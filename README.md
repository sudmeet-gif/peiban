# 陪伴：本地 AI 聊天与情感陪伴

陪伴是一款 Windows 桌面聊天软件。你可以导入自己有权使用的聊天资料，为不同角色设置昵称、头像、说话方式和声音，并通过文字、图片、表情或语音消息交流。聊天记录和角色记忆分别保存在本机。

程序使用本地模型，不需要 API key。初次准备模型需要下载文件；完成准备后，聊天、图片理解、语音识别和语音生成可以离线运行。软件回复和合成声音可能出错，也不能等同于真实人物。

## 主要功能

- 多角色独立聊天，可编辑昵称、头像、角色设定和语气。
- 导入 Markdown、TXT、DOCX、图片、表情及常见音频；导入聊天时可确认双方发言人。
- 角色记忆可增删修改，支持历史设定恢复、备份和选择数据保存位置。
- 文字回复、表情、录音识别和本地合成语音；可选预设声音，或用多段参考录音克隆音色。
- 图片及录音识别结果可人工确认；聊天窗口内直接播放语音。

## 准备微信聊天记录：推荐先使用 WePort

1. 先使用 [WePort](https://github.com/Panther114/Weport) 按其说明连接自己的微信数据。
2. 在 WePort 的「导出数据」中选择需要的会话和时间范围，格式选择 **Markdown**，并按需要包含图片和语音附件。
3. 保留导出文件与附件的相对目录结构。在陪伴中新建角色，点击「导入聊天资料」，选择导出的 Markdown 文件。
4. 确认哪位发言人是角色、哪位是自己，再开始导入。每个角色分别导入对应资料。

导出能否包含附件取决于微信本机是否仍保存相关文件，详情以 WePort 的文档为准。请只处理有权使用的数据；未经同意，不要公开聊天记录或使用他人的声音。WePort 是独立项目，与本项目分别维护。

## 运行条件与下载

- Windows 10/11，64 位；建议 16 GB 或以上内存，32 GB 使用多个模型时更宽裕。
- 默认可使用 CPU。模型和语音生成速度取决于电脑配置，首次加载通常较慢。
- 本仓库只包含源码和文档，**不包含模型、运行程序或安装包**。请自行下载下列组件，完成后可离线使用。

### 从源码运行

安装 Python 3.12 后，在项目目录的 PowerShell 中执行：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install torch --index-url https://download.pytorch.org/whl/cpu
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-speech.txt
```

然后准备本地运行程序与模型：

| 功能 | 需要准备的文件 | 默认位置 |
| --- | --- | --- |
| 对话 | [llama.cpp Windows CPU 发行版](https://github.com/ggml-org/llama.cpp/releases)及所需 DLL | `runtime/llama-server.exe` |
| 对话 | [Qwen3-4B-GGUF](https://huggingface.co/Qwen/Qwen3-4B-GGUF) 的 `Qwen3-4B-Q4_K_M.gguf` | `models/` |
| 图片理解 | [Qwen3-VL-2B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct-GGUF) 的 `Qwen3VL-2B-Instruct-Q4_K_M.gguf` 和 `mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf` | `models/` |
| 预设声音 | [Qwen3-TTS-12Hz-1.7B-CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice) 完整模型目录 | `models/Qwen3-TTS-12Hz-1.7B-CustomVoice/` |
| 声音克隆 | [Qwen3-TTS-12Hz-0.6B-Base](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-Base) 完整模型目录 | `models/Qwen3-TTS-12Hz-0.6B-Base/` |
| 语音识别 | [SenseVoice ONNX](https://huggingface.co/csukuangfj/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17) 的 `model.int8.onnx` 和 `tokens.txt` | `models/sensevoice/` |

TTS 模型必须下载完整目录，包括 `speech_tokenizer`。程序的「软件与模型设置」也可以手动选择其他本地路径。源代码运行默认使用项目的 `.venv` 作为语音运行环境；如果已有单独的语音环境，可设置 `PEIBAN_TTS_HOME` 或在界面中选择其 Python 和模型路径。

准备完成后运行：

```powershell
.\.venv\Scripts\python.exe main.py
```

新建角色后，在侧栏展开「角色与软件设置」，可以导入资料、管理记忆、配置声音及备份。直接选择音频参考时，请确认有权使用相应声音。

## 测试

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

本机语音、视觉和聊天模型需单独准备后才能进行真实推理测试；自动化测试不下载模型。模型生成的记忆和识别结果可能不准确，请自行检查。

## 隐私与第三方组件

项目不会把你的聊天记录或模型文件提交到仓库；`.gitignore` 排除了本地配置、数据库、资料、音频、模型和运行文件。请勿把真实聊天、声音、密钥或备份加入公开仓库。

语言、视觉和语音组件包括 Qwen、llama.cpp、SenseVoice、sherpa-onnx 等，分别遵循其许可。来源及归属见 [第三方组件说明](THIRD-PARTY-NOTICES.txt)，SenseVoice 模型许可见 [FunASR 模型许可](tools/FunASR_MODEL_LICENSE.txt)。
