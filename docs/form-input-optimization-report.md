# 表单输入控件优化分析报告

## 一、背景

这是一个给用户进行AI视频创作的本地工具平台。当前UI中存在大量让用户手动输入的文本框，但实际很多字段是可枚举的有限集合，应该改为下拉框或单选框，以提升用户体验并减少输入错误。

## 二、问题字段清单

### P0 - 高优先级（严重影响用户体验）

#### 1. 角色必需参考类型
- **文件**: `apps/web/src/features/recipes-v2/DirectorRecipeManager.tsx`
- **行号**: 81
- **当前控件**: `<input>` 文本框，用户输入逗号分隔的枚举值
- **问题**: 用户容易拼错、输入无效值
- **建议改为**: 标签式多选组件（Tag/Chip selector）
- **建议选项**:
  - HERO（主角参考）
  - FRONT（正面参考）
  - LEFT（左侧参考）
  - RIGHT（右侧参考）
  - BACK（背面参考）
  - TOP（顶部参考）
  - BOTTOM（底部参考）

#### 2. 视频分辨率（宽×高）
- **文件**: `apps/web/src/features/generation/PostProcessPanel.tsx`
- **行号**: 25-26
- **当前控件**: 两个 `<input type="number">` 分别输入宽和高
- **问题**: 用户需要手动输入数字，容易超出有效范围（64-8192）或输入无效值
- **建议改为**: 预设分辨率下拉框（宽高联动选择）
- **建议选项**:
  - 1920×1080（全高清）
  - 1280×720（高清）
  - 1080×1920（竖屏9:16）
  - 720×1280（竖屏）
  - 1080×1350（4:5社交媒体）
  - 1080×1080（1:1方形）
  - 3840×2160（4K）
  - 2560×1440（2K）
  - 自定义（手动输入模式）

### P1 - 中等优先级（可优化体验）

#### 3. BrandKit 颜色和排版配置
- **文件**: `apps/web/src/features/shared/BrandKitPanel.tsx`
- **行号**: 7
- **当前控件**: `<textarea>` 输入JSON字符串
- **问题**: 对于非技术用户非常不友好，容易产生格式错误
- **建议改为**: 结构化表单字段
  | 字段 | 建议控件 |
  |-----|---------|
  | 主色调 | 颜色选择器 + hex输入 |
  | 字体系列 | 下拉框（Inter, system-ui, Roboto...）|
  | 间距单位 | number input 或 preset下拉框 |
  | 次要色 | 颜色选择器 + hex输入 |

#### 4. CRF 值（视频编码质量参数）
- **文件**: `apps/web/src/features/generation/PostProcessPanel.tsx`
- **行号**: 27
- **当前控件**: `<input type="number" min="0" max="51">`
- **问题**: CRF是有固定语义的值，不是任意数字
- **建议改为**: 预设CRF下拉框
- **建议选项**:
  - 18（高质量，文件较大）
  - 20（良好平衡）
  - 22（推荐默认值）
  - 23（标准质量）
  - 28（低带宽/预览）

#### 5. Workflow代码标识符
- **文件**: `apps/web/src/features/shared/AutomationWorkflowPanel.tsx`
- **行号**: 21-22, 172
- **当前控件**: `<input>` 自由文本
- **问题**: 用户自由输入代码，容易产生无效标识符（如空格、中文、特殊字符）
- **建议改为**: 
  - 方案A: 自动生成（UUID或时间戳前缀）
  - 方案B: 预定义模板选择（只有几个固定模式）

## 三、已正确实现的控件（无需修改）

以下字段已经正确使用了select/radio/checkbox：

| 模块 | 字段 | 控件类型 |
|-----|------|---------|
| GenerationControlPanel | 生产档位(tier) | select |
| PostProcessPanel | H264 preset | select |
| DirectorIntentEditor | 景别/构图/运镜 | ChoiceGrid(radio button组) |
| EpisodeRunPanel | 生产质量模式 | radio button |
| EpisodeRunPanel | 人工确认节点 | select |
| QcPolicyManager | QC阶段/类别 | tabs + select |
| DirectorRecipeManager | 画幅 | select |
| DirectorRecipeManager | 对白覆盖 | select |
| DirectorRecipeManager | 图像/视频能力 | select |
| GenerateMultiViewPanel | 一致性强度 | select |
| GenerateMultiViewPanel | 背景选项 | select |
| LocalLLMConfigurationPanel | Provider协议 | select |
| LocalLLMConfigurationPanel | 目标能力 | select |
| AutomationWorkflowPanel | 模板选择 | select |
| ShotGroupPlanner | 类型/场景 | select |
| BrandKitPanel | 水印位置 | select |

## 四、设计建议

### 4.1 下拉框设计原则
1. **选项数量控制**: 7±2个选项以内最佳，超过可考虑分组（如按用途/场景分组）
2. **默认值**: 提供合理的默认值，减少用户决策负担
3. **可搜索下拉框**: 当选项超过15个时，建议添加搜索过滤功能
4. **联动选择**: 相关字段（如分辨率与画幅）应联动更新选项

### 4.2 标签式多选组件设计原则
1. **可视化**: 让用户清楚看到已选中的标签
2. **快捷操作**: 提供"全选"、"清空"快捷按钮
3. **数量限制**: 超出数量限制时禁用未选标签
4. **实时反馈**: 选中/取消时立即更新显示

### 4.3 自定义输入的保留
- 在改为下拉框的场景中，保留"自定义"选项，用户仍可手动输入
- 自定义输入应添加格式验证和错误提示

## 五、预期收益

1. **减少输入错误**: 枚举值不再依赖用户记忆和输入
2. **提升效率**: 点击选择比打字更快
3. **降低门槛**: 非技术用户也能正确操作
4. **数据一致性**: 后端接收到的值更规范
5. **更好的可访问性**: 屏幕阅读器能更好识别选项
