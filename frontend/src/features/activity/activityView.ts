import type { PlaySessionResponse, PlaytimePromptResponse } from "../../shared/api-types.ts";
import {
  deleteSession,
  getDailyLedger,
  importHomeAssistantCsv,
  ingestHomeAssistantEvent,
  listPlaytimePrompts,
  listSessions,
  rebuildDailyLedger,
  resolvePlaytimePrompt,
  updateSession,
} from "../../shared/api.ts";
import { clearEl, el, openModalDialog, showBanner } from "../../shared/dom.ts";

export async function renderActivityView(container: HTMLElement): Promise<void> {
  clearEl(container);
  const bannerBox = el("div");
  const promptsBox = el("div", { class: "stack" });
  const sessionsBox = el("div", { class: "table-scroll" });
  const ledgerBox = el("div", { class: "table-scroll" });

  function drawPrompts(prompts: Array<PlaytimePromptResponse>): void {
    clearEl(promptsBox);
    if (!prompts.length) {
      promptsBox.appendChild(el("p", { class: "muted" }, ["No pending playtime prompts."]));
      return;
    }
    for (const p of prompts) {
      const msg = `${p.title} (${p.platform}) recorded +${p.delta_hours}h while marked "${p.current_status}" (${p.previous_hours}h -> ${p.new_hours}h).`;
      promptsBox.appendChild(el("div", { class: "prompt-card row-between" }, [
        el("div", { class: "stack" }, [
          el("strong", {}, [p.title]),
          el("span", { class: "muted" }, [msg]),
        ]),
        el("div", { class: "row-actions" }, [
          el("button", { type: "button", class: "btn btn-primary", onClick: () => handlePrompt(p.id, "accept") }, ["Set Playing"]),
          el("button", { type: "button", class: "btn btn-secondary", onClick: () => handlePrompt(p.id, "dismiss") }, ["Dismiss"]),
          el("button", { type: "button", class: "btn btn-ghost", onClick: () => handlePrompt(p.id, "mute") }, ["Mute Game"]),
        ]),
      ]));
    }
  }

  async function handlePrompt(promptId: number, action: string): Promise<void> {
    try {
      const res = await resolvePlaytimePrompt(promptId, action);
      showBanner(bannerBox, `Prompt ${res.action}: status is ${res.completion_status}`, "info");
      await refresh();
    } catch (err) {
      showBanner(bannerBox, err instanceof Error ? err.message : "Failed resolving prompt", "error");
    }
  }

  function openEditSessionModal(s: PlaySessionResponse): void {
    openModalDialog(`Edit Session #${s.id}`, (dialog, body) => {
      const modalBanner = el("div");
      const startIn = el("input", { type: "text", required: true, value: s.started_at }) as HTMLInputElement;
      const endIn = el("input", { type: "text", required: true, value: s.ended_at }) as HTMLInputElement;
      const durIn = el("input", { type: "number", min: "1", required: true, value: String(s.duration_minutes) }) as HTMLInputElement;
      const notesIn = el("textarea", { rows: "3" }, [s.notes || ""]) as HTMLTextAreaElement;
      const lockIn = el("input", { type: "checkbox" }) as HTMLInputElement;
      lockIn.checked = Boolean(s.is_locked);

      const form = el("form", {
        class: "stack",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          showBanner(modalBanner, "");
          try {
            await updateSession(s.id, {
              started_at: startIn.value.trim(),
              ended_at: endIn.value.trim(),
              duration_minutes: Number(durIn.value || 1),
              notes: notesIn.value,
              is_locked: lockIn.checked,
            });
            dialog.close("saved");
            await refresh();
          } catch (err) {
            showBanner(modalBanner, err instanceof Error ? err.message : "Failed updating session", "error");
          }
        },
      }, [
        modalBanner,
        el("label", {}, ["Started At (ISO UTC)", startIn]),
        el("label", {}, ["Ended At (ISO UTC)", endIn]),
        el("label", {}, ["Duration (minutes)", durIn]),
        el("label", { class: "row-check" }, [lockIn, "Lock session against automated sync coalescing"]),
        el("label", {}, ["Notes", notesIn]),
        el("div", { class: "row-actions" }, [
          el("button", { type: "submit", class: "btn btn-primary" }, ["Save"]),
          el("button", { type: "button", class: "btn btn-ghost", onClick: () => dialog.close("cancel") }, ["Cancel"]),
        ]),
      ]);
      body.appendChild(form);
    });
  }

  function drawSessions(sessions: Array<PlaySessionResponse>): void {
    clearEl(sessionsBox);
    if (!sessions.length) {
      sessionsBox.appendChild(el("p", { class: "muted" }, ["No play sessions recorded yet."]));
      return;
    }
    const tbody = el("tbody");
    for (const s of sessions) {
      tbody.appendChild(el("tr", {}, [
        el("td", {}, [String(s.game_id)]),
        el("td", {}, [s.platform]),
        el("td", {}, [s.started_at.slice(0, 16).replace("T", " ")]),
        el("td", {}, [`${s.duration_minutes}m`]),
        el("td", {}, [`${s.source} (${s.precision_level})${s.is_locked ? " [Locked]" : ""}`]),
        el("td", {}, [s.notes || ""]),
        el("td", { class: "row-actions" }, [
          el("button", { type: "button", class: "btn btn-secondary", onClick: () => openEditSessionModal(s) }, ["Edit"]),
          el("button", {
            type: "button",
            class: "btn btn-ghost",
            onClick: async () => {
              try {
                await deleteSession(s.id);
                await refresh();
              } catch (err) {
                showBanner(bannerBox, err instanceof Error ? err.message : "Could not delete session", "error");
              }
            },
          }, ["Delete"]),
        ]),
      ]));
    }
    sessionsBox.appendChild(el("table", { class: "data-table" }, [
      el("caption", { class: "sr-only" }, ["Recent Play Sessions"]),
      el("thead", {}, [
        el("tr", {}, [
          el("th", { scope: "col" }, ["Game ID"]),
          el("th", { scope: "col" }, ["Platform"]),
          el("th", { scope: "col" }, ["Started"]),
          el("th", { scope: "col" }, ["Duration"]),
          el("th", { scope: "col" }, ["Provenance"]),
          el("th", { scope: "col" }, ["Notes"]),
          el("th", { scope: "col" }, ["Actions"]),
        ]),
      ]),
      tbody,
    ]));
  }

  function drawLedger(rows: Array<Record<string, unknown>>): void {
    clearEl(ledgerBox);
    if (!rows.length) {
      ledgerBox.appendChild(el("p", { class: "muted" }, ["Daily playtime ledger is empty."]));
      return;
    }
    const tbody = el("tbody");
    for (const r of rows.slice(0, 50)) {
      tbody.appendChild(el("tr", {}, [
        el("td", {}, [String(r.date || "")]),
        el("td", {}, [String(r.game_id || "")]),
        el("td", {}, [String(r.platform_family || "")]),
        el("td", {}, [`${Number(r.minutes_played || 0)}m`]),
        el("td", {}, [String(r.sessions_count || 0)]),
      ]));
    }
    ledgerBox.appendChild(el("table", { class: "data-table" }, [
      el("caption", { class: "sr-only" }, ["Daily Playtime Ledger"]),
      el("thead", {}, [
        el("tr", {}, [
          el("th", { scope: "col" }, ["Date"]),
          el("th", { scope: "col" }, ["Game ID"]),
          el("th", { scope: "col" }, ["Platform Family"]),
          el("th", { scope: "col" }, ["Minutes"]),
          el("th", { scope: "col" }, ["Sessions"]),
        ]),
      ]),
      tbody,
    ]));
  }

  async function refresh(): Promise<void> {
    try {
      const [prompts, sessions, ledger] = await Promise.all([
        listPlaytimePrompts(),
        listSessions(undefined, 50),
        getDailyLedger(),
      ]);
      drawPrompts(prompts);
      drawSessions(sessions);
      drawLedger(ledger);
    } catch (err) {
      showBanner(bannerBox, err instanceof Error ? err.message : "Failed loading activity data", "error");
    }
  }

  const evtTitle = el("input", { type: "text", required: true, placeholder: "Halo Infinite" }) as HTMLInputElement;
  const evtPlatform = el("input", { type: "text", value: "PC (Steam)" }) as HTMLInputElement;
  const evtKind = el("select", {}, [
    el("option", { value: "record" }, ["record (exact duration)"]),
    el("option", { value: "start" }, ["start"]),
    el("option", { value: "stop" }, ["stop"]),
  ]) as HTMLSelectElement;
  const evtMins = el("input", { type: "number", min: "1", value: "45" }) as HTMLInputElement;
  const evtNotes = el("input", { type: "text", placeholder: "Optional session note" }) as HTMLInputElement;

  const csvPlatform = el("input", { type: "text", value: "Xbox" }) as HTMLInputElement;
  const csvArea = el("textarea", {
    rows: "4",
    placeholder: "entity_id,state,last_changed\nsensor.xbox_game,Halo Infinite,2026-10-01T18:00:00Z\nsensor.xbox_game,off,2026-10-01T19:00:00Z",
  }) as HTMLTextAreaElement;

  container.appendChild(el("section", { class: "stack" }, [
    el("h1", {}, ["Activity & Sessions"]),
    bannerBox,
    el("div", { class: "panel stack" }, [
      el("h2", {}, ["Pending Playtime Prompts"]),
      promptsBox,
    ]),
    el("div", { class: "panel stack" }, [
      el("h2", {}, ["Log Session or Home Assistant Event"]),
      el("form", {
        class: "toolbar",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          showBanner(bannerBox, "");
          try {
            const kind = evtKind.value;
            const res = await ingestHomeAssistantEvent({
              event: kind,
              title: evtTitle.value.trim(),
              platform: evtPlatform.value.trim() || "PC",
              duration_minutes: kind === "start" ? null : Number(evtMins.value || 1),
              notes: evtNotes.value.trim() || null,
            });
            showBanner(bannerBox, `Event recorded: ${res.status} (${res.duration_minutes}m)`, "info");
            evtTitle.value = "";
            evtNotes.value = "";
            await refresh();
          } catch (err) {
            showBanner(bannerBox, err instanceof Error ? err.message : "Failed recording event", "error");
          }
        },
      }, [
        evtTitle,
        evtPlatform,
        evtKind,
        evtMins,
        evtNotes,
        el("button", { type: "submit", class: "btn btn-primary" }, ["Record Event"]),
      ]),
    ]),
    el("div", { class: "panel stack" }, [
      el("h2", {}, ["Import Home Assistant History CSV"]),
      el("form", {
        class: "stack",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          showBanner(bannerBox, "");
          try {
            const rep = await importHomeAssistantCsv({
              csv_content: csvArea.value,
              platform: csvPlatform.value.trim() || "Xbox",
            });
            showBanner(bannerBox, `HA CSV import: ${rep.imported_count} imported, ${rep.skipped_count} skipped`, "info");
            await refresh();
          } catch (err) {
            showBanner(bannerBox, err instanceof Error ? err.message : "CSV import failed", "error");
          }
        },
      }, [
        el("label", {}, ["Target Platform", csvPlatform]),
        el("label", {}, ["Home Assistant CSV Export", csvArea]),
        el("div", { class: "row-actions" }, [
          el("button", { type: "submit", class: "btn btn-secondary" }, ["Import CSV Spans"]),
        ]),
      ]),
    ]),
    el("div", { class: "panel stack" }, [
      el("h2", {}, ["Recent Sessions"]),
      sessionsBox,
    ]),
    el("div", { class: "panel stack" }, [
      el("div", { class: "row-between" }, [
        el("h2", {}, ["Daily Playtime Ledger"]),
        el("button", {
          type: "button",
          class: "btn btn-secondary",
          onClick: async () => {
            try {
              const rows = await rebuildDailyLedger();
              drawLedger(rows);
              showBanner(bannerBox, `Daily playtime ledger rebuilt (${rows.length} entries)`, "info");
            } catch (err) {
              showBanner(bannerBox, err instanceof Error ? err.message : "Failed rebuilding ledger", "error");
            }
          },
        }, ["Rebuild Ledger"]),
      ]),
      ledgerBox,
    ]),
  ]));

  await refresh();
}
