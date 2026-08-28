import { ProfileOverrideFields } from "../model-config/ProfileOverrideFields";

type EditorProps = {
  capability: string;
  overrideSchema?: Record<string, unknown>;
  inputJson: string;
  parameterJson: string;
  outputJson: string;
  resourceJson: string;
  onInputChange: (value: string) => void;
  onParameterChange: (value: string) => void;
  onOutputChange: (value: string) => void;
  onResourceChange: (value: string) => void;
};

function parseObject(value: string): { data: Record<string, unknown>; error: string | null } {
  try {
    const parsed: unknown = JSON.parse(value);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") return { data: {}, error: "必须是 JSON 对象" };
    return { data: parsed as Record<string, unknown>, error: null };
  } catch {
    return { data: {}, error: "JSON 语法无效" };
  }
}

function write(data: Record<string, unknown>, onChange: (value: string) => void) {
  onChange(JSON.stringify(data, null, 2));
}

function patchNested(source: Record<string, unknown>, key: string, patch: Record<string, unknown>) {
  const current = source[key];
  return { ...source, [key]: { ...(current && typeof current === "object" && !Array.isArray(current) ? current as Record<string, unknown> : {}), ...patch } };
}

export function ProfileContractEditors(props: EditorProps) {
  const input = parseObject(props.inputJson);
  const parameters = parseObject(props.parameterJson);
  const output = parseObject(props.outputJson);
  const resources = parseObject(props.resourceJson);
  const errors = [input.error, parameters.error, output.error, resources.error].filter(Boolean);
  const seed = parameters.data.seed && typeof parameters.data.seed === "object" ? parameters.data.seed as Record<string, unknown> : {};
  const defaults = parameters.data.defaults && typeof parameters.data.defaults === "object" && !Array.isArray(parameters.data.defaults)
    ? parameters.data.defaults as Record<string, unknown>
    : {};
  const defaultMediaKind = props.capability.includes("TTS") || props.capability.includes("AUDIO") ? "AUDIO" : props.capability.startsWith("IMAGE_") ? "IMAGE" : "VIDEO";

  const changeDefaults = (nextDefaults: Record<string, unknown>) => {
    const nextParameters = { ...parameters.data };
    if (Object.keys(nextDefaults).length) nextParameters.defaults = nextDefaults;
    else delete nextParameters.defaults;
    write(nextParameters, props.onParameterChange);
  };

  const applyTemplate = () => {
    write({ ...input.data, transport: "LOOPBACK_HTTP", input_slots: input.data.input_slots ?? {} }, props.onInputChange);
    const declaredCapabilities = parameters.data.capabilities && typeof parameters.data.capabilities === "object" && !Array.isArray(parameters.data.capabilities)
      ? parameters.data.capabilities as Record<string, unknown>
      : {};
    const unsupported = { support: "UNSUPPORTED", required_inputs: [] };
    write({
      ...patchNested(parameters.data, "seed", { determinism: "EXPLICIT", required: true }),
      capabilities: {
        ...declaredCapabilities,
        ...(props.capability.startsWith("VIDEO_") ? {
          camera: declaredCapabilities.camera ?? {
            support: "PROMPT_FALLBACK",
            prompt_fallback: true,
            required_inputs: [],
          },
        } : {}),
        extend: declaredCapabilities.extend ?? unsupported,
        V2V: declaredCapabilities.V2V ?? unsupported,
        reference: declaredCapabilities.reference ?? unsupported,
        motion: declaredCapabilities.motion ?? unsupported,
      },
    }, props.onParameterChange);
    write({ ...output.data, media_kind: output.data.media_kind ?? defaultMediaKind }, props.onOutputChange);
    write({ ...resources.data, gpu_heavy_concurrency: 1, worker_policy: resources.data.worker_policy ?? "ONE_H3_WORKER_ONE_GPU_TASK" }, props.onResourceChange);
  };

  return <div className="profile-contract-structured">
    <section className="profile-contract-section" aria-labelledby="profile-runtime-boundary-title">
      <div className="profile-contract-section__heading">
        <div><p className="eyebrow">01 · 执行边界</p><h4 id="profile-runtime-boundary-title">任务从哪里执行、产出什么</h4></div>
        <span className="status-pill neutral">任务提交前生效</span>
      </div>
      <p>这里定义 Studio 如何调用执行器以及期望得到的媒体类型。它不是 ComfyUI 的启动设置，也不会修改当前浏览器。</p>
      <div className="profile-contract-fields">
        <label>服务端执行方式<select value={String(input.data.transport ?? "")} onChange={(event) => write({ ...input.data, transport: event.target.value }, props.onInputChange)}><option value="">请选择执行方式</option><option value="LOOPBACK_HTTP">本机 HTTP 服务（如 ComfyUI / Ollama）</option><option value="LOCAL_PROCESS">由 Studio 启动本机进程</option><option value="LOCAL_CLI">通过命令行输入输出调用</option></select><small>每次任务开始时使用。仅允许本机边界；具体地址、进程或命令来自已绑定运行时。</small></label>
        <label>输出媒体类型<select value={String(output.data.media_kind ?? "")} onChange={(event) => write({ ...output.data, media_kind: event.target.value }, props.onOutputChange)}><option value="">请选择输出类型</option><option value="IMAGE">图片</option><option value="VIDEO">视频</option><option value="AUDIO">音频</option><option value="DOCUMENT">结构化文档</option></select><small>任务完成并登记产物时校验；应与该能力的实际输出一致。</small></label>
      </div>
    </section>

    <section className="profile-contract-section" aria-labelledby="profile-scheduling-title">
      <div className="profile-contract-section__heading">
        <div><p className="eyebrow">02 · 复现与调度</p><h4 id="profile-scheduling-title">结果能否复现、机器如何排队</h4></div>
        <span className="status-pill neutral">任务计划阶段生效</span>
      </div>
      <p>这些设置会进入任务执行快照，用于重放、审计和本机资源互斥。</p>
      <div className="profile-contract-fields">
        <label>随机种子策略<select value={String(seed.determinism ?? "")} onChange={(event) => write(patchNested(parameters.data, "seed", { determinism: event.target.value }), props.onParameterChange)}><option value="">请选择种子策略</option><option value="EXPLICIT">必须指定种子，允许严格重放</option><option value="BEST_EFFORT">记录种子，但仅尽量复现</option><option value="NONDETERMINISTIC">允许每次产生不同结果</option><option value="profile_declared">沿用模型能力自身声明</option></select><span className="checkbox-label"><input type="checkbox" checked={Boolean(seed.required)} onChange={(event) => write(patchNested(parameters.data, "seed", { required: event.target.checked }), props.onParameterChange)} />创建任务时必须填写随机种子</span><small>用于创建任务和故障重放；不会改变已经冻结的历史任务。</small></label>
        <label>任务执行策略<select value={String(resources.data.worker_policy ?? "ONE_H3_WORKER_ONE_GPU_TASK")} onChange={(event) => write({ ...resources.data, worker_policy: event.target.value, gpu_heavy_concurrency: 1 }, props.onResourceChange)}><option value="ONE_H3_WORKER_ONE_GPU_TASK">每个执行器同时处理 1 个显卡任务</option><option value="CPU_ONLY_SERIAL">只使用处理器并逐个执行</option></select><small>用于后台处理服务领取任务时；显卡重任务始终保持单并发安全门禁。</small></label>
        <div className="field-fact"><span>显卡重任务并发</span><strong>固定为 1</strong><small>这是系统安全门禁，不是可调的质量参数。可避免多个大模型任务同时挤占显存。</small></div>
      </div>
    </section>

    <section className="profile-contract-section" id="profile-generation-defaults" aria-labelledby="profile-generation-defaults-title">
      <div className="profile-contract-section__heading">
        <div><p className="eyebrow">03 · 生成默认参数</p><h4 id="profile-generation-defaults-title">项目没有覆盖时采用的起始值</h4></div>
        <span className="status-pill neutral">生成设置阶段生效</span>
      </div>
      <p>这里设置该能力版本的全局默认值。项目、镜头或单次生成可以在模型允许的范围内覆盖；已创建任务仍使用自己的冻结快照。</p>
      <ProfileOverrideFields schema={props.overrideSchema} value={defaults} scope="RUN" context="profile-default" onChange={changeDefaults} />
    </section>

    <details className="profile-contract-advanced">
      <summary><span>高级技术契约</span><small>输入槽、能力边界、资源策略与原始 JSON</small></summary>
      <div className="profile-contract-advanced__body">
        <p className="muted">这些字段用于适配器开发、兼容性验证和审计。普通参数调整不需要修改原始 JSON。</p>
        <div className="profile-contract-template-action">
          <div><strong>恢复安全契约基线</strong><small>补齐本机执行、种子、输出和显卡互斥的安全字段；不会发布版本。</small></div>
          <button type="button" className="secondary" onClick={applyTemplate}>应用安全基线</button>
        </div>
        {errors.length === 0 ? <p className="ok-text" role="status">输入、参数、输出和资源 4 组契约结构有效。</p> : <p className="inline-error" role="alert">{errors.join("；")}</p>}
        <details className="profile-contract-json">
          <summary>查看系统保存的完整 JSON</summary>
          <div className="profile-contract-fields">
            <div><strong>输入契约</strong><pre>{props.inputJson}</pre></div>
            <div><strong>参数约束</strong><pre>{props.parameterJson}</pre></div>
            <div><strong>输出契约</strong><pre>{props.outputJson}</pre></div>
            <div><strong>资源策略</strong><pre>{props.resourceJson}</pre></div>
          </div>
        </details>
      </div>
    </details>
  </div>;
}
