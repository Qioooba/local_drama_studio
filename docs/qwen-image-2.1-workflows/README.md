# Qwen-Image-2.1 工作流（UI / API JSON）

本目录由 `scripts/qwen21/export_workflow_json.py` 从**已发布**的不可变工作流版本导出，
不得手工编辑；`ui/` 与 `api/` 是同一条冻结图的两个表示。

| definition | capability | 节点/连线 |
|---|---|---|
| `QWEN_IMAGE_21_EDIT` | `IMAGE_EDIT` | 9 / 11 |
| `QWEN_IMAGE_21_EDIT_2REF` | `IMAGE_EDIT` | 10 / 12 |
| `QWEN_IMAGE_21_T2I_CHARACTER` | `IMAGE_CHARACTER` | 8 / 8 |
| `QWEN_IMAGE_21_T2I_CONCEPT` | `IMAGE_CONCEPT` | 8 / 8 |
| `QWEN_IMAGE_21_T2I_SCENE` | `IMAGE_SCENE` | 8 / 8 |

## 语义绑定

### `QWEN_IMAGE_21_EDIT`

| 语义角色 | 节点 | 输入 |
|---|---|---|
| `CFG` | `6` | `cfg` |
| `DENOISE` | `6` | `denoise` |
| `NEGATIVE_PROMPT` | `4` | `negative_prompt` |
| `OUTPUT_PREFIX` | `8` | `filename_prefix` |
| `PROMPT` | `4` | `prompt` |
| `REFERENCE_IMAGE_1` | `9` | `image` |
| `RESOLUTION` | `4` | `resolution` |
| `SAMPLER` | `6` | `sampler_name` |
| `SCHEDULER` | `6` | `scheduler` |
| `SEED` | `6` | `seed` |
| `STEPS` | `6` | `steps` |

输入槽：

- `CFG` — required=False
- `NEGATIVE_PROMPT` — required=False
- `OUTPUT_PREFIX` — required=False
- `PROMPT` — required=True
- `REFERENCE_IMAGE_1` — required=True
- `RESOLUTION` — required=False
- `SEED` — required=True
- `STEPS` — required=False

### `QWEN_IMAGE_21_EDIT_2REF`

| 语义角色 | 节点 | 输入 |
|---|---|---|
| `CFG` | `6` | `cfg` |
| `DENOISE` | `6` | `denoise` |
| `NEGATIVE_PROMPT` | `4` | `negative_prompt` |
| `OUTPUT_PREFIX` | `8` | `filename_prefix` |
| `PROMPT` | `4` | `prompt` |
| `REFERENCE_IMAGE_1` | `9` | `image` |
| `REFERENCE_IMAGE_2` | `10` | `image` |
| `RESOLUTION` | `4` | `resolution` |
| `SAMPLER` | `6` | `sampler_name` |
| `SCHEDULER` | `6` | `scheduler` |
| `SEED` | `6` | `seed` |
| `STEPS` | `6` | `steps` |

输入槽：

- `CFG` — required=False
- `NEGATIVE_PROMPT` — required=False
- `OUTPUT_PREFIX` — required=False
- `PROMPT` — required=True
- `REFERENCE_IMAGE_1` — required=True
- `REFERENCE_IMAGE_2` — required=True
- `RESOLUTION` — required=False
- `SEED` — required=True
- `STEPS` — required=False

### `QWEN_IMAGE_21_T2I_CHARACTER`

| 语义角色 | 节点 | 输入 |
|---|---|---|
| `CFG` | `6` | `cfg` |
| `DENOISE` | `6` | `denoise` |
| `HEIGHT` | `5` | `height` |
| `NEGATIVE_PROMPT` | `4` | `negative_prompt` |
| `OUTPUT_PREFIX` | `8` | `filename_prefix` |
| `PROMPT` | `4` | `prompt` |
| `RESOLUTION` | `4` | `resolution` |
| `SAMPLER` | `6` | `sampler_name` |
| `SCHEDULER` | `6` | `scheduler` |
| `SEED` | `6` | `seed` |
| `STEPS` | `6` | `steps` |
| `WIDTH` | `5` | `width` |

输入槽：

- `CFG` — required=False
- `HEIGHT` — required=False
- `NEGATIVE_PROMPT` — required=False
- `OUTPUT_PREFIX` — required=False
- `PROMPT` — required=True
- `SEED` — required=True
- `STEPS` — required=False
- `WIDTH` — required=False

### `QWEN_IMAGE_21_T2I_CONCEPT`

| 语义角色 | 节点 | 输入 |
|---|---|---|
| `CFG` | `6` | `cfg` |
| `DENOISE` | `6` | `denoise` |
| `HEIGHT` | `5` | `height` |
| `NEGATIVE_PROMPT` | `4` | `negative_prompt` |
| `OUTPUT_PREFIX` | `8` | `filename_prefix` |
| `PROMPT` | `4` | `prompt` |
| `RESOLUTION` | `4` | `resolution` |
| `SAMPLER` | `6` | `sampler_name` |
| `SCHEDULER` | `6` | `scheduler` |
| `SEED` | `6` | `seed` |
| `STEPS` | `6` | `steps` |
| `WIDTH` | `5` | `width` |

输入槽：

- `CFG` — required=False
- `HEIGHT` — required=False
- `NEGATIVE_PROMPT` — required=False
- `OUTPUT_PREFIX` — required=False
- `PROMPT` — required=True
- `SEED` — required=True
- `STEPS` — required=False
- `WIDTH` — required=False

### `QWEN_IMAGE_21_T2I_SCENE`

| 语义角色 | 节点 | 输入 |
|---|---|---|
| `CFG` | `6` | `cfg` |
| `DENOISE` | `6` | `denoise` |
| `HEIGHT` | `5` | `height` |
| `NEGATIVE_PROMPT` | `4` | `negative_prompt` |
| `OUTPUT_PREFIX` | `8` | `filename_prefix` |
| `PROMPT` | `4` | `prompt` |
| `RESOLUTION` | `4` | `resolution` |
| `SAMPLER` | `6` | `sampler_name` |
| `SCHEDULER` | `6` | `scheduler` |
| `SEED` | `6` | `seed` |
| `STEPS` | `6` | `steps` |
| `WIDTH` | `5` | `width` |

输入槽：

- `CFG` — required=False
- `HEIGHT` — required=False
- `NEGATIVE_PROMPT` — required=False
- `OUTPUT_PREFIX` — required=False
- `PROMPT` — required=True
- `SEED` — required=True
- `STEPS` — required=False
- `WIDTH` — required=False

