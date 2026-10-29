"""Local-only inference adapters. No user content is sent to remote services."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request


class ModelError(RuntimeError):
    """An actionable problem with a local model or runtime."""


# Optionally point to an existing speech environment; all bundled models are
# otherwise resolved relative to the application directory.
_TOOL = Path(os.environ.get("PEIBAN_TTS_HOME", "")) if os.environ.get("PEIBAN_TTS_HOME") else None
_OFFLINE_ENV = {"PYTHONUTF8": "1", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1",
                "HF_DATASETS_OFFLINE": "1", "NO_PROXY": "127.0.0.1,localhost"}
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
# This client communicates exclusively with the local HTTP inference server.
# Construct only the HTTP handlers: build_opener() also initializes HTTPS and
# may fail at startup when a machine has an invalid certificate environment.
_OPENER = urllib.request.OpenerDirector()
_OPENER.add_handler(urllib.request.HTTPHandler())
_OPENER.add_handler(urllib.request.HTTPDefaultErrorHandler())
_OPENER.add_handler(urllib.request.HTTPErrorProcessor())


def _require_file(value, label):
    if not value or not Path(value).is_file():
        raise ModelError(f"找不到{label}：{value or '尚未设置'}。请在模型设置中选择本地文件。")
    return str(Path(value).resolve())


def _require_directory(value, label):
    if not value or not Path(value).is_dir():
        raise ModelError(f"找不到{label}：{value or '尚未设置'}。请在模型设置中选择本地模型目录。")
    if not _tts_ready(value):
        raise ModelError(f"{label}尚未下载完整：{value}。请完成模型下载后重试。")
    return str(Path(value).resolve())


def _tts_ready(value):
    if not value:
        return False
    root = Path(value)
    return all((root / p).is_file() for p in ("config.json", "model.safetensors", "tokenizer_config.json", "speech_tokenizer/model.safetensors"))


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class LocalModels:
    """Configuration keys: llama_server, chat_model, vision_model, vision_mmproj,
    tts_python, tts_custom_model, tts_base_model, asr_model, asr_tokens, gpu_layers.
    All model paths are local paths. Call close() when the application exits.
    """

    def __init__(self, settings=None):
        self.settings = dict(settings or {})
        root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parent.parent
        self.settings.setdefault("llama_server", str(root / "runtime" / "llama-server.exe"))
        chat_model = root / "models" / "Qwen3-4B-Q4_K_M.gguf"
        backup_model = root / "models" / "Qwen3-0.6B-Q8_0.gguf"
        if not chat_model.is_file() and backup_model.is_file():
            chat_model = backup_model
        self.settings.setdefault("chat_model", str(chat_model))
        self.settings.setdefault("vision_model", str(root / "models" / "Qwen3VL-2B-Instruct-Q4_K_M.gguf"))
        self.settings.setdefault("vision_mmproj", str(root / "models" / "mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf"))
        portable_python = root / "speech_runtime" / "python.exe"
        portable_custom = root / "models" / "Qwen3-TTS-12Hz-1.7B-CustomVoice"
        portable_asr = root / "models" / "sensevoice"
        tool = _TOOL or root
        self.settings.setdefault("tts_python", str(portable_python if portable_python.is_file() else tool / ".venv" / "Scripts" / "python.exe"))
        self.settings.setdefault("tts_custom_model", str(portable_custom if portable_custom.is_dir() else tool / "models" / "Qwen3-TTS-12Hz-1.7B-CustomVoice"))
        self.settings.setdefault("tts_base_model", str(root / "models" / "Qwen3-TTS-12Hz-0.6B-Base"))
        self.settings.setdefault("asr_model", str((portable_asr if portable_asr.is_dir() else tool / "qa-models" / "sensevoice") / "model.int8.onnx"))
        self.settings.setdefault("asr_tokens", str((portable_asr if portable_asr.is_dir() else tool / "qa-models" / "sensevoice") / "tokens.txt"))
        self._process = None
        self._kind = None
        self._port = None
        self._lock = threading.RLock()

    def status(self):
        s = self.settings
        return {"llama_server": Path(s.get("llama_server", "")).is_file(),
                "chat_model": Path(s.get("chat_model", "")).is_file(),
                "vision_model": Path(s.get("vision_model", "")).is_file() and Path(s.get("vision_mmproj", "")).is_file(),
                "tts_preset": Path(s["tts_python"]).is_file() and _tts_ready(s["tts_custom_model"]),
                "tts_clone": Path(s["tts_python"]).is_file() and _tts_ready(s["tts_base_model"]),
                "asr": Path(s["tts_python"]).is_file() and Path(s["asr_model"]).is_file() and Path(s["asr_tokens"]).is_file()}

    def close(self):
        with self._lock:
            process, self._process = self._process, None
            self._kind = self._port = None
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    def _start(self, kind, cancel_event=None):
        if self._process and self._process.poll() is None and self._kind == kind:
            return
        self.close()
        s = self.settings
        exe = _require_file(s.get("llama_server"), "llama-server 程序")
        key = "vision_model" if kind == "vision" else "chat_model"
        model = _require_file(s.get(key), "视觉模型" if kind == "vision" else "对话模型")
        args = [exe, "--host", "127.0.0.1", "--port", str(_port()), "-m", model,
                "-c", str(int(s.get("context_size", 4096))), "-ngl", str(int(s.get("gpu_layers", 0)))]
        if kind == "vision":
            args += ["--mmproj", _require_file(s.get("vision_mmproj"), "视觉模型投影文件")]
        self._port = int(args[args.index("--port") + 1])
        try:
            self._process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                             stderr=subprocess.DEVNULL, creationflags=_NO_WINDOW,
                                             env={**os.environ, **_OFFLINE_ENV})
        except OSError as exc:
            raise ModelError(f"无法启动本地模型程序：{exc}") from exc
        self._kind = kind
        deadline = time.monotonic() + int(s.get("startup_timeout", 180))
        while time.monotonic() < deadline:
            if cancel_event and cancel_event.is_set():
                self.close()
                raise ModelError("已取消模型加载。")
            if self._process.poll() is not None:
                self.close()
                raise ModelError("本地模型程序启动失败，请检查模型文件和程序是否匹配；可尝试将 GPU 层数设为 0。")
            try:
                with _OPENER.open(f"http://127.0.0.1:{self._port}/health", timeout=1) as response:
                    if response.status == 200:
                        return
            except (OSError, urllib.error.URLError):
                time.sleep(.3)
        self.close()
        raise ModelError("本地模型加载超时，请检查可用内存和模型文件。")

    def _request(self, messages, stream, **kwargs):
        body = {"messages": messages, "stream": stream, "temperature": 0.7,
                "max_tokens": int(self.settings.get("max_tokens", 512)),
                "chat_template_kwargs": {"enable_thinking": False}, **kwargs}
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        return urllib.request.Request(f"http://127.0.0.1:{self._port}/v1/chat/completions", data=data,
                                      headers={"Content-Type": "application/json"}, method="POST")

    def stream_chat(self, messages, cancel_event=None):
        """Yield text fragments; messages use OpenAI role/content dictionaries."""
        with self._lock:
            if cancel_event and cancel_event.is_set():
                return
            self._start("chat", cancel_event)
            try:
                with _OPENER.open(self._request(messages, True), timeout=180) as response:
                    finished = threading.Event()
                    if cancel_event:
                        def interrupt_when_cancelled():
                            while not finished.wait(0.2):
                                if cancel_event.is_set():
                                    try:
                                        response.fp.raw._sock.shutdown(socket.SHUT_RDWR)
                                    except (AttributeError, OSError):
                                        pass
                                    return
                        threading.Thread(target=interrupt_when_cancelled, daemon=True).start()
                    try:
                        for line in response:
                            if cancel_event and cancel_event.is_set():
                                return
                            line = line.decode("utf-8").strip()
                            if not line.startswith("data:"):
                                continue
                            payload = line[5:].strip()
                            if payload == "[DONE]":
                                break
                            obj = json.loads(payload)
                            if obj.get("error"):
                                raise ModelError(str(obj["error"]))
                            choices = obj.get("choices", [])
                            if choices:
                                content = choices[0].get("delta", {}).get("content")
                                if content:
                                    yield content
                    finally:
                        finished.set()
            except (OSError, ValueError, urllib.error.URLError) as exc:
                if cancel_event and cancel_event.is_set():
                    return
                raise ModelError(f"本地对话模型请求失败：{exc}") from exc

    def describe_image(self, path):
        """Return a model-generated description, not verified facts."""
        path = Path(_require_file(path, "图片"))
        if path.stat().st_size > 20 * 1024 * 1024:
            raise ModelError("图片超过 20 MB，请先缩小后再识别。")
        mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                ".webp": "image/webp", ".gif": "image/gif"}.get(path.suffix.lower())
        if not mime:
            raise ModelError("图片理解支持 JPG、PNG、WEBP 或 GIF。")
        image = base64.b64encode(path.read_bytes()).decode("ascii")
        messages = [{"role": "user", "content": [{"type": "text", "text": "请用中文简洁描述图片内容和可辨认的文字；无法辨认的地方请说明。"},
                    {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{image}"}}]}]
        with self._lock:
            self._start("vision")
            try:
                with _OPENER.open(self._request(messages, False), timeout=300) as response:
                    obj = json.load(response)
                return obj["choices"][0]["message"]["content"].strip()
            except (OSError, ValueError, KeyError, IndexError, urllib.error.URLError) as exc:
                raise ModelError(f"本地图像理解失败：{exc}") from exc

    def _worker(self, action, payload, timeout=1800, cancel_event=None):
        python = _require_file(self.settings.get("tts_python"), "语音工具 Python")
        script = Path(__file__).with_name("model_worker.py")
        try:
            with self._lock:
                # The text model is unloaded before running large speech models.
                self.close()
                process = subprocess.Popen([python, str(script), action], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                           creationflags=_NO_WINDOW, env={**os.environ, **_OFFLINE_ENV})
                deadline = time.monotonic() + timeout
                input_data = json.dumps(payload, ensure_ascii=False)
                while True:
                    if cancel_event and cancel_event.is_set():
                        process.kill()
                        process.communicate()
                        raise ModelError("已取消语音处理。")
                    if time.monotonic() >= deadline:
                        process.kill()
                        process.communicate()
                        raise ModelError("本地语音处理超时。")
                    try:
                        stdout, stderr = process.communicate(input=input_data, timeout=0.25)
                        break
                    except subprocess.TimeoutExpired:
                        input_data = None
        except OSError as exc:
            raise ModelError(f"本地语音处理未能完成：{exc}") from exc
        marker = "COMPANION_RESULT="
        lines = [x[len(marker):] for x in stdout.splitlines() if x.startswith(marker)]
        if process.returncode or not lines:
            detail = (stderr or stdout).strip()[-1200:]
            raise ModelError(f"本地语音处理失败：{detail or '请检查模型和工具环境'}")
        try:
            return json.loads(lines[-1])
        except ValueError as exc:
            raise ModelError("语音工具返回了无法识别的结果。") from exc

    def synthesize(self, text, output_path, speaker="Vivian", reference_audio=None, reference_text=None, consent=False, cancel_event=None):
        if not text.strip():
            raise ModelError("没有可生成的文字。")
        clone = bool(reference_audio)
        if clone and not consent:
            raise ModelError("使用参考录音前，请确认你有权使用该声音。")
        model = _require_directory(self.settings.get("tts_base_model" if clone else "tts_custom_model"),
                                   "声音克隆 Base 模型" if clone else "预设声音模型")
        if clone:
            references = reference_audio if isinstance(reference_audio, (list, tuple)) else [reference_audio]
            reference_audio = [_require_file(path, "参考录音") for path in references]
        destination = Path(output_path).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._worker("tts", {"model": model, "text": text, "output": str(destination), "speaker": speaker,
                             "reference_audio": reference_audio, "reference_text": reference_text or ""},
                     cancel_event=cancel_event)
        if not destination.is_file() or destination.stat().st_size == 0:
            raise ModelError("声音生成未产生音频文件。")
        return str(destination)

    def transcribe(self, path, cancel_event=None):
        audio = _require_file(path, "录音")
        model = _require_file(self.settings.get("asr_model"), "语音识别模型")
        tokens = _require_file(self.settings.get("asr_tokens"), "语音识别词表")
        return self._worker("asr", {"audio": audio, "model": model, "tokens": tokens}, cancel_event=cancel_event)["text"]
