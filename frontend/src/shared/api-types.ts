/* Auto-generated from FastAPI OpenAPI schema by scripts/export_openapi.py. Do not hand-edit. */

export interface AcceptInviteRequest {
  invite_code: string;
  username: string;
  password: string;
}

export interface AdminCreateUserRequest {
  username: string;
  password: string;
  role?: string;
}

export interface AdminResetPasswordRequest {
  new_password: string;
}

export interface AdminUpdateUserRequest {
  role?: string | null;
  status?: string | null;
  status_reason?: string | null;
}

export interface AuthCredentialsRequest {
  username: string;
  password: string;
}

export interface AuthResponse {
  account: UserAccountResponse;
}

export interface Body_import_legacy_sqlite_api_portability_import_legacy_sqlite_post {
  file: string;
}

export interface BootstrapStatusResponse {
  bootstrap_open: boolean;
}

export interface ConnectionsSummaryResponse {
  steam_id?: string | null;
  steam_api_key_configured: boolean;
  xbox_gamertag?: string | null;
  xbox_xuid?: string | null;
  xbl_api_key_configured: boolean;
  xbl_watcher_api_key_configured: boolean;
  psn_configured: boolean;
  ha_url?: string | null;
  ha_token_configured: boolean;
  ha_entity_id?: string | null;
  credential_decryption_error?: string | null;
  updated_at?: string | null;
}

export interface CreateInviteRequest {
  role?: string;
  note?: string;
  ttl_hours?: number;
}

export interface CreatePatRequest {
  name: string;
  scopes: Array<string>;
  ttl_days?: number | null;
}

export interface CreatedInviteResponse {
  code: string;
  record: InviteResponse;
}

export interface CreatedPatResponse {
  token: string;
  record: PatResponse;
}

export interface GameCreateRequest {
  title: string;
  platform: string;
  source?: string;
  completion_status?: string;
  ownership_status?: string;
  is_game_pass?: boolean;
  is_play_anywhere?: boolean;
  playtime_hours?: number;
  genres?: Array<string>;
  tags?: Array<string>;
  release_year?: number | null;
  critic_score?: number | null;
  user_rating?: number | null;
  cover_url?: string | null;
  notes?: string;
  favorite?: boolean;
}

export interface GameResponse {
  id: number;
  external_id: string;
  title: string;
  platform: string;
  source: string;
  playtime_hours: number;
  playtime_minutes: number;
  last_synced_playtime_minutes?: number | null;
  last_played?: string | null;
  completion_status: string;
  status_locked: boolean;
  ownership_status: string;
  ownership_locked: boolean;
  is_game_pass: boolean;
  is_play_anywhere: boolean;
  achievements_unlocked: number;
  achievements_total: number;
  genres: Array<string>;
  tags: Array<string>;
  critic_score?: number | null;
  user_rating?: number | null;
  release_year?: number | null;
  cover_url?: string | null;
  notes: string;
  favorite: boolean;
  canonical_id?: number | null;
  created_at: string;
  updated_at: string;
}

export interface GameUpdateRequest {
  title?: string | null;
  platform?: string | null;
  completion_status?: string | null;
  status_locked?: boolean | null;
  ownership_status?: string | null;
  ownership_locked?: boolean | null;
  is_game_pass?: boolean | null;
  is_play_anywhere?: boolean | null;
  playtime_hours?: number | null;
  genres?: Array<string> | null;
  tags?: Array<string> | null;
  replace_labels?: boolean;
  user_rating?: number | null;
  clear_rating?: boolean;
  release_year?: number | null;
  cover_url?: string | null;
  notes?: string | null;
  favorite?: boolean | null;
  playtime_prompt_muted?: boolean | null;
}

export interface HTTPValidationError {
  detail?: Array<ValidationError>;
}

export interface HealthResponse {
  status: string;
  version: string;
}

