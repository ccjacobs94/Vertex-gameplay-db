import type {
  AcceptInviteRequest,
  AdminCreateUserRequest,
  AdminUpdateUserRequest,
  AuthCredentialsRequest,
  AuthResponse,
  BootstrapStatusResponse,
  ConnectionsSummaryResponse,
  CreatedInviteResponse,
  CreatedPatResponse,
  CreateInviteRequest,
  CreatePatRequest,
  GameCreateRequest,
  GameResponse,
  GameUpdateRequest,
  ImportReportResponse,
  InviteResponse,
  JobResponse,
  JobsOverviewResponse,
  LibraryStatsResponse,
  PasswordChangeRequest,
  PatResponse,
  UpdateConnectionsRequest,
  UserAccountResponse,
} from "./api-types.ts";

export class ApiError extends Error {
  constructor(status, code, detail) {
    super(detail || `HTTP ${status}`);
    this.status = status;
    this.code = code || "http_error";
    this.detail = detail || `HTTP ${status}`;
  }
}

async function apiFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const resp = await fetch(path, {
    ...init,
    headers,
    credentials: "same-origin",
    cache: "no-store",
  });
  if (resp.status === 204) {
    return undefined as unknown as T;
  }
  const text = await resp.text();
  let parsed: Record<string, unknown> = {};
  if (text) {
    try {
      parsed = JSON.parse(text) as Record<string, unknown>;
    } catch {
      parsed = { detail: text };
    }
  }
  if (!resp.ok) {
    const code = typeof parsed.code === "string" ? parsed.code : "http_error";
    const detail = typeof parsed.detail === "string" ? parsed.detail : `HTTP ${resp.status}`;
    throw new ApiError(resp.status, code, detail);
  }
  return parsed as unknown as T;
}

export function getBootstrapStatus(): Promise<BootstrapStatusResponse> {
  return apiFetch<BootstrapStatusResponse>("/api/auth/bootstrap");
}

export function bootstrapAdmin(body: AuthCredentialsRequest): Promise<AuthResponse> {
  return apiFetch<AuthResponse>("/api/auth/bootstrap", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function login(body: AuthCredentialsRequest): Promise<AuthResponse> {
  return apiFetch<AuthResponse>("/api/auth/login", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function logout(): Promise<void> {
  return apiFetch<void>("/api/auth/logout", { method: "POST" });
}

export function acceptInvite(body: AcceptInviteRequest): Promise<AuthResponse> {
  return apiFetch<AuthResponse>("/api/auth/invites/accept", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getAccount(): Promise<UserAccountResponse> {
  return apiFetch<UserAccountResponse>("/api/account");
}

export function changePassword(body: PasswordChangeRequest): Promise<void> {
  return apiFetch<void>("/api/account/password", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateTimezone(timezone: string): Promise<UserAccountResponse> {
  return apiFetch<UserAccountResponse>("/api/account/timezone", {
    method: "PATCH",
    body: JSON.stringify({ timezone }),
  });
}

export function listPats(): Promise<Array<PatResponse>> {
  return apiFetch<Array<PatResponse>>("/api/account/tokens");
}

export function createPat(body: CreatePatRequest): Promise<CreatedPatResponse> {
  return apiFetch<CreatedPatResponse>("/api/account/tokens", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function revokePat(tokenId: string): Promise<void> {
  return apiFetch<void>(`/api/account/tokens/${encodeURIComponent(tokenId)}`, {
    method: "DELETE",
  });
}

export interface TaxonomyResponse {
  genres: Array<string>;
  tags: Array<string>;
}

export function getTaxonomy(): Promise<TaxonomyResponse> {
  return apiFetch<TaxonomyResponse>("/api/taxonomy");
}

export function listGames(search: string = "", platform: string = "", status: string = ""): Promise<Array<GameResponse>> {
  const params = new URLSearchParams();
  if (search) params.set("search", search);
  if (platform) params.set("platform", platform);
  if (status) params.set("status", status);
  const qs = params.toString();
  const suffix = qs ? `?${qs}` : "";
  return apiFetch<Array<GameResponse>>(`/api/games${suffix}`);
}

export function addGame(body: GameCreateRequest): Promise<GameResponse> {
  return apiFetch<GameResponse>("/api/games", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateGame(gameId: number, body: GameUpdateRequest): Promise<GameResponse> {
  return apiFetch<GameResponse>(`/api/games/${gameId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteGame(gameId: number): Promise<void> {
  return apiFetch<void>(`/api/games/${gameId}`, { method: "DELETE" });
}

export function getStats(): Promise<LibraryStatsResponse> {
  return apiFetch<LibraryStatsResponse>("/api/stats");
}

export function exportLibraryJson(): Promise<Record<string, unknown>> {
  return apiFetch<Record<string, unknown>>("/api/portability/export.json");
}

export function importLibraryJson(payload: Record<string, unknown>, mode: string, dryRun: boolean): Promise<ImportReportResponse> {
  return apiFetch<ImportReportResponse>("/api/portability/import/json", {
    method: "POST",
    body: JSON.stringify({ payload, mode, dry_run: dryRun }),
  });
}

export function importLegacySqlite(file: File, mode: string, dryRun: boolean): Promise<ImportReportResponse> {
  const fd = new FormData();
  fd.append("file", file);
  const params = new URLSearchParams({ mode, dry_run: String(dryRun) });
  return apiFetch<ImportReportResponse>(`/api/portability/import/legacy-sqlite?${params.toString()}`, {
    method: "POST",
    body: fd,
  });
}

export function getConnections(): Promise<ConnectionsSummaryResponse> {
  return apiFetch<ConnectionsSummaryResponse>("/api/connections");
}

export function updateConnections(body: UpdateConnectionsRequest): Promise<ConnectionsSummaryResponse> {
  return apiFetch<ConnectionsSummaryResponse>("/api/connections", {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function enqueueSteamSync(): Promise<JobResponse> {
  return apiFetch<JobResponse>("/api/sync/steam", { method: "POST" });
}

export function getJobs(): Promise<JobsOverviewResponse> {
  return apiFetch<JobsOverviewResponse>("/api/jobs");
}

export function adminListUsers(): Promise<Array<UserAccountResponse>> {
  return apiFetch<Array<UserAccountResponse>>("/api/admin/users");
}

export function adminCreateUser(body: AdminCreateUserRequest): Promise<UserAccountResponse> {
  return apiFetch<UserAccountResponse>("/api/admin/users", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function adminUpdateUser(userId: string, body: AdminUpdateUserRequest): Promise<UserAccountResponse> {
  return apiFetch<UserAccountResponse>(`/api/admin/users/${encodeURIComponent(userId)}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function adminDeleteUser(userId: string): Promise<void> {
  return apiFetch<void>(`/api/admin/users/${encodeURIComponent(userId)}`, { method: "DELETE" });
}

export function adminListInvites(): Promise<Array<InviteResponse>> {
  return apiFetch<Array<InviteResponse>>("/api/admin/invites");
}

export function adminCreateInvite(body: CreateInviteRequest): Promise<CreatedInviteResponse> {
  return apiFetch<CreatedInviteResponse>("/api/admin/invites", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function adminRevokeInvite(codeHash: string): Promise<void> {
  return apiFetch<void>(`/api/admin/invites/${encodeURIComponent(codeHash)}`, { method: "DELETE" });
}
