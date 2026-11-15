import type { UserAccountResponse } from "../../shared/api-types.ts";
import {
  changePassword,
  createPat,
  enqueueSteamSync,
  getConnections,
  getJobs,
  listPats,
  revokePat,
  updateConnections,
  updateTimezone,
} from "../../shared/api.ts";
import { clearEl, el, showBanner } from "../../shared/dom.ts";

export async function renderSettingsView(container: HTMLElement, account: UserAccountResponse): Promise<void> {
  clearEl(container);
  const bannerBox = el("div");
  const jobsBox = el("div", { class: "stack" });
  const patsBox = el("div", { class: "stack" });

  const tzIn = el("input", { type: "text", value: account.timezone || "auto" }) as HTMLInputElement;
  const steamIdIn = el("input", { type: "text", placeholder: "SteamID64 or vanity" }) as HTMLInputElement;
  const steamKeyIn = el("input", { type: "password", placeholder: "Optional Steam Web API key (encrypted)" }) as HTMLInputElement;

  const curPwIn = el("input", { type: "password", required: true }) as HTMLInputElement;
  const newPwIn = el("input", { type: "password", required: true, minlength: "8" }) as HTMLInputElement;

  const patNameIn = el("input", { type: "text", placeholder: "Home Assistant / MCP script" }) as HTMLInputElement;
  const patOut = el("pre", { class: "code-out" });

  async function refreshData(): Promise<void> {
    const [conns, jobsData, pats] = await Promise.all([getConnections(), getJobs(), listPats()]);
    steamIdIn.value = conns.steam_id || "";
    if (conns.credential_decryption_error) {
      showBanner(bannerBox, conns.credential_decryption_error, "error");
    }

    clearEl(jobsBox);
    for (const p of jobsData.providers) {
      jobsBox.appendChild(el("div", { class: "stat-card" }, [
        el("strong", {}, [p.provider.toUpperCase()]),
        el("span", { class: "muted" }, [`State: ${p.active_state || "idle"}${p.credential_expired ? " (CREDENTIALS EXPIRED)" : ""}`]),
        p.last_success_at ? el("span", { class: "muted" }, [`Last success: ${p.last_success_at}`]) : null,
        p.last_error ? el("span", { class: "error-text" }, [`Last error: ${p.last_error}`]) : null,
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
            await revokePat(t.id);
            await refreshData();
          },
        }, ["Revoke"]),
      ]));
    }
  }

  container.appendChild(el("section", { class: "stack" }, [
    bannerBox,
    el("div", { class: "panel" }, [
      el("h2", {}, ["Connections & Steam Sync"]),
      el("form", {
        class: "stack",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          try {
            await updateConnections({
              steam_id: steamIdIn.value,
              steam_api_key: steamKeyIn.value || null,
            });
            steamKeyIn.value = "";
            showBanner(bannerBox, "Saved encrypted connections", "info");
            await refreshData();
          } catch (err) {
            showBanner(bannerBox, err instanceof Error ? err.message : "Connection update failed", "error");
          }
        },
      }, [
        el("label", {}, ["Steam ID / Vanity", steamIdIn]),
        el("label", {}, ["Steam Web API Key", steamKeyIn]),
        el("div", { class: "row-actions" }, [
          el("button", { type: "submit", class: "btn btn-primary" }, ["Save Connections"]),
          el("button", {
            type: "button",
            class: "btn btn-secondary",
            onClick: async () => {
              const job = await enqueueSteamSync();
              showBanner(bannerBox, `Queued Steam sync job #${job.id} (${job.state})`, "info");
              await refreshData();
            },
          }, ["Queue Steam Sync"]),
        ]),
      ]),
      el("h3", {}, ["Job Health"]),
      jobsBox,
    ]),
    el("div", { class: "panel" }, [
      el("h2", {}, ["Timezone & Security"]),
      el("form", {
        class: "row-actions",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          await updateTimezone(tzIn.value);
          showBanner(bannerBox, "Timezone updated", "info");
        },
      }, [tzIn, el("button", { type: "submit", class: "btn btn-secondary" }, ["Save Timezone"])]),
      el("form", {
        class: "stack",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          await changePassword({ current_password: curPwIn.value, new_password: newPwIn.value });
          curPwIn.value = "";
          newPwIn.value = "";
          showBanner(bannerBox, "Password updated; other sessions & tokens revoked", "info");
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
          const created = await createPat({
            name: patNameIn.value || "CLI Token",
            scopes: ["read:library", "write:library", "sync:platforms"],
          });
          patOut.textContent = `Copy once: ${created.token}`;
          patNameIn.value = "";
          await refreshData();
        },
      }, [patNameIn, el("button", { type: "submit", class: "btn btn-secondary" }, ["Create PAT"])]),
      patOut,
      patsBox,
    ]),
  ]));

  await refreshData();
}
