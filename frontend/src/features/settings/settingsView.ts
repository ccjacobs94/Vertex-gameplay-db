import type { UserAccountResponse } from "../../shared/api-types.ts";
import {
  backfillAchievementSessions,
  changePassword,
  createPat,
  enqueueAllSyncs,
  enqueueGogSync,
  enqueuePsnSync,
  enqueueSteamSync,
  enqueueXboxSync,
  getConnections,
  getJobs,
  getTelemetryStatus,
  listPats,
  pollTelemetry,
  revokePat,
  runTelemetrySweep,
  testConnection,
  updateConnections,
  updateTimezone,
} from "../../shared/api.ts";
import { clearEl, el, showBanner } from "../../shared/dom.ts";

export async function renderSettingsView(container: HTMLElement, account: UserAccountResponse, onAccountUpdated?: (updated: UserAccountResponse) => void): Promise<void> {
  clearEl(container);
  const bannerBox = el("div");
  const jobsBox = el("div", { class: "stack" });
  const telemetryBox = el("div", { class: "stack" });
  const patsBox = el("div", { class: "stack" });

  const steamStatusLbl = el("span", { class: "muted" });
  const xboxStatusLbl = el("span", { class: "muted" });
  const psnStatusLbl = el("span", { class: "muted" });
  const gogStatusLbl = el("span", { class: "muted" });
  const haStatusLbl = el("span", { class: "muted" });
  let gogTokenConfigured = false;

  const tzIn = el("input", { type: "text", value: account.timezone || "auto", "aria-label": "Account timezone" }) as HTMLInputElement;

  // Steam
  const steamIdIn = el("input", { type: "text", placeholder: "SteamID64 or vanity" }) as HTMLInputElement;
  const steamKeyIn = el("input", { type: "password", placeholder: "Steam Web API key (encrypted)" }) as HTMLInputElement;

  // Xbox
  const xboxGamertagIn = el("input", { type: "text", placeholder: "Xbox Gamertag" }) as HTMLInputElement;
  const xboxXuidIn = el("input", { type: "text", placeholder: "Resolved XUID (optional)" }) as HTMLInputElement;
  const xblKeyIn = el("input", { type: "password", placeholder: "OpenXBL API key (encrypted)" }) as HTMLInputElement;
  const xblWatcherKeyIn = el("input", { type: "password", placeholder: "Optional secondary XBL watcher key" }) as HTMLInputElement;

  // PSN
  const psnNpssoIn = el("input", { type: "password", placeholder: "PSN NPSSO token or JSON (encrypted)" }) as HTMLInputElement;

  // GOG
  const gogUsernameIn = el("input", { type: "text", placeholder: "GOG public username (optional)" }) as HTMLInputElement;
  const gogUserIdIn = el("input", { type: "text", placeholder: "GOG user ID (optional)" }) as HTMLInputElement;
  const gogAccessIn = el("input", { type: "password", placeholder: "GOG access token or OAuth JSON/code URL" }) as HTMLInputElement;
  const gogRefreshIn = el("input", { type: "password", placeholder: "GOG refresh token (optional)" }) as HTMLInputElement;

  // Home Assistant
  const haUrlIn = el("input", { type: "url", placeholder: "https://homeassistant.local:8123" }) as HTMLInputElement;
  const haTokenIn = el("input", { type: "password", placeholder: "Home Assistant Long-Lived Token" }) as HTMLInputElement;
  const haEntityIn = el("input", { type: "text", placeholder: "sensor.xbox_now_playing" }) as HTMLInputElement;

  const curPwIn = el("input", { type: "password", required: true }) as HTMLInputElement;
  const newPwIn = el("input", { type: "password", required: true, minlength: "8" }) as HTMLInputElement;

  const patNameIn = el("input", { type: "text", placeholder: "Home Assistant / MCP script", "aria-label": "Token name" }) as HTMLInputElement;
  const patOut = el("pre", { class: "code-out" });

  async function runProviderTest(provider: string, credential?: string, secondaryCredential?: string): Promise<void> {
    showBanner(bannerBox, "");
    try {
      const res = await testConnection({
        provider,
        credential: credential ? credential.trim() : null,
        secondary_credential: secondaryCredential ? secondaryCredential.trim() : null,
      });
      showBanner(
        bannerBox,
        `${provider.toUpperCase()}: ${res.message}`,
        res.status === "ok" ? "info" : "error"
      );
    } catch (err) {
      showBanner(bannerBox, err instanceof Error ? err.message : `${provider} test failed`, "error");
    }
  }

  async function refreshData(): Promise<void> {
    try {
      const [conns, jobsData, pats, telStatus] = await Promise.all([
        getConnections(),
        getJobs(),
        listPats(),
        getTelemetryStatus(),
      ]);

      steamIdIn.value = conns.steam_id || "";
      steamStatusLbl.textContent = conns.steam_api_key_configured
        ? "Steam Web API key: configured (encrypted)"
        : "Steam Web API key: not configured";

      xboxGamertagIn.value = conns.xbox_gamertag || "";
      xboxXuidIn.value = conns.xbox_xuid || "";
      xboxStatusLbl.textContent = `OpenXBL key: ${conns.xbl_api_key_configured ? "configured" : "not set"} • Watcher key: ${conns.xbl_watcher_api_key_configured ? "configured" : "not set"}`;

      psnStatusLbl.textContent = conns.psn_configured
        ? "PlayStation NPSSO token: configured (encrypted)"
        : "PlayStation NPSSO token: not configured";

      gogUsernameIn.value = conns.gog_username || "";
      gogUserIdIn.value = conns.gog_user_id || "";
      gogTokenConfigured = Boolean(conns.gog_token_configured);
      gogStatusLbl.textContent = conns.gog_token_configured
        ? `GOG Galaxy OAuth tokens: configured${conns.gog_username ? ` (${conns.gog_username})` : ""}`
        : "GOG Galaxy OAuth tokens: not configured (public username library fallback available)";

      haUrlIn.value = conns.ha_url || "";
      haEntityIn.value = conns.ha_entity_id || "";
      haStatusLbl.textContent = conns.ha_token_configured
        ? "Home Assistant token: configured (encrypted)"
        : "Home Assistant token: not configured";

      if (conns.credential_decryption_error) {
        showBanner(bannerBox, conns.credential_decryption_error, "error");
      }

      clearEl(telemetryBox);
      telemetryBox.appendChild(el("p", { class: "muted" }, [
        `Telemetry: ${telStatus.telemetry_enabled ? "enabled" : "disabled"} • Outbound sync: ${telStatus.outbound_sync_enabled ? "enabled" : "disabled"} • Poll: ${Math.round(telStatus.poll_interval_seconds)}s • Flush delay: ${telStatus.flush_delay_seconds}s`,
      ]));
      telemetryBox.appendChild(el("p", { class: "muted" }, [
        `Telemetry sources — Xbox Polling: ${telStatus.providers.xbox ? "ready" : "off"} • Xbox RTA WS: ${telStatus.providers.xbox_rta ? "ready" : "off"} • GOG Presence: ${telStatus.providers.gog ? "ready" : "off"}`,
      ]));
      if (!telStatus.active_sessions.length) {
        telemetryBox.appendChild(el("p", { class: "muted" }, ["No active live telemetry session currently tracked."]));
      } else {
        for (const sess of telStatus.active_sessions) {
          telemetryBox.appendChild(el("div", { class: "stat-card" }, [
            el("strong", {}, [`ACTIVE [${sess.provider.toUpperCase()}] ${sess.title}`]),
            el("span", { class: "muted" }, [`Started: ${sess.started_at} • Last seen: ${sess.last_seen_at} (${sess.detection_method})`]),
            sess.rich_presence ? el("span", { class: "muted" }, [`Status: ${sess.rich_presence}`]) : null,
          ]));
        }
      }

      clearEl(jobsBox);
      if (!jobsData.providers.length && !jobsData.recent_jobs.length) {
        jobsBox.appendChild(el("p", { class: "muted" }, ["No sync jobs recorded yet."]));
      }
      for (const p of jobsData.providers) {
        jobsBox.appendChild(el("div", { class: "stat-card" }, [
          el("strong", {}, [p.provider.toUpperCase()]),
          el("span", { class: "muted" }, [`State: ${p.active_state || "idle"}${p.credential_expired ? " (CREDENTIALS EXPIRED)" : ""}`]),
          p.last_success_at ? el("span", { class: "muted" }, [`Last success: ${p.last_success_at}`]) : null,
          p.next_attempt_at ? el("span", { class: "muted" }, [`Next attempt: ${p.next_attempt_at}`]) : null,
          p.last_error ? el("span", { class: "error-text" }, [`Last error: ${p.last_error}`]) : null,
        ]));
      }
      for (const j of jobsData.recent_jobs.slice(0, 8)) {
        jobsBox.appendChild(el("div", { class: "row-between" }, [
          el("span", { class: "muted" }, [`Job #${j.id} ${j.kind}: ${j.state} (${j.attempts}/${j.max_attempts})`]),
          el("span", { class: "muted" }, [j.finished_at || j.scheduled_at]),
        ]));
      }

      clearEl(patsBox);
      for (const t of pats) {
        patsBox.appendChild(el("div", { class: "row-between" }, [
          el("span", {}, [`${t.name} (${t.token_prefix}...) [${t.scopes.join(", ")}]${t.revoked_at ? " REVOKED" : ""}`]),
          t.revoked_at ? null : el("button", {
            type: "button",
            class: "btn btn-ghost",
            onClick: async () => {
              try {
                await revokePat(t.id);
                await refreshData();
              } catch (err) {
                showBanner(bannerBox, err instanceof Error ? err.message : "Could not revoke token", "error");
              }
            },
          }, ["Revoke"]),
        ]));
      }
    } catch (err) {
      showBanner(bannerBox, err instanceof Error ? err.message : "Failed loading settings", "error");
    }
  }

  container.appendChild(el("section", { class: "stack" }, [
    el("h1", {}, ["Settings & Sync"]),
    bannerBox,
    el("div", { class: "panel" }, [
      el("h2", {}, ["Platform Connections & Sync"]),
      el("form", {
        class: "stack",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          try {
            await updateConnections({
              steam_id: steamIdIn.value,
              steam_api_key: steamKeyIn.value || null,
              xbox_gamertag: xboxGamertagIn.value,
              xbox_xuid: xboxXuidIn.value,
              xbl_api_key: xblKeyIn.value || null,
              xbl_watcher_api_key: xblWatcherKeyIn.value || null,
              psn_npsso: psnNpssoIn.value || null,
              gog_username: gogUsernameIn.value,
              gog_user_id: gogUserIdIn.value,
              gog_access_token: gogAccessIn.value || null,
              gog_refresh_token: gogRefreshIn.value || null,
              ha_url: haUrlIn.value,
              ha_token: haTokenIn.value || null,
              ha_entity_id: haEntityIn.value,
            });
            steamKeyIn.value = "";
            xblKeyIn.value = "";
            xblWatcherKeyIn.value = "";
            psnNpssoIn.value = "";
            gogAccessIn.value = "";
            gogRefreshIn.value = "";
            haTokenIn.value = "";
            showBanner(bannerBox, "Saved encrypted connections", "info");
            await refreshData();
          } catch (err) {
            showBanner(bannerBox, err instanceof Error ? err.message : "Connection update failed", "error");
          }
        },
      }, [
        el("h3", {}, ["Steam"]),
        steamStatusLbl,
        el("label", {}, ["Steam ID / Vanity", steamIdIn]),
        el("label", {}, ["Steam Web API Key", steamKeyIn]),
        el("div", { class: "row-actions" }, [
          el("button", { type: "button", class: "btn btn-ghost", onClick: () => runProviderTest("steam", steamIdIn.value, steamKeyIn.value) }, ["Test Steam"]),
          el("button", {
            type: "button",
            class: "btn btn-ghost",
            onClick: async () => {
              try {
                await updateConnections({ clear_steam_api_key: true });
                showBanner(bannerBox, "Cleared saved personal Steam API key", "info");
                await refreshData();
              } catch (err) {
                showBanner(bannerBox, err instanceof Error ? err.message : "Failed to clear Steam key", "error");
              }
            },
          }, ["Clear Steam Key"]),
          el("button", {
            type: "button",
            class: "btn btn-secondary",
            onClick: async () => {
              try {
                const job = await enqueueSteamSync();
                showBanner(bannerBox, `Queued Steam sync job #${job.id} (${job.state})`, "info");
                await refreshData();
              } catch (err) {
                showBanner(bannerBox, err instanceof Error ? err.message : "Failed to queue Steam sync", "error");
              }
            },
          }, ["Queue Steam Sync"]),
        ]),

        el("h3", {}, ["Xbox Live / Game Pass"]),
        xboxStatusLbl,
        el("label", {}, ["Xbox Gamertag", xboxGamertagIn]),
        el("label", {}, ["Xbox XUID", xboxXuidIn]),
        el("label", {}, ["OpenXBL API Key", xblKeyIn]),
        el("label", {}, ["Xbox Telemetry Watcher Key (Optional)", xblWatcherKeyIn]),
        el("div", { class: "row-actions" }, [
          el("button", {
            type: "button",
            class: "btn btn-ghost",
            onClick: () => runProviderTest("xbox", xboxGamertagIn.value.trim() || xblKeyIn.value.trim(), xblKeyIn.value.trim()),
          }, ["Test Xbox"]),
          el("button", {
            type: "button",
            class: "btn btn-ghost",
            onClick: async () => {
              try {
                await updateConnections({ clear_xbl_api_key: true, clear_xbl_watcher_api_key: true });
                showBanner(bannerBox, "Cleared Xbox OpenXBL API keys", "info");
                await refreshData();
              } catch (err) {
                showBanner(bannerBox, err instanceof Error ? err.message : "Failed to clear Xbox keys", "error");
              }
            },
          }, ["Clear Xbox Keys"]),
          el("button", {
            type: "button",
            class: "btn btn-secondary",
            onClick: async () => {
              try {
                const job = await enqueueXboxSync();
                showBanner(bannerBox, `Queued Xbox sync job #${job.id} (${job.state})`, "info");
                await refreshData();
              } catch (err) {
                showBanner(bannerBox, err instanceof Error ? err.message : "Failed to queue Xbox sync", "error");
              }
            },
          }, ["Queue Xbox Sync"]),
        ]),

        el("h3", {}, ["PlayStation Network"]),
        psnStatusLbl,
        el("label", {}, ["PSN NPSSO Token", psnNpssoIn]),
        el("div", { class: "row-actions" }, [
          el("button", { type: "button", class: "btn btn-ghost", onClick: () => runProviderTest("psn", psnNpssoIn.value) }, ["Test PSN"]),
          el("button", {
            type: "button",
            class: "btn btn-ghost",
            onClick: async () => {
              try {
                await updateConnections({ clear_psn_npsso: true });
                showBanner(bannerBox, "Cleared PSN NPSSO token", "info");
                await refreshData();
              } catch (err) {
                showBanner(bannerBox, err instanceof Error ? err.message : "Failed to clear PSN token", "error");
              }
            },
          }, ["Clear PSN Token"]),
          el("button", {
            type: "button",
            class: "btn btn-secondary",
            onClick: async () => {
              try {
                const job = await enqueuePsnSync();
                showBanner(bannerBox, `Queued PSN sync job #${job.id} (${job.state})`, "info");
                await refreshData();
              } catch (err) {
                showBanner(bannerBox, err instanceof Error ? err.message : "Failed to queue PSN sync", "error");
              }
            },
          }, ["Queue PSN Sync"]),
        ]),

        el("h3", {}, ["GOG Galaxy"]),
        gogStatusLbl,
        el("label", {}, ["GOG Public Username", gogUsernameIn]),
        el("label", {}, ["GOG User ID", gogUserIdIn]),
        el("label", {}, ["GOG OAuth Access Token / Code URL / JSON", gogAccessIn]),
        el("label", {}, ["GOG OAuth Refresh Token", gogRefreshIn]),
        el("div", { class: "row-actions" }, [
          el("button", {
            type: "button",
            class: "btn btn-ghost",
            onClick: () => {
              const gogCred =
                gogAccessIn.value.trim() ||
                gogRefreshIn.value.trim() ||
                (gogTokenConfigured ? "" : gogUsernameIn.value.trim());
              return runProviderTest("gog", gogCred);
            },
          }, ["Test GOG"]),
          el("button", {
            type: "button",
            class: "btn btn-ghost",
            onClick: async () => {
              try {
                await updateConnections({ clear_gog_tokens: true });
                showBanner(bannerBox, "Cleared GOG Galaxy OAuth tokens", "info");
                await refreshData();
              } catch (err) {
                showBanner(bannerBox, err instanceof Error ? err.message : "Failed to clear GOG tokens", "error");
              }
            },
          }, ["Clear GOG Tokens"]),
          el("button", {
            type: "button",
            class: "btn btn-secondary",
            onClick: async () => {
              try {
                const job = await enqueueGogSync();
                showBanner(bannerBox, `Queued GOG sync job #${job.id} (${job.state})`, "info");
                await refreshData();
              } catch (err) {
                showBanner(bannerBox, err instanceof Error ? err.message : "Failed to queue GOG sync", "error");
              }
            },
          }, ["Queue GOG Sync"]),
        ]),

        el("h3", {}, ["Home Assistant"]),
        haStatusLbl,
        el("label", {}, ["Home Assistant URL", haUrlIn]),
        el("label", {}, ["Home Assistant Long-Lived Token", haTokenIn]),
        el("label", {}, ["Default Sensor Entity ID", haEntityIn]),
        el("div", { class: "row-actions" }, [
          el("button", { type: "button", class: "btn btn-ghost", onClick: () => runProviderTest("homeassistant", haUrlIn.value, haTokenIn.value) }, ["Test Home Assistant"]),
          el("button", {
            type: "button",
            class: "btn btn-ghost",
            onClick: async () => {
              try {
                await updateConnections({ clear_ha_token: true });
                showBanner(bannerBox, "Cleared Home Assistant token", "info");
                await refreshData();
              } catch (err) {
                showBanner(bannerBox, err instanceof Error ? err.message : "Failed to clear Home Assistant token", "error");
              }
            },
          }, ["Clear HA Token"]),
        ]),

        el("hr"),
        el("div", { class: "row-actions" }, [
          el("button", { type: "submit", class: "btn btn-primary" }, ["Save Connections"]),
          el("button", {
            type: "button",
            class: "btn btn-secondary",
            onClick: async () => {
              try {
                const res = await enqueueAllSyncs();
                showBanner(
                  bannerBox,
                  res.queued_count
                    ? `Queued syncs for: ${res.providers.join(", ")}`
                    : "No configured platform credentials found to sync",
                  res.queued_count ? "info" : "error"
                );
                await refreshData();
              } catch (err) {
                showBanner(bannerBox, err instanceof Error ? err.message : "Could not queue platform syncs", "error");
              }
            },
          }, ["Sync All Configured Platforms"]),
        ]),
      ]),
      el("h3", {}, ["Job Health"]),
      jobsBox,
    ]),
    el("div", { class: "panel stack" }, [
      el("h2", {}, ["Live Telemetry, Daily Sweep & Achievement Backfill"]),
      telemetryBox,
      el("div", { class: "row-actions" }, [
        el("button", {
          type: "button",
          class: "btn btn-secondary",
          onClick: async () => {
            try {
              const res = await pollTelemetry();
              showBanner(bannerBox, `Telemetry poll ${res.status}: ${res.events.length} event(s)`, res.errors.length ? "error" : "info");
              await refreshData();
            } catch (err) {
              showBanner(bannerBox, err instanceof Error ? err.message : "Telemetry poll failed", "error");
            }
          },
        }, ["Poll Live Telemetry"]),
        el("button", {
          type: "button",
          class: "btn btn-secondary",
          onClick: async () => {
            try {
              const res = await runTelemetrySweep();
              showBanner(bannerBox, `Daily sweep ${res.status}: checked ${res.titles_checked} title(s), reconciled ${res.reconciled_count} session(s)`, res.errors.length ? "error" : "info");
              await refreshData();
            } catch (err) {
              showBanner(bannerBox, err instanceof Error ? err.message : "Daily sweep failed", "error");
            }
          },
        }, ["Run Daily Sweep"]),
        el("button", {
          type: "button",
          class: "btn btn-ghost",
          onClick: async () => {
            try {
              const res = await backfillAchievementSessions();
              showBanner(bannerBox, `Achievement backfill created ${res.sessions_created} coarse session(s) across ${res.games_backfilled} game(s)`, "info");
            } catch (err) {
              showBanner(bannerBox, err instanceof Error ? err.message : "Achievement backfill failed", "error");
            }
          },
        }, ["Backfill Sessions from Achievements"]),
      ]),
    ]),
    el("div", { class: "panel" }, [
      el("h2", {}, ["Timezone & Security"]),
      el("form", {
        class: "row-actions",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          try {
            const updated = await updateTimezone(tzIn.value);
            if (onAccountUpdated) onAccountUpdated(updated);
            showBanner(bannerBox, "Timezone updated", "info");
          } catch (err) {
            showBanner(bannerBox, err instanceof Error ? err.message : "Timezone update failed", "error");
          }
        },
      }, [tzIn, el("button", { type: "submit", class: "btn btn-secondary" }, ["Save Timezone"])]),
      el("form", {
        class: "stack",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          try {
            await changePassword({ current_password: curPwIn.value, new_password: newPwIn.value });
            curPwIn.value = "";
            newPwIn.value = "";
            showBanner(bannerBox, "Password updated; other sessions & tokens revoked", "info");
          } catch (err) {
            showBanner(bannerBox, err instanceof Error ? err.message : "Password change failed", "error");
          }
        },
      }, [
        el("h3", {}, ["Change Password"]),
        el("label", {}, ["Current Password", curPwIn]),
        el("label", {}, ["New Password", newPwIn]),
        el("button", { type: "submit", class: "btn btn-secondary" }, ["Update Password"]),
      ]),
    ]),
    el("div", { class: "panel" }, [
      el("h2", {}, ["Personal Access Tokens"]),
      el("form", {
        class: "row-actions",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          try {
            const created = await createPat({
              name: patNameIn.value || "CLI Token",
              scopes: ["read:library", "write:library", "sync:platforms"],
            });
            patOut.textContent = `Copy once: ${created.token}`;
            patNameIn.value = "";
            await refreshData();
          } catch (err) {
            showBanner(bannerBox, err instanceof Error ? err.message : "Could not create PAT", "error");
          }
        },
      }, [patNameIn, el("button", { type: "submit", class: "btn btn-secondary" }, ["Create PAT"])]),
      patOut,
      patsBox,
    ]),
  ]));

  await refreshData();
}
