import type {
  AcceptInviteRequest,
  AchievementBackfillResponse,
  AdminCreateUserRequest,
  AdminResetPasswordRequest,
  AdminUpdateUserRequest,
  AuthCredentialsRequest,
  AuthResponse,
  BootstrapStatusResponse,
  CatalogEnrichmentReportResponse,
  CatalogEnrichRequest,
  CatalogSearchResultResponse,
  ConnectionsSummaryResponse,
  ConnectionTestRequest,
  ConnectionTestResponse,
  CreatedInviteResponse,
  CreatedPatResponse,
  CreateInviteRequest,
  CreatePatRequest,
  GameAchievementsResponse,
  GameAchievementsSyncResponse,
  GameCreateRequest,
  GameResponse,
  GameUpdateRequest,
  HomeAssistantEventRequest,
  HomeAssistantEventResponse,
  HomeAssistantImportCsvRequest,
  HomeAssistantImportResponse,
  HomeAssistantSyncRequest,
  HomeAssistantSyncResponse,
  ImportReportResponse,
  InviteResponse,
  JobResponse,
  JobsOverviewResponse,
  LibraryAnalyticsResponse,
  LibraryStatsResponse,
  PasswordChangeRequest,
  PatResponse,
  PlaySessionResponse,
  PlaytimePromptResolutionResponse,
  PlaytimePromptResponse,
  ReconcileCandidateResponse,
  ResetDismissalsResponse,
  SessionAnalyticsResponse,
  SessionWindowStatsResponse,
  SyncAllResponse,
  TelemetryPollResponse,
  TelemetryStatusResponse,
  TelemetrySweepResponse,
  UpdateConnectionsRequest,
  UpdateSessionRequest,
  UserAccountResponse,
  WishlistCreateRequest,
  WishlistDealEvaluationResponse,
  WishlistHistoryResponse,
  WishlistItemResponse,
  WishlistPlatformsUpdateRequest,
  WishlistPriceRecordRequest,
  WishlistPriceRecordResponse,
  WishlistPurchaseRequest,
  WishlistPurchaseResponse,
  WishlistUpdateRequest,
} from "./api-types.ts";

export class ApiError extends Error {
  status: number;
  code: string;
  detail: string;

