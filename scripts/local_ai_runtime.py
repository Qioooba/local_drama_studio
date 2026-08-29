from __future__ import annotations

import argparse
import json
import math
import time
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_MODEL_ROOT = Path(r"F:\AI_Models\LocalDramaStudio")


def _emit(payload: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


def _embedding_smoke(
    model_root: Path,
    requested_texts: list[str] | None = None,
    instruction: str | None = None,
    *,
    include_vectors: bool = False,
) -> dict[str, Any]:
    import torch
    from torch.nn import functional
    from transformers import AutoModel, AutoTokenizer

    model_path = model_root / "PyTorch" / "Qwen3-Embedding-8B"
    started = time.perf_counter()
    tokenizer = AutoTokenizer.from_pretrained(
        model_path, padding_side="left", local_files_only=True
    )
    model = AutoModel.from_pretrained(
        model_path,
        dtype=torch.float16,
        device_map="auto",
        local_files_only=True,
        attn_implementation="sdpa",
    ).eval()
    smoke = not requested_texts
    task = instruction or "给定中文小说检索问题，找出能回答问题的世界观资料"
    texts = requested_texts or [
        f"Instruct: {task}\nQuery:青云宗掌门是谁？",
        "青云宗现任掌门是顾长风，他在天元历一百二十年继任。",
        "林家三女儿在南城经营一家糕点铺，与青云宗没有关系。",
    ]
    if requested_texts and instruction:
        texts = [f"Instruct: {instruction}\nQuery:{text}" for text in requested_texts]
    batch = tokenizer(
        texts, padding=True, truncation=True, max_length=256, return_tensors="pt"
    )
    batch = batch.to(model.device)
    with torch.inference_mode():
        states = model(**batch).last_hidden_state
        pooled = states[:, -1]
        embeddings = functional.normalize(pooled, p=2, dim=1)
    scores = (
        (embeddings[0:1] @ embeddings[1:].T).float().cpu().tolist()[0] if smoke else []
    )
    dimension = int(embeddings.shape[1])
    if dimension != 4096 or (smoke and not scores[0] > scores[1]):
        raise RuntimeError(
            f"embedding smoke assertion failed: dimension={dimension}, scores={scores}"
        )
    result = {
        "task": "embedding",
        "status": "PASS",
        "model": str(model_path),
        "dimension": dimension,
        "count": len(texts),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "network_used": False,
    }
    if smoke:
        result.update({"semantic_score": scores[0], "unrelated_score": scores[1]})
    if include_vectors:
        result["vectors"] = embeddings.float().cpu().tolist()
    return result


def _voxcpm_smoke(
    model_root: Path, audio_output: Path, text: str = "本地导演台配音测试通过。"
) -> dict[str, Any]:
    import numpy as np
    import soundfile as sf
    from voxcpm import VoxCPM

    model_path = model_root / "PyTorch" / "VoxCPM2"
    started = time.perf_counter()
    model = VoxCPM.from_pretrained(
        str(model_path),
        load_denoiser=False,
        local_files_only=True,
        optimize=False,
        device="cuda",
    )
    waveform = model.generate(
        text=text,
        inference_timesteps=4,
        min_len=2,
        max_len=192,
        retry_badcase=False,
    )
    samples = np.asarray(waveform, dtype=np.float32).reshape(-1)
    if (
        samples.size < 4800
        or not np.isfinite(samples).all()
        or float(np.max(np.abs(samples))) <= 0
    ):
        raise RuntimeError("VoxCPM2 produced invalid or empty audio")
    audio_output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(audio_output, samples, 48000, subtype="PCM_16")
    return {
        "task": "voxcpm2",
        "status": "PASS",
        "model": str(model_path),
        "output": str(audio_output.resolve()),
        "sample_rate": 48000,
        "sample_count": int(samples.size),
        "duration_seconds": round(samples.size / 48000, 3),
        "peak": round(float(np.max(np.abs(samples))), 6),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "network_used": False,
    }


def _asr_smoke(model_root: Path, audio_input: Path) -> dict[str, Any]:
    import torch
    from transformers import AutoModelForMultimodalLM, AutoProcessor

    model_path = model_root / "PyTorch" / "Qwen3-ASR-1.7B-hf"
    started = time.perf_counter()
    processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
    model = AutoModelForMultimodalLM.from_pretrained(
        model_path,
        dtype=torch.bfloat16,
        device_map="auto",
        local_files_only=True,
    ).eval()
    inputs = processor.apply_transcription_request(audio=str(audio_input.resolve()))
    inputs = inputs.to(model.device, model.dtype)
    with torch.inference_mode():
        output_ids = model.generate(**inputs, max_new_tokens=128, do_sample=False)
    generated_ids = output_ids[:, inputs["input_ids"].shape[1] :]
    parsed = processor.decode(generated_ids, return_format="parsed")[0]
    transcription = str(parsed.get("transcription") or "").strip()
    if not transcription:
        raise RuntimeError("Qwen3-ASR returned an empty transcription")
    return {
        "task": "asr",
        "status": "PASS",
        "model": str(model_path),
        "audio": str(audio_input.resolve()),
        "language": parsed.get("language"),
        "transcription": transcription,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "network_used": False,
    }


def _alignment_smoke(
    model_root: Path,
    audio_input: Path,
    transcript: str,
    language: str,
) -> dict[str, Any]:
    import torch
    from transformers import AutoModelForTokenClassification, AutoProcessor

    model_path = model_root / "PyTorch" / "Qwen3-ForcedAligner-0.6B-hf"
    started = time.perf_counter()
    processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
    model = AutoModelForTokenClassification.from_pretrained(
        model_path,
        dtype=torch.bfloat16,
        device_map="auto",
        local_files_only=True,
    ).eval()
    inputs, word_lists = processor.prepare_forced_aligner_inputs(
        audio=str(audio_input.resolve()),
        transcript=transcript,
        language=language,
    )
    inputs = inputs.to(model.device, model.dtype)
    with torch.inference_mode():
        outputs = model(**inputs)
    timestamps = processor.decode_forced_alignment(
        logits=outputs.logits,
        input_ids=inputs["input_ids"],
        word_lists=word_lists,
        timestamp_token_id=model.config.timestamp_token_id,
    )[0]
    normalized = [
        {
            "text": str(item["text"]),
            "start_time": round(float(item["start_time"]), 3),
            "end_time": round(float(item["end_time"]), 3),
        }
        for item in timestamps
    ]
    if not normalized or any(
        not math.isfinite(item["start_time"]) or item["end_time"] < item["start_time"]
        for item in normalized
    ):
        raise RuntimeError("Qwen3 ForcedAligner returned invalid timestamps")
    return {
        "task": "alignment",
        "status": "PASS",
        "model": str(model_path),
        "audio": str(audio_input.resolve()),
        "transcript": transcript,
        "language": language,
        "timestamps": normalized,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "network_used": False,
    }


def _ollama_smoke(base_url: str, model_name: str) -> dict[str, Any]:
    payload = json.dumps(
        {
            "model": model_name,
            "prompt": "只输出：本地导演模型测试通过",
            "stream": False,
            "think": False,
            "options": {"num_predict": 64, "temperature": 0, "num_ctx": 2048},
        },
        ensure_ascii=False,
    ).encode("utf-8")
    started = time.perf_counter()
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=600) as response:
        result = json.loads(response.read().decode("utf-8"))
    text = str(result.get("response") or "").strip()
    if text != "本地导演模型测试通过" or result.get("done") is not True:
        raise RuntimeError(f"Ollama smoke assertion failed: {text!r}")
    return {
        "task": "ollama",
        "status": "PASS",
        "model": model_name,
        "base_url": base_url,
        "response": text,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "network_used": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Offline one-shot adapters for the LocalDramaStudio model suite."
    )
    parser.add_argument(
        "task", choices=("embedding", "voxcpm2", "asr", "alignment", "ollama")
    )
    parser.add_argument("--model-root", type=Path, default=DEFAULT_MODEL_ROOT)
    parser.add_argument("--audio-input", type=Path)
    parser.add_argument("--audio-output", type=Path)
    parser.add_argument("--text", action="append")
    parser.add_argument("--instruction")
    parser.add_argument("--include-vectors", action="store_true")
    parser.add_argument("--transcript")
    parser.add_argument("--language", default="Chinese")
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--ollama-model", default="qwen3.8:27b")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.task == "embedding":
        result = _embedding_smoke(
            args.model_root,
            args.text,
            args.instruction,
            include_vectors=args.include_vectors,
        )
    elif args.task == "voxcpm2":
        audio_output = args.audio_output or Path("work/local-model-tests/voxcpm2.wav")
        result = _voxcpm_smoke(
            args.model_root,
            audio_output,
            (args.text or ["本地导演台配音测试通过。"])[0],
        )
    elif args.task == "asr":
        if args.audio_input is None or not args.audio_input.is_file():
            parser.error("--audio-input must name an existing audio file")
        result = _asr_smoke(args.model_root, args.audio_input)
    elif args.task == "alignment":
        if (
            args.audio_input is None
            or not args.audio_input.is_file()
            or not args.transcript
        ):
            parser.error("alignment requires --audio-input and --transcript")
        result = _alignment_smoke(
            args.model_root, args.audio_input, args.transcript, args.language
        )
    else:
        result = _ollama_smoke(args.ollama_base_url, args.ollama_model)
    _emit(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
