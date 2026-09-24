from __future__ import annotations

import argparse
import base64
import json
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

DEFAULT_MODEL_ROOT = Path(r"F:\AI_Models\LocalDramaStudio")

#: VoxCPM2 diffusion sampling steps.  4 is the project's speed-oriented default;
#: the upstream model default is 10 and the 4/10 comparison is a declared
#: experiment, not a claim that both are equal in quality.
DEFAULT_INFERENCE_TIMESTEPS = 4

#: New-audio length ceiling in generated patches.  One patch is ~0.16 s at the
#: standard VoxCPM2 geometry (patch_size 4, Audio VAE 8x6x5x2x2x2 = 1920 at
#: 48 kHz), so 256 is roughly 41 s of headroom for a 15-25 s narration segment.
DEFAULT_MAX_LEN = 256

#: Classifier-free guidance.  Recorded explicitly instead of relying on a
#: library default that may change between the locked versions.
DEFAULT_CFG_VALUE = 2.0

#: Independent ASR output ceiling.  Official Qwen3-ASR generation config uses a
#: large ceiling and stops at the end-of-sequence token, so 512 removes the
#: truncation risk the previous 128 carried without forcing long output.
DEFAULT_ASR_MAX_NEW_TOKENS = 512

#: Reference-audio parameters the runtime looks for when it decides whether the
#: locked VoxCPM build accepts a fixed reference voice.
_REFERENCE_PARAMETER_CANDIDATES = ("prompt_wav_path", "prompt_text", "reference_wav_path")

#: Speech-rate parameter names, most specific first.
_SPEED_PARAMETER_CANDIDATES = ("speed", "speech_rate", "rate")


def _decode_text(value: str) -> str:
    """Decode the ASCII-safe text transport used by the Windows parent."""
    return base64.b64decode(value.encode("ascii"), validate=True).decode("utf-8")


def _generate_parameters(model: Any) -> tuple[frozenset[str], bool]:
    """Parameter names accepted by ``model.generate``, plus whether it takes ``**kwargs``.

    The upstream wrapper is ``def generate(self, *args, **kwargs)``, which forwards
    to a private ``_generate`` that *does* declare ``prompt_wav_path``/``prompt_text``/
    ``reference_wav_path``/``cfg_value``.  A plain ``inspect.signature(model.generate)``
    therefore sees no explicit names at all, and the previous code read that as
    "the installed VoxCPM lacks prompt_wav_path" and refused every cloned-voice
    narration.  An explicit ``**kwargs`` channel is a declaration that arbitrary
    keyword arguments are forwarded, so it is treated as supporting the
    parameters the runtime knows how to pass.
    """

    import inspect

    try:
        parameters = inspect.signature(model.generate).parameters
    except (TypeError, ValueError):  # pragma: no cover - a non-introspectable callable
        return frozenset(), True
    names = frozenset(parameters)
    accepts_extra_keywords = any(
        parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
    )
    return names, accepts_extra_keywords


def _supports(parameter_names: frozenset[str], accepts_extra_keywords: bool, name: str) -> bool:
    return accepts_extra_keywords or name in parameter_names


