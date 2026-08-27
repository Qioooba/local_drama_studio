import { Component, type ErrorInfo, type ReactNode } from "react";
import { Link, useParams, useRouteError } from "react-router-dom";
import { ApiRequestError } from "../../generated/api";

export type ErrorBoundaryProps = {
  children: ReactNode;
  fallbackTitle?: string;
  fallbackMessage?: string;
  projectId?: string;
  onRetry?: () => void;
  resetKeys?: unknown[];
};

type ErrorBoundaryState = {
  hasError: boolean;
  error: unknown | null;
};

export function extractRequestId(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiRequestError && error.requestId) {
    return error.requestId;
  }
  if (typeof error === "object" && error !== null && "requestId" in error) {
    const value = (error as Record<string, unknown>).requestId;
    if (typeof value === "string" && value.length > 0) return value;
  }
  const message = error instanceof Error ? error.message : String(error);
  const match = /请求 ID\s*([a-zA-Z0-9_-]+)/i.exec(message);
  if (match && match[1]) return match[1];
  return null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: unknown): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo): void {
    console.error("[ErrorBoundary] Caught error:", error, errorInfo);
  }

  componentDidUpdate(prevProps: ErrorBoundaryProps): void {
    if (!this.state.hasError) return;
    if (this.props.resetKeys && prevProps.resetKeys) {
      const changed = this.props.resetKeys.some((key, i) => key !== prevProps.resetKeys?.[i]);
      if (changed) {
        this.reset();
      }
    }
  }

  reset = (): void => {
    this.setState({ hasError: false, error: null });
    this.props.onRetry?.();
  };

  render(): ReactNode {
    if (!this.state.hasError) {
      return this.props.children;
    }

    const { error } = this.state;
    const { fallbackTitle, fallbackMessage, projectId } = this.props;
    const message = fallbackMessage ?? (error instanceof Error ? error.message : String(error));
    const requestId = extractRequestId(error);

    return (
      <div className="product-error-boundary" role="alert" aria-live="assertive">
        <div className="error-boundary-card">
          <div className="panel-heading">
            <div>
              <p className="eyebrow">工作区异常拦截</p>
              <h3>{fallbackTitle ?? "页面区域发生错误"}</h3>
            </div>
            <span className="status-pill status-pill--error">已隔离</span>
          </div>
          <p className="error-boundary-message">{message}</p>
          {requestId && (
            <p className="error-boundary-request-id">
              请求 ID：<code>{requestId}</code>
            </p>
          )}
          <div className="error-boundary-actions">
            <button type="button" className="secondary" onClick={this.reset}>
              重试
            </button>
            {projectId ? (
              <Link className="primary-action v2-inline-link" to={`/projects/${projectId}`}>
                返回项目
              </Link>
            ) : (
              <Link className="primary-action v2-inline-link" to="/projects">
                返回项目列表
              </Link>
            )}
          </div>
        </div>
      </div>
    );
  }
}

export function RouteErrorBoundary({ defaultProjectId, error: explicitError }: { defaultProjectId?: string; error?: unknown }) {
  let routeError: unknown = explicitError;
  try {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    const caught = useRouteError();
    if (caught) routeError = caught;
  } catch {
    // Fallback when rendered outside data router
  }
  const params = useParams();
  const projectId = params.projectId ?? defaultProjectId;
  const message = routeError instanceof Error ? routeError.message : String(routeError ?? "未知路由异常");
  const requestId = extractRequestId(routeError);

  return (
    <div className="product-error-boundary route-error-boundary" role="alert" aria-live="assertive">
      <div className="error-boundary-card">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">工作区路由异常</p>
            <h2>工作区载入受阻</h2>
          </div>
          <span className="status-pill status-pill--error">页面加载错误</span>
        </div>
        <p className="muted">
          当前工作区在渲染时发生未捕获异常。生产状态和不可变版本不受影响，您可以重试当前页面或返回项目。
        </p>
        <p className="error-boundary-message">{message}</p>
        {requestId && (
          <p className="error-boundary-request-id">
            请求 ID：<code>{requestId}</code>
          </p>
        )}
        <div className="error-boundary-actions">
          <button type="button" className="secondary" onClick={() => window.location.reload()}>
            刷新重试
          </button>
          {projectId ? (
            <Link className="primary-action v2-inline-link" to={`/projects/${projectId}`}>
              返回项目
            </Link>
          ) : (
            <Link className="primary-action v2-inline-link" to="/projects">
              返回项目列表
            </Link>
          )}
        </div>
      </div>
    </div>
  );
}
