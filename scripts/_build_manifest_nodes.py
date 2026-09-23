"""把可信节点清单与节点哈希补进 model_manifest.json。

节点名与插件哈希都来自实际探测：
  * class_mappings = 运行中 ComfyUI /object_info 里的自定义节点类
  * plugins[].plugin_init_sha256 = 对应 custom_nodes 插件 __init__.py 的实际 SHA-256
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from pathlib import Path

MANIFEST = Path(r"F:\AI_Projects\h3\model_manifest.json")
CUSTOM_NODES = Path(r"E:\AI\ComfyDesktop\ComfyUI\ComfyUI\custom_nodes")

# 本项目工作流会用到的自定义插件；哈希取自各自 __init__.py 的真实内容。
PROJECT_PLUGINS = [
    "ComfyUI_RH_MinMaxH3",
    "ComfyUI-GGUF",
    "ComfyUI-KJNodes",
    "ComfyUI-VideoHelperSuite",
    "ComfyUI-Frame-Interpolation",
    "ComfyUI_essentials",
    "ComfyUI-Impact-Pack",
    "rgthree-comfy",
]

BUILTIN_PREFIXES = ("local_drama",)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fetch_object_info() -> dict:
    with urllib.request.urlopen("http://127.0.0.1:8188/object_info", timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def plugin_node_classes(plugin_dir: Path) -> set[str]:
    """节点类名 = 插件的 NODE_CLASS_MAPPINGS 键，直接解析源码。"""

    names: set[str] = set()
    for path in plugin_dir.rglob("*.py"):
        try:
            source = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "NODE_CLASS_MAPPINGS" not in source:
            continue
        # 收集 "Name": 形式的键，这是 ComfyUI 节点的注册写法。
        for line in source.splitlines():
            stripped = line.strip()
            if not stripped.startswith('"') or ":" not in stripped:
                continue
            key = stripped.split('"', 2)
            if len(key) < 3:
                continue
            candidate = key[1]
            if candidate and candidate[0].isupper() and " " not in candidate:
                names.add(candidate)
    return names


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    object_info = fetch_object_info()
    available = set(object_info.keys())

    plugins: list[dict] = []
    trusted: set[str] = set()
    for name in PROJECT_PLUGINS:
        directory = CUSTOM_NODES / name
        if not directory.is_dir():
            continue
        init = directory / "__init__.py"
        if not init.is_file():
            continue
        classes = {cls for cls in plugin_node_classes(directory) if cls in available}
        trusted |= classes
        plugins.append(
            {
                "name": name,
                "plugin_init_sha256": sha256_file(init),
                "node_mapping_sha256": hashlib.sha256(
                    "\n".join(sorted(classes)).encode("utf-8")
                ).hexdigest(),
                "node_class_count": len(classes),
            }
        )

    manifest["nodes"] = {
        "source": "live ComfyUI /object_info plus each plugin's own __init__.py hash",
        "comfyui_version": "0.37.1",
        "class_mappings": sorted(trusted),
        "plugins": plugins,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("plugins:", [(item["name"], item["node_class_count"]) for item in plugins])
    print("trusted classes:", len(trusted))
    sample = [cls for cls in sorted(trusted) if "H3" in cls or "MiniMax" in cls][:12]
    print("H3 sample:", sample)


if __name__ == "__main__":
    main()