def _voxcpm_generate_kwargs(
    *,
    text: str,
    inference_timesteps: int,
    max_len: int,
    cfg_value: float,
    supports: Callable[[str], bool],
    min_len: int = 2,
    retry_badcase: bool = False,
    prompt_audio: Path | None = None,
    prompt_text: str | None = None,
    speed: float | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """The one parameter construction shared by the single and batch VoxCPM paths.

    ``supports`` answers "does the locked model accept this keyword?".  Returns
    ``(kwargs, facts)`` where ``facts`` records what was actually requested, what
    was passed through, and whether the model applied the speech rate natively.
    Keeping one builder means the smoke task and the production batch task cannot
    drift into different ``max_len``/``cfg``/reference behaviour, which is exactly
    how a fix applied to only the batch path would silently stop applying to the
    other.  ``speed_applied_natively`` is *reported*, never assumed: the worker
    needs it to decide whether the audible rate still requires its atempo pass.
    """

    generate_kwargs: dict[str, Any] = {
        "text": text,
        "inference_timesteps": int(inference_timesteps),
        "min_len": int(min_len),
        "max_len": int(max_len),
        "retry_badcase": bool(retry_badcase),
    }
    if supports("cfg_value"):
        generate_kwargs["cfg_value"] = float(cfg_value)
    if prompt_audio is not None:
        # A fixed reference voice is a declared product parameter; the runtime
        # refuses loudly instead of quietly narrating with the model's own voice.
        if not supports("prompt_wav_path"):
            raise RuntimeError("VOXCPM_PROMPT_UNSUPPORTED: installed VoxCPM lacks prompt_wav_path")
        generate_kwargs["prompt_wav_path"] = str(Path(prompt_audio).resolve())
        if prompt_text and supports("prompt_text"):
            generate_kwargs["prompt_text"] = str(prompt_text)
    applied_speed_natively = False
    requested_speed = None if speed is None else round(float(speed), 6)
    if requested_speed is not None and requested_speed != 1.0:
        for candidate in _SPEED_PARAMETER_CANDIDATES:
            if supports(candidate):
                generate_kwargs[candidate] = requested_speed
                applied_speed_natively = True
                break
    facts = {
        "inference_timesteps": int(inference_timesteps),
        "min_len": int(min_len),
        "max_len": int(max_len),
        "cfg_value": float(cfg_value) if supports("cfg_value") else None,
        "max_len_passed": supports("max_len"),
        "reference_passed": prompt_audio is not None,
        "prompt_text_passed": bool(prompt_text) and prompt_audio is not None and supports("prompt_text"),
        "requested_speed": requested_speed,
        "speed_applied_natively": applied_speed_natively,
    }
    return generate_kwargs, facts


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
    inference_timesteps: int = DEFAULT_INFERENCE_TIMESTEPS,
    max_len: int = DEFAULT_MAX_LEN,
    cfg_value: float = DEFAULT_CFG_VALUE,
) -> dict[str, Any]:
    """Synthesise many narration segments in one process.

    Loading VoxCPM2 costs tens of seconds; doing it once per segment made a
    five-minute narration take over an hour.  The batch task keeps the model
    resident and writes one WAV per requested item, reporting per-item status so
    one bad segment cannot discard the rest of the batch.
    """

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
    parameter_names, accepts_extra_keywords = _generate_parameters(model)

    def supports(name: str) -> bool:
        return _supports(parameter_names, accepts_extra_keywords, name)

    results: list[dict[str, Any]] = []
    for item in items:
        item_id = str(item.get("id") or "")
        text = str(item.get("text") or "")
        target = output_dir / f"{item_id}.wav"
        item_started = time.perf_counter()
        try:
            if not text.strip():
                raise RuntimeError("empty narration text")
            generate_kwargs, facts = _voxcpm_generate_kwargs(
                text=text,
                inference_timesteps=inference_timesteps,
                max_len=max_len,
                cfg_value=cfg_value,
                supports=supports,
                prompt_audio=prompt_audio,
                prompt_text=prompt_text,
                speed=speed,
            )
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
                    "requested_speed": facts["requested_speed"],
                    "speed_applied_natively": facts["speed_applied_natively"],
                    "parameters": facts,
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
        "parameters": {
            "inference_timesteps": int(inference_timesteps),
            "min_len": 2,
            "max_len": int(max_len),
            "cfg_value": float(cfg_value),
            "retry_badcase": False,
        },
        "network_used": False,
    }


def _forced_aligner_sample_rate(processor: Any) -> int:
    """The aligner's own input sampling rate, read from its processor config.

    ``decode_forced_alignment`` reports seconds; the pipeline compares aligner
    chunks with narration takes that are stored at 48 kHz, so the sample numbers
    must be derived at the rate the aligner actually declares instead of a
    hardcoded constant that a future checkpoint could silently falsify.
    """

    extractor = getattr(processor, "feature_extractor", None)
    rate = getattr(extractor, "sampling_rate", None)
    try:
        rate_value = int(rate)
    except (TypeError, ValueError):
        return 16000
    return rate_value if rate_value > 0 else 16000


