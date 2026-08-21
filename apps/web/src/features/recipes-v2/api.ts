import type { DirectorRecipe, DirectorRecipeBinding, DirectorRecipeDocument, DirectorRecipeVersion, QcPolicyVersionOption } from "./types";

export class DirectorRecipeApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details: Record<string, unknown> = {}) { super(message); this.name = "DirectorRecipeApiError"; }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api/v1${path}`, init);
  const body = await response.json().catch(() => null) as { error?: { code?: string; message?: string; details?: Record<string, unknown> } } | null;
  if (!response.ok) throw new DirectorRecipeApiError(response.status, body?.error?.code ?? `HTTP_${response.status}`, body?.error?.message ?? "Director Recipe 请求失败", body?.error?.details);
  return body as T;
}

export async function listDirectorRecipes(projectId: string) { return (await request<{ items: DirectorRecipe[] }>(`/projects/${encodeURIComponent(projectId)}/director-recipes`)).items; }
export async function getDirectorRecipeBinding(projectId: string) { return (await request<{ binding: DirectorRecipeBinding | null }>(`/projects/${encodeURIComponent(projectId)}/director-recipe-binding`)).binding; }
export async function createDirectorRecipe(projectId: string, payload: { code: string; title: string; recipe: DirectorRecipeDocument; reason: string }) { return (await request<{ recipe: DirectorRecipe }>(`/projects/${encodeURIComponent(projectId)}/director-recipes`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).recipe; }
export async function createDirectorRecipeVersion(projectId: string, recipeId: string, recipe: DirectorRecipeDocument, reason: string) { return (await request<{ version: DirectorRecipeVersion }>(`/projects/${encodeURIComponent(projectId)}/director-recipes/${encodeURIComponent(recipeId)}/versions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ recipe, reason }) })).version; }
export async function bindDirectorRecipe(projectId: string, recipeVersionId: string, reason: string, expectedRevision: number | null) { return (await request<{ binding: DirectorRecipeBinding }>(`/projects/${encodeURIComponent(projectId)}/director-recipe-binding`, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ recipe_version_id: recipeVersionId, reason, expected_revision: expectedRevision }) })).binding; }
export async function listRecipeQcPolicies(projectId: string) { return (await request<{ items: QcPolicyVersionOption[] }>(`/projects/${encodeURIComponent(projectId)}/qc-policies`)).items; }

