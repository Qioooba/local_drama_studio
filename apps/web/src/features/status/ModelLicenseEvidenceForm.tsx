import { useState } from "react";
import { importModelLicenseEvidence, type ModelCompatibilitySnapshot } from "../../generated/api";
import { ProjectLocalResourceSelect } from "../shared/ProjectLocalResourceSelect";
import { LOCAL_MODEL_KIND_LABELS } from "../shared/formOptions";

export function ModelLicenseEvidenceForm({ projectId, reports, onImported }: { projectId: string; reports: ModelCompatibilitySnapshot["reports"]; onImported: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [artifactId, setArtifactId] = useState("");
  const [evidencePath, setEvidencePath] = useState("");
  const [licenseName, setLicenseName] = useState("");
  const [licenseStatus, setLicenseStatus] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const submit = async () => {
    if (!artifactId || !evidencePath.trim() || !licenseName.trim() || !licenseStatus) {
      setError("请显式选择模型和授权状态，并填写项目内证据路径与许可证名称。");
      return;
    }
    setPending(true);
    setError(null);
    setSuccess(null);
    try {
      const result = await importModelLicenseEvidence(projectId, {
        model_artifact_id: artifactId,
        evidence_path: evidencePath.trim(),
        license_name: licenseName.trim(),
        license_status: licenseStatus as "LOCAL_LICENSE_VERIFIED" | "USER_OWNED",
      });
      setSuccess(`证据已冻结；离线 hash/量化报告：${result.report.report_status}`);
      onImported();
    } catch (caught) {
      setError(`导入失败：${String(caught)}。未通过路径、SHA 或许可证声明校验的记录不会写入。`);
    } finally {
      setPending(false);
    }
  };

  return <div className="model-license-import">
    <button className="secondary" type="button" aria-expanded={expanded} onClick={() => setExpanded((value) => !value)}>{expanded ? "收起用户授权记录" : "可选：记录用户授权信息"}</button>
    {expanded && <form onSubmit={(event) => { event.preventDefault(); void submit(); }}>
      <div className="field-grid">
        <label>模型文件<select value={artifactId} onChange={(event) => setArtifactId(event.target.value)} required><option value="">请选择模型</option>{reports.map((item) => <option key={item.artifact_id} value={item.artifact_id}>{item.code} · {LOCAL_MODEL_KIND_LABELS[item.kind] ?? "本机模型"}</option>)}</select></label>
        <ProjectLocalResourceSelect projectId={projectId} kind="LICENSE_EVIDENCE" value={evidencePath} onChange={setEvidencePath} label="项目内许可证证据" required emptyLabel="请选择证据文件" />
        <label>许可证名称<input value={licenseName} onChange={(event) => setLicenseName(event.target.value)} placeholder="以真实许可证文件为准" required /></label>
        <label>授权状态<select value={licenseStatus} onChange={(event) => setLicenseStatus(event.target.value)} required><option value="">显式选择</option><option value="LOCAL_LICENSE_VERIFIED">本地许可证已核验</option><option value="USER_OWNED">用户拥有授权</option></select></label>
      </div>
      <p className="muted">此记录完全可选，仅用于用户自己的项目追溯。平台不会据此替用户作法律判断；不填写不会阻止本机模型兼容性验证。提交会本地读取并哈希所选模型，不会联网、上传或加载权重。</p>
      <button className="primary-action" type="submit" disabled={pending}>{pending ? "正在校验完整模型…" : "校验并冻结证据"}</button>
      {error && <p className="inline-error" role="alert">{error}</p>}
      {success && <p className="review-success" role="status">{success}</p>}
    </form>}
  </div>;
}