  constructor(status: number, code: string, detail: string) {
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

export function testConnection(body: ConnectionTestRequest): Promise<ConnectionTestResponse> {
  return apiFetch<ConnectionTestResponse>("/api/connections/test", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function enqueueSteamSync(): Promise<JobResponse> {
  return apiFetch<JobResponse>("/api/sync/steam", { method: "POST" });
}

export function enqueueXboxSync(): Promise<JobResponse> {
  return apiFetch<JobResponse>("/api/sync/xbox", { method: "POST" });
}

export function enqueuePsnSync(): Promise<JobResponse> {
  return apiFetch<JobResponse>("/api/sync/psn", { method: "POST" });
}

export function enqueueGogSync(): Promise<JobResponse> {
  return apiFetch<JobResponse>("/api/sync/gog", { method: "POST" });
}

export function enqueueAllSyncs(): Promise<SyncAllResponse> {
  return apiFetch<SyncAllResponse>("/api/sync", { method: "POST" });
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

export function adminResetPassword(userId: string, body: AdminResetPasswordRequest): Promise<UserAccountResponse> {
  return apiFetch<UserAccountResponse>(`/api/admin/users/${encodeURIComponent(userId)}/reset-password`, {
    method: "POST",
    body: JSON.stringify(body),
  });
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

export function getGameAchievements(gameId: number, autoSync = true): Promise<GameAchievementsResponse> {
  return apiFetch<GameAchievementsResponse>(`/api/games/${gameId}/achievements?auto_sync=${String(autoSync)}`);
}

export function syncGameAchievements(gameId: number): Promise<GameAchievementsSyncResponse> {
  return apiFetch<GameAchievementsSyncResponse>(`/api/games/${gameId}/achievements/sync`, {
    method: "POST",
  });
}

export function getReconcileCandidates(): Promise<Array<ReconcileCandidateResponse>> {
  return apiFetch<Array<ReconcileCandidateResponse>>("/api/reconcile/candidates");
}

export function confirmReconcile(gameIds: Array<number>): Promise<{ canonical_id: number }> {
  return apiFetch<{ canonical_id: number }>("/api/reconcile/confirm", {
    method: "POST",
    body: JSON.stringify({ game_ids: gameIds }),
  });
}

export function dismissReconcile(gameIds: Array<number>): Promise<void> {
  return apiFetch<void>("/api/reconcile/dismiss", {
    method: "POST",
    body: JSON.stringify({ game_ids: gameIds }),
  });
}

export function unlinkReconcile(gameId: number): Promise<void> {
  return apiFetch<void>(`/api/reconcile/unlink/${gameId}`, { method: "POST" });
}

export function resetReconcileDismissals(): Promise<ResetDismissalsResponse> {
  return apiFetch<ResetDismissalsResponse>("/api/reconcile/reset-dismissals", { method: "POST" });
}

export function listPlaytimePrompts(): Promise<Array<PlaytimePromptResponse>> {
  return apiFetch<Array<PlaytimePromptResponse>>("/api/playtime-prompts");
}

export function resolvePlaytimePrompt(promptId: number, action: string): Promise<PlaytimePromptResolutionResponse> {
  return apiFetch<PlaytimePromptResolutionResponse>(`/api/playtime-prompts/${promptId}/resolve`, {
    method: "POST",
    body: JSON.stringify({ action }),
  });
}

export function listSessions(gameId?: number, limit = 50): Promise<Array<PlaySessionResponse>> {
  const qs = new URLSearchParams();
  if (gameId !== undefined) qs.set("game_id", String(gameId));
  qs.set("limit", String(limit));
  return apiFetch<Array<PlaySessionResponse>>(`/api/sessions?${qs.toString()}`);
}

export function updateSession(sessionId: number, body: UpdateSessionRequest): Promise<PlaySessionResponse> {
  return apiFetch<PlaySessionResponse>(`/api/sessions/${sessionId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteSession(sessionId: number): Promise<void> {
  return apiFetch<void>(`/api/sessions/${sessionId}`, { method: "DELETE" });
}

export function getSessionWindowStats(days = 30): Promise<SessionWindowStatsResponse> {
  return apiFetch<SessionWindowStatsResponse>(`/api/sessions/stats?days=${days}`);
}

export function getDailyLedger(): Promise<Array<Record<string, unknown>>> {
  return apiFetch<Array<Record<string, unknown>>>("/api/ledger");
}

export function rebuildDailyLedger(): Promise<Array<Record<string, unknown>>> {
  return apiFetch<Array<Record<string, unknown>>>("/api/ledger/rebuild", { method: "POST" });
}

export function ingestHomeAssistantEvent(body: HomeAssistantEventRequest): Promise<HomeAssistantEventResponse> {
  return apiFetch<HomeAssistantEventResponse>("/api/integrations/homeassistant/events", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function importHomeAssistantCsv(body: HomeAssistantImportCsvRequest): Promise<HomeAssistantImportResponse> {
  return apiFetch<HomeAssistantImportResponse>("/api/integrations/homeassistant/import-csv", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function syncHomeAssistantHistory(body: HomeAssistantSyncRequest): Promise<HomeAssistantSyncResponse> {
  return apiFetch<HomeAssistantSyncResponse>("/api/integrations/homeassistant/sync", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function backfillAchievementSessions(): Promise<AchievementBackfillResponse> {
  return apiFetch<AchievementBackfillResponse>("/api/sessions/backfill-achievements", {
    method: "POST",
  });
}

export function getTelemetryStatus(): Promise<TelemetryStatusResponse> {
  return apiFetch<TelemetryStatusResponse>("/api/telemetry/status");
}

export function pollTelemetry(): Promise<TelemetryPollResponse> {
  return apiFetch<TelemetryPollResponse>("/api/telemetry/poll", { method: "POST" });
}

export function runTelemetrySweep(): Promise<TelemetrySweepResponse> {
  return apiFetch<TelemetrySweepResponse>("/api/telemetry/sweep", { method: "POST" });
}

export function getLibraryAnalytics(search = "", platform = "", status = ""): Promise<LibraryAnalyticsResponse> {
  const qs = new URLSearchParams();
  if (search) qs.set("search", search);
  if (platform) qs.set("platform", platform);
  if (status) qs.set("status", status);
  const clientTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (clientTz) qs.set("client_timezone", clientTz);
  const query = qs.toString();
  return apiFetch<LibraryAnalyticsResponse>(`/api/analytics${query ? `?${query}` : ""}`);
}

export function getSessionAnalytics(days = 365, limit = 50, offset = 0, platform = ""): Promise<SessionAnalyticsResponse> {
  const qs = new URLSearchParams();
  qs.set("days", String(days));
  qs.set("limit", String(limit));
  qs.set("offset", String(offset));
  if (platform) qs.set("platform", platform);
  const clientTz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  if (clientTz) qs.set("client_timezone", clientTz);
  return apiFetch<SessionAnalyticsResponse>(`/api/analytics/sessions?${qs.toString()}`);
}

export function searchCatalog(query: string, limit = 10): Promise<Array<CatalogSearchResultResponse>> {
  const qs = new URLSearchParams({ q: query, limit: String(limit) });
  return apiFetch<Array<CatalogSearchResultResponse>>(`/api/catalog/search?${qs.toString()}`);
}

export function enrichLibrary(body: CatalogEnrichRequest = { limit: 500, force: false }): Promise<CatalogEnrichmentReportResponse> {
  return apiFetch<CatalogEnrichmentReportResponse>("/api/catalog/enrich", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function listWishlist(status = "active"): Promise<Array<WishlistItemResponse>> {
  const qs = new URLSearchParams({ status });
  return apiFetch<Array<WishlistItemResponse>>(`/api/wishlist?${qs.toString()}`);
}

export function createWishlistItem(body: WishlistCreateRequest): Promise<WishlistItemResponse> {
  return apiFetch<WishlistItemResponse>("/api/wishlist", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateWishlistItem(itemId: number, body: WishlistUpdateRequest): Promise<WishlistItemResponse> {
  return apiFetch<WishlistItemResponse>(`/api/wishlist/${itemId}`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function updateWishlistPlatforms(itemId: number, body: WishlistPlatformsUpdateRequest): Promise<WishlistItemResponse> {
  return apiFetch<WishlistItemResponse>(`/api/wishlist/${itemId}/platforms`, {
    method: "PATCH",
    body: JSON.stringify(body),
  });
}

export function deleteWishlistItem(itemId: number): Promise<void> {
  return apiFetch<void>(`/api/wishlist/${itemId}`, { method: "DELETE" });
}

export function recordWishlistPrice(itemId: number, body: WishlistPriceRecordRequest): Promise<WishlistPriceRecordResponse> {
  return apiFetch<WishlistPriceRecordResponse>(`/api/wishlist/${itemId}/price`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function purchaseWishlistItem(itemId: number, body: WishlistPurchaseRequest): Promise<WishlistPurchaseResponse> {
  return apiFetch<WishlistPurchaseResponse>(`/api/wishlist/${itemId}/purchase`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function getWishlistHistory(itemId: number, limit = 50): Promise<WishlistHistoryResponse> {
  return apiFetch<WishlistHistoryResponse>(`/api/wishlist/${itemId}/history?limit=${limit}`);
}

export function getWishlistDeals(smartUpgradesOnly = false, includeAboveTarget = false, platform = ""): Promise<WishlistDealEvaluationResponse> {
  const qs = new URLSearchParams({
    smart_upgrades_only: String(smartUpgradesOnly),
    include_above_target: String(includeAboveTarget),
  });
  if (platform) qs.set("platform", platform);
  return apiFetch<WishlistDealEvaluationResponse>(`/api/wishlist/deals?${qs.toString()}`);
}
