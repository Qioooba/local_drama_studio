type EditorProps = {
  capability: string;
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
  const defaultMediaKind = props.capability.includes("TTS") || props.capability.includes("AUDIO") ? "AUDIO" : props.capability.startsWith("IMAGE_") ? "IMAGE" : "VIDEO";

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
    <div className="profile-contract-fields">
      <label>服务端执行方式<select value={String(input.data.transport ?? "")} onChange={(event) => write({ ...input.data, transport: event.target.value }, props.onInputChange)}><option value="">请选择</option><option value="LOOPBACK_HTTP">Windows 服务端 HTTP 接口</option><option value="LOCAL_PROCESS">Windows 服务端进程</option><option value="LOCAL_CLI">Windows 服务端命令行程序</option></select><small>执行器由 Studio 服务端调用，与当前浏览器电脑无关。</small></label>
      <label>随机种子可复现性<select value={String(seed.determinism ?? "")} onChange={(event) => write(patchNested(parameters.data, "seed", { determinism: event.target.value }), props.onParameterChange)}><option value="">请选择</option><option value="EXPLICIT">指定随机种子，可严格复现</option><option value="BEST_EFFORT">尽量复现，但不保证完全一致</option><option value="NONDETERMINISTIC">每次结果可能不同</option><option value="profile_declared">由模型配置声明</option></select><span className="checkbox-label"><input type="checkbox" checked={Boolean(seed.required)} onChange={(event) => write(patchNested(parameters.data, "seed", { required: event.target.checked }), props.onParameterChange)} />随机种子必填</span></label>
      <label>输出媒体类型<select value={String(output.data.media_kind ?? "")} onChange={(event) => write({ ...output.data, media_kind: event.target.value }, props.onOutputChange)}><option value="">请选择</option><option value="IMAGE">图片</option><option value="VIDEO">视频</option><option value="AUDIO">音频</option><option value="DOCUMENT">文档</option></select><small>容器和编码等扩展字段可在高级区维护。</small></label>
      <div className="field-fact"><span>显卡任务并发</span><strong>每次 1 个</strong><small>由服务端安全门禁固定，无需填写。</small></div>
      <label>任务执行策略<select value={String(resources.data.worker_policy ?? "ONE_H3_WORKER_ONE_GPU_TASK")} onChange={(event) => write({ ...resources.data, worker_policy: event.target.value, gpu_heavy_concurrency: 1 }, props.onResourceChange)}><option value="ONE_H3_WORKER_ONE_GPU_TASK">每个执行器同时运行一个显卡任务</option><option value="CPU_ONLY_SERIAL">仅用处理器逐个执行</option></select></label>
    </div>
    <div className="profile-editor-actions"><button type="button" className="secondary" onClick={applyTemplate}>应用安全本地契约模板</button>{errors.length === 0 ? <span className="ok-text" role="status">4 组 JSON 结构有效</span> : <span className="inline-error" role="alert">{errors.join("；")}</span>}</div>
    <details>
      <summary>查看系统保存的完整契约</summary>
      <p className="muted">扩展字段由系统和能力模板维护；这里仅供核对，避免要求用户手写 JSON。</p>
      <div className="profile-contract-fields">
        <div><strong>输入契约</strong><pre>{props.inputJson}</pre></div>
        <div><strong>参数约束</strong><pre>{props.parameterJson}</pre></div>
        <div><strong>输出契约</strong><pre>{props.outputJson}</pre></div>
        <div><strong>资源策略</strong><pre>{props.resourceJson}</pre></div>
      </div>
    </details>
  </div>;
}
