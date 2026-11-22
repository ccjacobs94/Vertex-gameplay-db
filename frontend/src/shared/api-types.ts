/* Auto-generated from FastAPI OpenAPI schema by scripts/export_openapi.py. Do not hand-edit. */

export interface AcceptInviteRequest {
  invite_code: string;
  username: string;
  password: string;
}

export interface AchievementBackfillResponse {
  sessions_created: number;
  games_backfilled: number;
}

export interface AchievementPlatformStatusResponse {
  game_id: number;
  platform: string;
  short_platform: string;
  source: string;
  unlocked: boolean;
  unlock_time?: string | null;
  reward_type?: string | null;
  reward_value?: string | null;
  rarity_pct?: number | null;
}

export interface ActiveTelemetrySessionResponse {
  provider: string;
  external_title_id: string;
  game_id: number;
  title: string;
  platform: string;
  source: string;
  started_at: string;
  last_seen_at: string;
  baseline_minutes: number;
  baseline_achievements: number;
  rich_presence?: string | null;
  detection_method: string;
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

export interface AnalyticsSummaryResponse {
  total_entries: number;
  unique_titles: number;
  total_hours: number;
  played_games: number;
  beaten_count: number;
  dropped_count: number;
  playing_count: number;
  shelf_of_shame_count: number;
  favorites_count: number;
  completion_rate: number;
  drop_rate: number;
  avg_hours: number;
  avg_played_hours: number;
  avg_beaten_hours: number;
  avg_dropped_hours: number;
  dominant_system: string;
  dominant_genre: string;
  achievements_unlocked: number;
  achievements_total: number;
  achievement_rate: number;
  perfect_games_count: number;
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

export interface BucketAnalyticsItemResponse {
  key: string;
  label: string;
  count: number;
  count_pct: number;
  hours: number;
  hours_pct: number;
}

export interface CanonicalGameResponse {
  id: number;
  normalized_title: string;
  title: string;
  release_year?: number | null;
  developer: string;
  publisher: string;
  cover_url?: string | null;
  genres: Array<string>;
  critic_score?: number | null;
  library_game_id?: number | null;
  created_at: string;
  updated_at: string;
}

export interface CatalogEnrichRequest {
  limit?: number;
  force?: boolean;
}

export interface CatalogEnrichmentReportResponse {
  games_enriched: number;
  covers_filled: number;
  remaining_without_genres: number;
  used_network: boolean;
  message: string;
}

export interface CatalogSearchResultResponse {
  normalized_title: string;
  title: string;
  steam_appid?: string | null;
  genres: Array<string>;
  tags: Array<string>;
  release_year?: number | null;
  critic_score?: number | null;
  cover_url?: string | null;
  source: string;
  retrieved_at: string;
  cached: boolean;
}

export interface CircadianRhythmResponse {
  grid: Array<Array<number>>;
  time_buckets: Record<string, number>;
  total_events: number;
  peak_day: string;
  peak_hour: string;
  archetype: string;
  eras_count: number;
}

export interface ConnectionTestRequest {
  provider: string;
  credential?: string | null;
  secondary_credential?: string | null;
}

export interface ConnectionTestResponse {
  status: string;
  platform: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface ConnectionsSummaryResponse {
  steam_id?: string | null;
  steam_api_key_configured: boolean;
  xbox_gamertag?: string | null;
  xbox_xuid?: string | null;
  xbl_api_key_configured: boolean;
  xbl_watcher_api_key_configured: boolean;
  psn_configured: boolean;
  gog_user_id?: string | null;
  gog_username?: string | null;
  gog_token_configured?: boolean;
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

export interface EditionPlatformSkuResponse {
  id: number;
  edition_id: number;
  platform_family: string;
  platform_label: string;
  storefront: string;
  store_sku_id: string;
  itad_plain?: string | null;
  store_url: string;
  msrp_usd?: number | null;
  current_price_usd?: number | null;
  regular_price_usd?: number | null;
  discount_pct: number;
  historical_low_usd?: number | null;
  is_authorized_seller: boolean;
  is_drm_free: boolean;
  last_checked_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface GameAchievementsResponse {
  game_id: number;
  unified_summary: UnifiedAchievementSummaryResponse;
  per_platform_summary: Array<PlatformAchievementSummaryResponse>;
  achievements: Array<GroupedAchievementResponse>;
}

export interface GameAchievementsSyncResponse {
  game_id: number;
  unified_summary: UnifiedAchievementSummaryResponse;
  per_platform_summary: Array<PlatformAchievementSummaryResponse>;
  achievements: Array<GroupedAchievementResponse>;
  synced_copies: number;
  synced_providers: Array<string>;
  warnings: Array<string>;
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

export interface GameEditionResponse {
  id: number;
  canonical_game_id: number;
  edition_name: string;
  edition_tier: string;
  tier_rank: number;
  is_base_edition: boolean;
  included_dlc_notes: string;
  cover_url?: string | null;
  skus: Array<EditionPlatformSkuResponse>;
  created_at: string;
  updated_at: string;
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

export interface GenreAnalyticsItemResponse {
  genre: string;
  count: number;
  hours: number;
  beaten_count: number;
  playing_count: number;
  backlog_count: number;
  dropped_count: number;
  playtime_pct: number;
  completion_rate: number;
}

export interface GroupedAchievementResponse {
  key: string;
  name: string;
  description: string;
  icon_url?: string | null;
  icon_gray_url?: string | null;
  is_hidden: boolean;
  unlocked: boolean;
  unlock_time?: string | null;
  reward_type?: string | null;
  reward_value?: string | null;
  rarity_pct?: number | null;
  unlocked_platforms: Array<string>;
  platforms: Array<AchievementPlatformStatusResponse>;
}

export interface HTTPValidationError {
  detail?: Array<ValidationError>;
}

export interface HealthResponse {
  status: string;
  version: string;
}

export interface HomeAssistantEventRequest {
  event: string;
  title?: string | null;
  platform?: string | null;
  external_id?: string | null;
  source?: string;
  started_at?: string | null;
  ended_at?: string | null;
  duration_minutes?: number | null;
  active_minutes?: number | null;
  idle_minutes?: number | null;
  wall_clock_minutes?: number | null;
  achievements_unlocked?: number;
  notes?: string | null;
  idempotency_key?: string | null;
}

export interface HomeAssistantEventResponse {
  status: string;
  game_id: number;
  session_id?: number | null;
  duration_minutes: number;
  idempotency_key: string;
}

export interface HomeAssistantImportCsvRequest {
  csv_content: string;
  platform?: string;
  max_gap_seconds?: number;
}

export interface HomeAssistantImportResponse {
  status: string;
  imported_count: number;
  skipped_count: number;
  total_spans: number;
}

export interface HomeAssistantSyncRequest {
  ha_url?: string | null;
  ha_token?: string | null;
  entity_id?: string | null;
  platform?: string;
  days_back?: number;
  max_gap_seconds?: number;
}

export interface HomeAssistantSyncResponse {
  status: string;
  states_retrieved: number;
  imported_count: number;
  skipped_count: number;
  total_spans: number;
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

export interface LibraryAnalyticsResponse {
  summary: AnalyticsSummaryResponse;
  platforms: Array<PlatformAnalyticsItemResponse>;
  genres: Array<GenreAnalyticsItemResponse>;
  tags: Array<TagAnalyticsItemResponse>;
  statuses: Record<string, StatusAnalyticsItemResponse>;
  commitment_tiers: Array<BucketAnalyticsItemResponse>;
  eras: Array<BucketAnalyticsItemResponse>;
  avg_user_rating?: number | null;
  avg_critic_score?: number | null;
  backlog_gems: Array<Record<string, unknown>>;
  hall_of_fame: Array<Record<string, unknown>>;
  circadian_rhythm: CircadianRhythmResponse;
  subscription_roi: Record<string, unknown>;
  cross_platform: Record<string, unknown>;
  trophy_pyramid: Record<string, unknown>;
  almost_perfect: Array<Record<string, unknown>>;
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

export interface OwnershipConflictResponse {
  already_in_library: boolean;
  conflicted_platforms: Array<string>;
  conflict_warning?: string | null;
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

export interface PlatformAchievementSummaryResponse {
  game_id: number;
  title: string;
  platform: string;
  short_platform: string;
  source: string;
  unlocked: number;
  total: number;
  percentage: number;
  achievements_synced_at?: string | null;
}

export interface PlatformAnalyticsItemResponse {
  name: string;
  count: number;
  hours: number;
  beaten_count: number;
  playtime_pct: number;
  library_pct: number;
  completion_rate: number;
  top_game_title?: string | null;
  top_game_hours: number;
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

export interface PlaytimePromptResolutionResponse {
  prompt_id: number;
  action: string;
  game_id: number;
  completion_status: string;
  status_locked: boolean;
  playtime_prompt_muted: boolean;
}

export interface PlaytimePromptResponse {
  id: number;
  game_id: number;
  title: string;
  platform: string;
  cover_url?: string | null;
  current_status: string;
  previous_hours: number;
  new_hours: number;
  delta_hours: number;
  total_playtime_hours: number;
  detected_at: string;
  status: string;
  prompt_type?: string;
  sessions_count?: number;
  achievements_unlocked?: number;
  achievements_total?: number;
  user_rating?: number | null;
}

export interface PriceHistoryEntryResponse {
  id: number;
  sku_id: number;
  storefront: string;
  price_usd: number;
  regular_price_usd?: number | null;
  discount_pct: number;
  is_all_time_low: boolean;
  source: string;
  recorded_at: string;
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

export interface PushSubscribeRequest {
  endpoint: string;
  keys?: PushSubscriptionKeys | null;
  p256dh?: string | null;
  auth?: string | null;
}

export interface PushSubscribeResponse {
  id: number;
  endpoint: string;
  created_at: string;
}

export interface PushSubscriptionKeys {
  p256dh: string;
  auth: string;
}

export interface PushTestResponse {
  status: string;
  sent: number;
  failed: number;
  removed: number;
  errors?: Array<string>;
}

export interface PushUnsubscribeRequest {
  endpoint: string;
}

export interface PushVapidKeyResponse {
  enabled: boolean;
  public_key: string;
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

export interface ResetDismissalsResponse {
  cleared: number;
}

export interface ResolvePlaytimePromptRequest {
  action: string;
}

export interface SessionAnalyticsResponse {
  kpis: Record<string, unknown>;
  heatmap_365: Array<Record<string, unknown>>;
  circadian_clock: Record<string, unknown>;
  snack_vs_binge: Record<string, unknown>;
  rotation_gantt_30d: Record<string, unknown>;
  recent_sessions: Array<Record<string, unknown>>;
  total_sessions: number;
  limit: number;
  offset: number;
}

export interface SessionWindowStatsResponse {
  total_sessions: number;
  total_minutes: number;
  average_duration_minutes: number;
  daily_playtime_series: Array<Record<string, unknown>>;
  platform_distribution: Record<string, number>;
}

export interface StatusAnalyticsItemResponse {
  name: string;
  count: number;
  count_pct: number;
  hours: number;
  hours_pct: number;
}

export interface SyncAllResponse {
  queued_count: number;
  providers: Array<string>;
  jobs: Array<JobResponse>;
}

export interface TagAnalyticsItemResponse {
  tag: string;
  count: number;
  hours: number;
  dropped_count: number;
  drop_rate: number;
  completion_rate: number;
}

export interface TelemetryPollResponse {
  status: string;
  events: Array<Record<string, unknown>>;
  active_sessions: Array<ActiveTelemetrySessionResponse>;
  errors: Array<string>;
}

export interface TelemetryProvidersResponse {
  xbox: boolean;
  xbox_rta: boolean;
  gog: boolean;
}

export interface TelemetryStatusResponse {
  telemetry_enabled: boolean;
  outbound_sync_enabled: boolean;
  poll_interval_seconds: number;
  daily_sweep_interval_seconds: number;
  flush_delay_seconds: number;
  providers: TelemetryProvidersResponse;
  active_sessions: Array<ActiveTelemetrySessionResponse>;
}

export interface TelemetrySweepResponse {
  status: string;
  reconciled_count: number;
  titles_checked: number;
  providers: Array<string>;
  errors: Array<string>;
}

export interface TimezoneUpdateRequest {
  timezone: string;
  timezone_eras?: Array<Record<string, unknown>> | null;
}

export interface UnifiedAchievementSummaryResponse {
  unlocked: number;
  total: number;
  percentage: number;
  overlap_count: number;
}

export interface UpdateConnectionsRequest {
  steam_id?: string | null;
  steam_api_key?: string | null;
  clear_steam_api_key?: boolean;
  xbox_gamertag?: string | null;
  xbox_xuid?: string | null;
  xbl_api_key?: string | null;
  clear_xbl_api_key?: boolean;
  xbl_watcher_api_key?: string | null;
  clear_xbl_watcher_api_key?: boolean;
  psn_npsso?: string | null;
  clear_psn_npsso?: boolean;
  gog_user_id?: string | null;
  gog_username?: string | null;
  gog_access_token?: string | null;
  gog_refresh_token?: string | null;
  clear_gog_tokens?: boolean;
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

export interface WishlistCandidateResponse {
  title: string;
  normalized_title: string;
  release_year?: number | null;
  developer?: string;
  publisher?: string;
  cover_url?: string | null;
  genres?: Array<string>;
  storefront: string;
  store_sku_id: string;
  platform_hints?: Array<string>;
  current_price_usd?: number | null;
  ownership_conflict: OwnershipConflictResponse;
}

export interface WishlistCandidatesSearchResponse {
  query: string;
  candidates: Array<WishlistCandidateResponse>;
  errors?: Array<string>;
}

export interface WishlistCreateRequest {
  title: string;
  platform_targets?: Array<WishlistTargetInput | string>;
  release_year?: number | null;
  developer?: string;
  publisher?: string;
  cover_url?: string | null;
  genres?: Array<string>;
  critic_score?: number | null;
  allow_smart_upgrade?: boolean;
  preferred_edition_id?: number | null;
  notes?: string;
  force_track_owned?: boolean;
}

export interface WishlistDealEvaluationResponse {
  total_deals: number;
  smart_upgrades_count: number;
  all_time_lows_count: number;
  deals: Array<WishlistDealResponse>;
}

export interface WishlistDealResponse {
  wishlist_item_id: number;
  canonical_game_id: number;
  game_title: string;
  cover_url?: string | null;
  edition_id: number;
  edition_name: string;
  edition_tier: string;
  tier_rank: number;
  sku_id: number;
  platform_family: string;
  platform_label: string;
  storefront: string;
  store_url: string;
  current_price_usd: number;
  regular_price_usd?: number | null;
  discount_pct: number;
  target_price_usd: number;
  savings_below_target_usd: number;
  current_base_edition_price_usd?: number | null;
  is_smart_upgrade: boolean;
  is_all_time_low: boolean;
  is_drm_free: boolean;
  deal_headline: string;
}

export interface WishlistHistoryResponse {
  item: WishlistItemResponse;
  price_history_by_sku: Record<string, Array<PriceHistoryEntryResponse>>;
}

export interface WishlistItemResponse {
  id: number;
  canonical_game_id: number;
  preferred_edition_id?: number | null;
  allow_smart_upgrade: boolean;
  status: string;
  notes: string;
  added_via: string;
  created_at: string;
  updated_at: string;
  canonical_game: CanonicalGameResponse;
  targets: Array<WishlistPlatformTargetResponse>;
  editions: Array<GameEditionResponse>;
  skus: Array<EditionPlatformSkuResponse>;
  conflict: OwnershipConflictResponse;
}

export interface WishlistPlatformTargetResponse {
  id: number;
  wishlist_item_id: number;
  platform_family: string;
  allowed_storefronts: Array<string>;
  target_price_usd: number;
  is_active: boolean;
  muted_owned_conflict: boolean;
  last_alerted_at?: string | null;
  last_alerted_price_usd?: number | null;
  last_alerted_edition_id?: number | null;
  created_at: string;
  updated_at: string;
}

export interface WishlistPlatformsUpdateRequest {
  platform_targets?: Array<WishlistTargetInput | string>;
  remove_platforms?: Array<string>;
  allow_smart_upgrade?: boolean | null;
  force_track_owned?: boolean;
}

export interface WishlistPriceRecordRequest {
  price_usd: number;
  platform?: string;
  storefront?: string;
  edition_name?: string;
  edition_tier?: string | null;
  regular_price_usd?: number | null;
  store_url?: string;
  store_sku_id?: string | null;
  is_drm_free?: boolean;
  is_authorized_seller?: boolean;
}

export interface WishlistPriceRecordResponse {
  sku_id: number;
  price_usd: number;
  is_all_time_low: boolean;
  historical_low_usd: number;
  item: WishlistItemResponse;
}

export interface WishlistPurchaseRequest {
  platform?: string;
  edition_name?: string | null;
  price_paid_usd?: number | null;
  completion_status?: string;
}

export interface WishlistPurchaseResponse {
  item: WishlistItemResponse;
  library_game_id: number;
  already_purchased: boolean;
}

export interface WishlistSyncRequest {
  wishlist_item_id?: number | null;
  force?: boolean;
  max_age_seconds?: number;
}

export interface WishlistTargetInput {
  platform: string;
  target_price_usd?: number;
  allowed_storefronts?: Array<string>;
  is_active?: boolean;
}

export interface WishlistUpdateRequest {
  allow_smart_upgrade?: boolean | null;
  preferred_edition_id?: number | null;
  status?: string | null;
  notes?: string | null;
}
