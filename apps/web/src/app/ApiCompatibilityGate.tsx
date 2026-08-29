import { useEffect, useState, type ReactNode } from "react";
import { API_CONTRACT_VERSION, ApiContractError, systemContract } from "../generated/api";

type ContractState =
  | { status: "checking" }
  | { status: "ready" }
  | { status: "error"; error: unknown };

export function ApiCompatibilityGate({ children }: { children: ReactNode }) {
  const [attempt, setAttempt] = useState(0);
  const [contract, setContract] = useState<ContractState>({ status: "checking" });

  useEffect(() => {
    let active = true;
    setContract({ status: "checking" });
    void (async () => {
      try {
        const observed = await systemContract();
        if (observed.api_contract_version !== API_CONTRACT_VERSION) {
          throw new ApiContractError(
            `界面需要 API 契约 ${API_CONTRACT_VERSION}，当前服务为 ${observed.api_contract_version || "未声明"}。`,
            "API_CONTRACT_VERSION_MISMATCH",
            "/api/v1/system/contract",
            API_CONTRACT_VERSION,
            observed.api_contract_version || null,
          );
        }
        if (active) setContract({ status: "ready" });
      } catch (error: unknown) {
        if (active) setContract({ status: "error", error });
      }
    })();
    return () => {
      active = false;
    };
  }, [attempt]);

  if (contract.status === "checking") {
    return <main className="route-loading" role="status">正在核对界面与本地服务版本…</main>;
  }

  if (contract.status === "error") {
    const error = contract.error;
    const isContractError = error instanceof ApiContractError;
    return (
      <main className="product-error-boundary route-error-boundary" role="alert" aria-live="assertive">
        <div className="error-boundary-card">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">启动契约校验</p>
              <h2>{isContractError ? "界面与本地服务版本不一致" : "无法核对本地服务版本"}</h2>
            </div>
            <span className="status-pill status-pill--error">已阻止载入</span>
          </div>
          <p className="muted">
            系统已在读取工作区数据前停止，项目文件、任务和不可变版本均未修改。
          </p>
          <p className="error-boundary-message">
            {error instanceof Error ? error.message : String(error)}
          </p>
          <div className="error-boundary-actions">
            <button type="button" className="secondary" onClick={() => setAttempt((value) => value + 1)}>
              重新检测服务
            </button>
            <button type="button" className="primary-action" onClick={() => window.location.reload()}>
              载入最新界面
            </button>
          </div>
        </div>
      </main>
    );
  }

  return children;
}