export interface ImportReportResponse {
  dry_run: boolean;
  mode: string;
  format_source: string;
  games_added: number;
  games_updated: number;
  games_skipped: number;
  sessions_added: number;
  warnings: Array<string>;
}

export interface InviteResponse {
  code_hash: string;
  created_by: string;
  role: string;
  note: string;
  created_at: string;
  expires_at?: string | null;
  used_by?: string | null;
  used_at?: string | null;
  revoked_at?: string | null;
}

export interface JobResponse {
  id: number;
  kind: string;
  provider: string;
  state: string;
  attempts: number;
  max_attempts: number;
  scheduled_at: string;
  available_at: string;
  started_at?: string | null;
  finished_at?: string | null;
  last_error_code?: string | null;
  last_error?: string | null;
  result?: Record<string, unknown> | null;
}

export interface JobsOverviewResponse {
  providers: Array<ProviderStatusResponse>;
  recent_jobs: Array<JobResponse>;
}

export interface JsonImportRequest {
  payload: Record<string, unknown>;
  mode?: string;
  dry_run?: boolean;
}

export interface LibraryStatsResponse {
  total_entries: number;
  unique_titles: number;
  playing: number;
  completed: number;
  not_played: number;
  favorites: number;
  total_playtime_hours: number;
  platforms: Record<string, number>;
}

export interface PasswordChangeRequest {
  current_password: string;
  new_password: string;
}

export interface PatResponse {
  id: string;
  name: string;
  token_prefix: string;
  scopes: Array<string>;
  created_at: string;
  last_used_at?: string | null;
  expires_at?: string | null;
  revoked_at?: string | null;
}

export interface PlaySessionResponse {
  id: number;
  game_id: number;
  platform: string;
  started_at: string;
  ended_at: string;
  duration_minutes: number;
  source: string;
  precision_level: string;
  notes?: string | null;
  achievements_unlocked: number;
  is_locked: boolean;
  created_at: string;
  updated_at: string;
}

export interface ProviderStatusResponse {
  provider: string;
  last_success_at?: string | null;
  last_failure_at?: string | null;
  last_error_code?: string | null;
  last_error?: string | null;
  next_attempt_at?: string | null;
  active_state?: string | null;
  credential_expired: boolean;
}

export interface ReadinessResponse {
  status: string;
  version: string;
  system_db: string;
  applied_system_migrations: Array<string>;
  worker_enabled: boolean;
  secret_key_configured: boolean;
  outbound_sync_enabled: boolean;
}

export interface ReconcileCandidateResponse {
  canonical_title: string;
  games: Array<GameResponse>;
}

export interface ReconcileIdsRequest {
  game_ids: Array<number>;
}

export interface TimezoneUpdateRequest {
  timezone: string;
  timezone_eras?: Array<Record<string, unknown>> | null;
}

export interface UpdateConnectionsRequest {
  steam_id?: string | null;
  steam_api_key?: string | null;
  clear_steam_api_key?: boolean;
  xbox_gamertag?: string | null;
  xbox_xuid?: string | null;
  xbl_api_key?: string | null;
  clear_xbl_api_key?: boolean;
  psn_npsso?: string | null;
  clear_psn_npsso?: boolean;
  ha_url?: string | null;
  ha_token?: string | null;
  clear_ha_token?: boolean;
  ha_entity_id?: string | null;
}

export interface UpdateSessionRequest {
  started_at?: string | null;
  ended_at?: string | null;
  duration_minutes?: number | null;
  notes?: string | null;
  is_locked?: boolean | null;
}

export interface UserAccountResponse {
  id: string;
  username: string;
  role: string;
  status: string;
  status_reason?: string | null;
  timezone: string;
  timezone_eras: Array<Record<string, unknown>>;
  onboarding_completed: boolean;
  created_at: string;
  updated_at: string;
  last_login_at?: string | null;
}

export interface ValidationError {
  loc: Array<string | number>;
  msg: string;
  type: string;
  input?: unknown;
  ctx?: Record<string, unknown>;
}
