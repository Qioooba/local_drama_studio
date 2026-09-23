from __future__ import annotations

import argparse
import base64
import json
import math
import time
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_MODEL_ROOT = Path(r"F:\AI_Models\LocalDramaStudio")


def _decode_text(value: str) -> str:
    """Decode the ASCII-safe text transport used by the Windows parent."""
    return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")


def _emit(payload: dict[str, Any], output: Path | None) -> None:
    rendered = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if output is not None:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    # Windows child processes can still inherit an OEM/ANSI stdout code page
    # despite PYTHONIOENCODING.  Keep the machine-readable subprocess channel
    # ASCII-only so the parent can always decode JSON as UTF-8 and recover the
    # original Unicode text from \u escapes.  Explicit output files remain
    # human-readable UTF-8.
    print(json.dumps(payload, ensure_ascii=True, indent=2), end="\n")


def _load_voxcpm(model_root: Path):
    """Load the narration model once for a whole batch of segments."""

    from voxcpm import VoxCPM

    model_path = model_root / "PyTorch" / "VoxCPM2"
    model = VoxCPM.from_pretrained(
        str(model_path),
        load_denoiser=False,
        local_files_only=True,
        optimize=False,
        device="cuda",
    )
    return model, model_path


def _voxcpm_batch(
    model_root: Path,
    manifest: Path,
    output_dir: Path,
    *,
    prompt_audio: Path | None = None,
    prompt_text: str | None = None,
    speed: float | None = None,
) -> dict[str, Any]:
    """Synthesise many narration segments in one process.

    Loading VoxCPM2 costs tens of seconds; doing it once per segment made a
    five-minute narration take over an hour.  The batch task keeps the model
    resident and writes one WAV per requested item, reporting per-item status so
    one bad segment cannot discard the rest of the batch.
    """

    import inspect

    import numpy as np
    import soundfile as sf

    items = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if not isinstance(items, list) or not items:
        raise RuntimeError("voxcpm2-batch manifest must be a non-empty JSON array")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    model, model_path = _load_voxcpm(model_root)
    load_seconds = round(time.perf_counter() - started, 3)
    parameter_names = inspect.signature(model.generate).parameters
    results: list[dict[str, Any]] = []
    for item in items:
        item_id = str(item.get("id") or "")
        text = str(item.get("text") or "")
        target = output_dir / f"{item_id}.wav"
        item_started = time.perf_counter()
        try:
            if not text.strip():
                raise RuntimeError("empty narration text")
            generate_kwargs: dict[str, Any] = {
                "text": text,
                "inference_timesteps": 4,
                "min_len": 2,
                "max_len": 192,
                "retry_badcase": False,
            }
            if prompt_audio is not None:
                if "prompt_wav_path" not in parameter_names:
                    raise RuntimeError("VOXCPM_PROMPT_UNSUPPORTED: installed VoxCPM lacks prompt_wav_path")
                generate_kwargs["prompt_wav_path"] = str(Path(prompt_audio).resolve())
                if prompt_text and "prompt_text" in parameter_names:
                    generate_kwargs["prompt_text"] = prompt_text
            applied_speed_natively = False
            if speed is not None and float(speed) != 1.0:
                for candidate in ("speed", "speech_rate", "rate"):
                    if candidate in parameter_names:
                        generate_kwargs[candidate] = float(speed)
                        applied_speed_natively = True
                        break
            waveform = model.generate(**generate_kwargs)
            samples = np.asarray(waveform, dtype=np.float32).reshape(-1)
            if samples.size < 4800 or not np.isfinite(samples).all() or float(np.max(np.abs(samples))) <= 0:
                raise RuntimeError("VoxCPM2 produced invalid or empty audio")
            sf.write(target, samples, 48000, subtype="PCM_16")
            results.append(
                {
                    "id": item_id,
                    "status": "PASS",
                    "output": str(target.resolve()),
                    "sample_rate": 48000,
                    "sample_count": int(samples.size),
                    "duration_seconds": round(samples.size / 48000, 3),
                    "elapsed_seconds": round(time.perf_counter() - item_started, 3),
                    "speed_applied_natively": applied_speed_natively,
                    "error": None,
                }
            )
        except Exception as error:  # one bad segment must not discard the batch
            results.append({"id": item_id, "status": "FAILED", "error": f"{type(error).__name__}: {error}"})
    failed = [item for item in results if item["status"] != "PASS"]
    return {
        "task": "voxcpm2-batch",
        "status": "PASS",
        "model": str(model_path),
        "items": results,
        "item_count": len(results),
        "failed_count": len(failed),
        "model_load_seconds": load_seconds,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "prompt_audio": None if prompt_audio is None else str(Path(prompt_audio).resolve()),
        "requested_speed": None if speed is None else round(float(speed), 6),
        "network_used": False,
    }