def _alignment_timestamps(
    processor: Any,
    *,
    logits: Any,
    input_ids: Any,
    word_lists: Any,
    timestamp_token_id: int,
    sample_rate_hz: int,
) -> list[dict[str, Any]]:
    """One normalised timestamp list shared by the single and batch alignment tasks.

    Every entry carries the same fact in the two units the pipeline reads:
    ``token`` + ``start_sample``/``end_sample`` (the canonical word-timing
    vocabulary of ``narration_alignment_revisions``) and ``start_ms``/``end_ms``
    for review.  Emitting only ``start_time``/``end_time`` seconds made the
    batch aligner's chunks unreadable to ``_match_chunks_to_tokens``, which
    looks for ``token``/``start_sample``: every token then came back unaligned
    while the stage still reported success.
    """

    raw = processor.decode_forced_alignment(
        logits=logits,
        input_ids=input_ids,
        word_lists=word_lists,
        timestamp_token_id=timestamp_token_id,
    )[0]
    normalized: list[dict[str, Any]] = []
    for entry in raw:
        text = str(entry.get("text") or "").strip()
        start = entry.get("start_time")
        end = entry.get("end_time")
        if not text or start is None or end is None:
            continue
        start_seconds = float(start)
        end_seconds = float(end)
        if end_seconds < start_seconds:
            raise RuntimeError("Qwen3 ForcedAligner returned a timestamp ending before it starts")
        normalized.append(
            {
                "token": text,
                "start_ms": round(start_seconds * 1000),
                "end_ms": round(end_seconds * 1000),
                "start_sample": round(start_seconds * sample_rate_hz),
                "end_sample": round(end_seconds * sample_rate_hz),
                "sample_rate_hz": int(sample_rate_hz),
                "start_time": round(start_seconds, 3),
                "end_time": round(end_seconds, 3),
            }
        )
    if not normalized:
        raise RuntimeError("Qwen3 ForcedAligner returned no usable timestamps")
    return normalized


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
    sample_rate_hz = _forced_aligner_sample_rate(processor)
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
            normalized = _alignment_timestamps(
                processor,
                logits=outputs.logits,
                input_ids=inputs["input_ids"],
                word_lists=word_lists,
                timestamp_token_id=model.config.timestamp_token_id,
                sample_rate_hz=sample_rate_hz,
            )
            results.append(
                {
                    "id": item_id,
                    "status": "PASS",
                    "timestamps": normalized,
                    "sample_rate_hz": sample_rate_hz,
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
        "sample_rate_hz": sample_rate_hz,
        "timestamp_segment_ms": int(getattr(model.config, "timestamp_segment_time", 0) or 0),
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
    inference_timesteps: int = DEFAULT_INFERENCE_TIMESTEPS,
    max_len: int = DEFAULT_MAX_LEN,
    cfg_value: float = DEFAULT_CFG_VALUE,
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
    parameter_names, accepts_extra_keywords = _generate_parameters(model)

    def supports(name: str) -> bool:
        return _supports(parameter_names, accepts_extra_keywords, name)

    # Speed is a declared product parameter.  The installed model may or may not
    # expose a native control; when it does the value is passed through, and when
    # it does not the caller keeps its declared atempo post-process so the audible
    # rate still matches the user's setting.  ``speed_applied_natively`` reports
    # which path actually happened instead of assuming either one.
    generate_kwargs, facts = _voxcpm_generate_kwargs(
        text=text,
        inference_timesteps=inference_timesteps,
        max_len=max_len,
        cfg_value=cfg_value,
        supports=supports,
        prompt_audio=prompt_audio,
        prompt_text=prompt_text,
        speed=speed,
    )
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
        "speed_applied_natively": facts["speed_applied_natively"],
        "parameters": facts,
        "network_used": False,
    }


def _asr_smoke(model_root: Path, audio_input: Path, *, max_new_tokens: int = DEFAULT_ASR_MAX_NEW_TOKENS) -> dict[str, Any]:
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
        # 512 is an output *ceiling*, not a fixed length: a transcription that
        # reaches its end-of-sequence token stops there.  The previous 128 was
        # short enough to cut a long narration segment mid-sentence, and a
        # truncated transcript would have been compared against the script as if
        # the missing words had never been spoken.
        output_ids = model.generate(
            **inputs, max_new_tokens=int(max_new_tokens), do_sample=False
        )
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
        "max_new_tokens": int(max_new_tokens),
        "do_sample": False,
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
    sample_rate_hz = _forced_aligner_sample_rate(processor)
    normalized = _alignment_timestamps(
        processor,
        logits=outputs.logits,
        input_ids=inputs["input_ids"],
        word_lists=word_lists,
        timestamp_token_id=model.config.timestamp_token_id,
        sample_rate_hz=sample_rate_hz,
    )
    return {
        "task": "alignment",
        "status": "PASS",
        "model": str(model_path),
        "audio": str(audio_input.resolve()),
        "transcript": transcript,
        "language": language,
        "timestamps": normalized,
        "sample_rate_hz": sample_rate_hz,
        "timestamp_segment_ms": int(getattr(model.config, "timestamp_segment_time", 0) or 0),
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
    # The narration parameters are declared machine settings, so the two
    # experiments the plan describes (4 vs 10 diffusion steps, and a longer
    # max_len) are one settings change instead of a source edit.
    parser.add_argument(
        "--inference-timesteps",
        type=int,
        default=DEFAULT_INFERENCE_TIMESTEPS,
        help="VoxCPM2 diffusion sampling steps (project default 4; upstream default 10).",
    )
    parser.add_argument(
        "--max-len",
        type=int,
        default=DEFAULT_MAX_LEN,
        help="VoxCPM2 generated-patch ceiling; one patch is ~0.16 s (256 is roughly 41 s).",
    )
    parser.add_argument(
        "--cfg-value",
        type=float,
        default=DEFAULT_CFG_VALUE,
        help="VoxCPM2 classifier-free guidance value.",
    )
    parser.add_argument(
        "--asr-max-new-tokens",
        type=int,
        default=DEFAULT_ASR_MAX_NEW_TOKENS,
        help="Independent ASR output ceiling; generation still stops at end of sequence.",
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
    narration_parameters = {
        "inference_timesteps": args.inference_timesteps,
        "max_len": args.max_len,
        "cfg_value": args.cfg_value,
    }

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
            **narration_parameters,
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
            **narration_parameters,
        )
    elif args.task == "alignment-batch":
        if args.batch_manifest is None:
            parser.error("alignment-batch requires --batch-manifest")
        result = _alignment_batch(args.model_root, args.batch_manifest, language)
    elif args.task == "asr":
        if args.audio_input is None or not args.audio_input.is_file():
            parser.error("--audio-input must name an existing audio file")
        result = _asr_smoke(
            args.model_root, args.audio_input, max_new_tokens=args.asr_max_new_tokens
        )
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
