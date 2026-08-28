import { requestJson, type Project } from "../../generated/api";

export type ProjectDetail = Project & {
  absolute_root_path: string;
};

export function getProject(projectId: string, baseUrl = ""): Promise<{ project: ProjectDetail }> {
  return requestJson(`/api/v1/projects/${encodeURIComponent(projectId)}`, undefined, baseUrl);
}