def _alignment_batch(
    model_root: Path,
    manifest: Path,
    language: str,
) -> dict[str, Any]:
    """Force-align many take files in one process, keeping the aligner resident."""

    import torch
    from transformers import AutoModelForTokenClassification, AutoProcessor

    items = json.loads(Path(manifest).read_text(encoding="utf-8"))
    if not isinstance(items, list) or not items:
        raise RuntimeError("alignment-batch manifest must be a non-empty JSON array")
    model_path = model_root / "PyTorch" / "Qwen3-ForcedAligner-0.6B-hf"
    started = time.perf_counter()
    processor = AutoProcessor.from_pretrained(model_path, local_files_only=True)
    model = AutoModelForTokenClassification.from_pretrained(
        model_path,
        dtype=torch.bfloat16,
        device_map="auto",
        local_files_only=True,
    ).eval()
    load_seconds = round(time.perf_counter() - started, 3)
    results: list[dict[str, Any]] = []
    for item in items:
        item_id = str(item.get("id") or "")
        audio = str(item.get("audio") or "")
        transcript = str(item.get("transcript") or "")
        item_started = time.perf_counter()
        try:
            if not Path(audio).is_file() or not transcript.strip():
                raise RuntimeError("alignment item needs an existing audio file and a transcript")
            inputs, word_lists = processor.prepare_forced_aligner_inputs(
                audio=str(Path(audio).resolve()),
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
                    "text": str(entry["text"]),
                    "start_time": round(float(entry["start_time"]), 3),
                    "end_time": round(float(entry["end_time"]), 3),
                }
                for entry in timestamps
            ]
            if not normalized or any(
                not math.isfinite(entry["start_time"]) or entry["end_time"] < entry["start_time"]
                for entry in normalized
            ):
                raise RuntimeError("Qwen3 ForcedAligner returned invalid timestamps")
            results.append(
                {
                    "id": item_id,
                    "status": "PASS",
                    "timestamps": normalized,
                    "elapsed_seconds": round(time.perf_counter() - item_started, 3),
                    "error": None,
                }
            )
        except Exception as error:
            results.append({"id": item_id, "status": "FAILED", "error": f"{type(error).__name__}: {error}"})
    failed = [entry for entry in results if entry["status"] != "PASS"]
    return {
        "task": "alignment-batch",
        "status": "PASS",
        "model": str(model_path),
        "items": results,
        "item_count": len(results),
        "failed_count": len(failed),
        "model_load_seconds": load_seconds,
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "language": language,
        "network_used": False,
    }


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
    model_root: Path,
    audio_output: Path,
    text: str = "本地导演台配音测试通过。",
    *,
    prompt_audio: Path | None = None,
    prompt_text: str | None = None,
    speed: float | None = None,
) -> dict[str, Any]:
    import inspect

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
    generate_kwargs: dict[str, Any] = {
        "text": text,
        "inference_timesteps": 4,
        "min_len": 2,
        "max_len": 192,
        "retry_badcase": False,
    }
    if prompt_audio is not None:
        # Zero-shot cloning is only requested by production voice profiles; the
        # smoke default (no --prompt-audio) must keep working on every install.
        parameter_names = inspect.signature(model.generate).parameters
        if "prompt_wav_path" not in parameter_names:
            raise RuntimeError("VOXCPM_PROMPT_UNSUPPORTED: installed VoxCPM lacks prompt_wav_path")
        generate_kwargs["prompt_wav_path"] = str(prompt_audio.resolve())
        if prompt_text:
            if "prompt_text" not in parameter_names:
                raise RuntimeError("VOXCPM_PROMPT_UNSUPPORTED: installed VoxCPM lacks prompt_text")
            generate_kwargs["prompt_text"] = prompt_text
    # Speed is a declared product parameter.  The installed model may or may not
    # expose a native control; when it does we pass the value through, and the
    # product additionally applies a declared atempo post-process so the audible
    # rate always matches the user's setting.  ``speed_applied_natively``
    # records which path actually happened instead of assuming either one.
    applied_speed_natively = False
    if speed is not None and speed != 1.0:
        parameter_names = inspect.signature(model.generate).parameters
        for candidate in ("speed", "speech_rate", "rate"):
            if candidate in parameter_names:
                generate_kwargs[candidate] = float(speed)
                applied_speed_natively = True
                break
    waveform = model.generate(**generate_kwargs)
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
        "requested_speed": None if speed is None else round(float(speed), 6),
        "speed_applied_natively": applied_speed_natively,
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
        "task",
        choices=("embedding", "voxcpm2", "voxcpm2-batch", "asr", "alignment", "alignment-batch", "ollama"),
    )
    parser.add_argument("--model-root", type=Path, default=DEFAULT_MODEL_ROOT)
    parser.add_argument("--audio-input", type=Path)
    parser.add_argument("--audio-output", type=Path)
    parser.add_argument("--text", action="append")
    parser.add_argument("--text-b64", action="append")
    parser.add_argument("--instruction")
    parser.add_argument("--instruction-b64")
    parser.add_argument("--include-vectors", action="store_true")
    parser.add_argument("--transcript")
    parser.add_argument("--transcript-b64")
    parser.add_argument("--language", default="Chinese")
    parser.add_argument("--language-b64")
    parser.add_argument("--prompt-audio", type=Path)
    parser.add_argument("--prompt-text")
    parser.add_argument("--prompt-text-b64")
    parser.add_argument(
        "--speed",
        type=float,
        default=None,
        help="Declared product speech-rate multiplier (0.5-2.0). Passed to the model when it exposes a native control.",
    )
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--ollama-model", default="qwen3.8:27b")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--batch-manifest", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    texts = list(args.text or [])
    texts.extend(_decode_text(value) for value in (args.text_b64 or []))
    instruction = _decode_text(args.instruction_b64) if args.instruction_b64 else args.instruction
    transcript = _decode_text(args.transcript_b64) if args.transcript_b64 else args.transcript
    language = _decode_text(args.language_b64) if args.language_b64 else args.language
    prompt_text = _decode_text(args.prompt_text_b64) if args.prompt_text_b64 else args.prompt_text

    if args.task == "embedding":
        result = _embedding_smoke(
            args.model_root,
            texts or None,
            instruction,
            include_vectors=args.include_vectors,
        )
    elif args.task == "voxcpm2":
        audio_output = args.audio_output or Path("work/local-model-tests/voxcpm2.wav")
        result = _voxcpm_smoke(
            args.model_root,
            audio_output,
            (texts or ["本地导演台配音测试通过。"])[0],
            prompt_audio=args.prompt_audio,
            prompt_text=prompt_text,
            speed=args.speed,
        )
    elif args.task == "voxcpm2-batch":
        if args.batch_manifest is None or args.output_dir is None:
            parser.error("voxcpm2-batch requires --batch-manifest and --output-dir")
        result = _voxcpm_batch(
            args.model_root,
            args.batch_manifest,
            args.output_dir,
            prompt_audio=args.prompt_audio,
            prompt_text=prompt_text,
            speed=args.speed,
        )
    elif args.task == "alignment-batch":
        if args.batch_manifest is None:
            parser.error("alignment-batch requires --batch-manifest")
        result = _alignment_batch(args.model_root, args.batch_manifest, language)
    elif args.task == "asr":
        if args.audio_input is None or not args.audio_input.is_file():
            parser.error("--audio-input must name an existing audio file")
        result = _asr_smoke(args.model_root, args.audio_input)
    elif args.task == "alignment":
        if (
            args.audio_input is None
            or not args.audio_input.is_file()
            or not transcript
        ):
            parser.error("alignment requires --audio-input and --transcript")
        result = _alignment_smoke(
            args.model_root, args.audio_input, transcript, language
        )
    else:
        result = _ollama_smoke(args.ollama_base_url, args.ollama_model)
    _emit(result, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
