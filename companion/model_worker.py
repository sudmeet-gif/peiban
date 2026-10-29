"""Isolated speech worker executed by the existing Qwen3-TTS Python runtime."""
import json
import math
import re
import sys


def _combine_reference_audio(paths):
    """Combine samples of the same speaker into a bounded 24 kHz reference."""
    import numpy as np
    import soundfile as sf
    from scipy.signal import resample_poly

    target_rate = 24000
    # Bound memory and inference latency, while taking a portion of each sample.
    seconds_per_sample = min(15.0, 60.0 / len(paths))
    chunks = []
    for path in paths:
        info = sf.info(path)
        data, rate = sf.read(path, dtype="float32", always_2d=True,
                             frames=max(1, int(info.samplerate * seconds_per_sample)))
        if not len(data):
            raise ValueError('参考音频为空：' + path)
        data = data.mean(axis=1)
        if rate != target_rate:
            common = math.gcd(int(rate), target_rate)
            data = resample_poly(data, target_rate // common, int(rate) // common).astype(np.float32)
        chunks.append(data)
        chunks.append(np.zeros(target_rate // 10, dtype=np.float32))
    return np.concatenate(chunks[:-1]), target_rate


def tts(data):
    import soundfile as sf
    import torch
    from qwen_tts import Qwen3TTSModel

    model = Qwen3TTSModel.from_pretrained(data["model"], device_map="cpu", dtype=torch.float32)
    if data.get("reference_audio"):
        refs = data["reference_audio"]
        refs = refs if isinstance(refs, list) else [refs]
        if len(refs) > 1:
            audio = _combine_reference_audio(refs)
            ref_text = ''  # A single transcript cannot describe multiple independent recordings.
        else:
            audio = refs[0]
            ref_text = data.get("reference_text", "").strip()
        wavs, sr = model.generate_voice_clone(text=data["text"], language="Chinese",
                                             ref_audio=audio, ref_text=ref_text or None,
                                             x_vector_only_mode=not bool(ref_text))
    else:
        wavs, sr = model.generate_custom_voice(text=data["text"], language="Chinese", speaker=data["speaker"])
    sf.write(data["output"], wavs[0], sr)
    return {"output": data["output"], "sample_rate": sr}


def asr(data):
    import numpy as np
    import soundfile as sf
    import sherpa_onnx
    from scipy.signal import resample_poly

    samples, rate = sf.read(data["audio"], dtype="float32", always_2d=True)
    samples = samples.mean(axis=1)
    if rate != 16000:
        common = math.gcd(int(rate), 16000)
        samples = resample_poly(samples, 16000 // common, int(rate) // common).astype(np.float32)
    if len(samples) == 0:
        raise ValueError("录音为空。")
    recognizer = sherpa_onnx.OfflineRecognizer.from_sense_voice(
        model=data["model"], tokens=data["tokens"], language="auto", use_itn=True, num_threads=4)
    stream = recognizer.create_stream()
    stream.accept_waveform(16000, samples)
    recognizer.decode_stream(stream)
    # SenseVoice outputs language, emotion and acoustic event tags as control tokens.
    text = re.sub(r"<\|[^|]*\|>", "", stream.result.text).strip()
    return {"text": text}


if __name__ == "__main__":
    data = json.load(sys.stdin)
    if sys.argv[1] == "tts":
        value = tts(data)
    elif sys.argv[1] == "asr":
        value = asr(data)
    else:
        raise ValueError("Unknown action")
    print("COMPANION_RESULT=" + json.dumps(value, ensure_ascii=False), flush=True)
