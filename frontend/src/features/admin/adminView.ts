import type { UserAccountResponse } from "../../shared/api-types.ts";
import {
  adminCreateInvite,
  adminCreateUser,
  adminDeleteUser,
  adminListInvites,
  adminListUsers,
  adminRevokeInvite,
  adminUpdateUser,
} from "../../shared/api.ts";
import { clearEl, el, showBanner } from "../../shared/dom.ts";

export async function renderAdminView(container: HTMLElement, currentUser: UserAccountResponse): Promise<void> {
  clearEl(container);
  const bannerBox = el("div");
  const usersList = el("div", { class: "stack" });
  const invitesList = el("div", { class: "stack" });
  const inviteOut = el("pre", { class: "code-out" });

  const newUsername = el("input", { type: "text", required: true, placeholder: "household-member" }) as HTMLInputElement;
  const newPass = el("input", { type: "password", required: true, minlength: "8", placeholder: "Temporary password" }) as HTMLInputElement;
  const inviteNote = el("input", { type: "text", placeholder: "Invite note (e.g. Alex)" }) as HTMLInputElement;

  async function refresh(): Promise<void> {
    const [users, invites] = await Promise.all([adminListUsers(), adminListInvites()]);
    clearEl(usersList);
    for (const u of users) {
      const toggleStatus = u.status === "active" ? "suspended" : "active";
      usersList.appendChild(el("div", { class: "row-between" }, [
        el("span", {}, [`${u.username} (${u.role}, ${u.status})${u.status_reason ? ` - ${u.status_reason}` : ""}`]),
        u.id === currentUser.id ? el("span", { class: "muted" }, ["(You)"]) : el("div", { class: "row-actions" }, [
          el("button", {
            type: "button",
            class: "btn btn-secondary",
            onClick: async () => {
              try {
                await adminUpdateUser(u.id, { status: toggleStatus });
                await refresh();
              } catch (err) {
                showBanner(bannerBox, err instanceof Error ? err.message : "Update failed", "error");
              }
            },
          }, [u.status === "active" ? "Suspend" : "Activate"]),
          el("button", {
            type: "button",
            class: "btn btn-ghost",
            onClick: async () => {
              try {
                await adminDeleteUser(u.id);
                await refresh();
              } catch (err) {
                showBanner(bannerBox, err instanceof Error ? err.message : "Delete failed", "error");
              }
            },
          }, ["Delete"]),
        ]),
      ]));
    }

    clearEl(invitesList);
    for (const inv of invites) {
      invitesList.appendChild(el("div", { class: "row-between" }, [
        el("span", {}, [`Role: ${inv.role} | Note: ${inv.note || "-"} | ${inv.used_by ? `Used by ${inv.used_by}` : inv.revoked_at ? "Revoked" : `Expires ${inv.expires_at}`}`]),
        inv.used_by || inv.revoked_at ? null : el("button", {
          type: "button",
          class: "btn btn-ghost",
          onClick: async () => {
            await adminRevokeInvite(inv.code_hash);
            await refresh();
          },
        }, ["Revoke"]),
      ]));
    }
  }

  container.appendChild(el("section", { class: "stack" }, [
    bannerBox,
    el("div", { class: "panel" }, [
      el("h2", {}, ["Single-Use Household Invitations"]),
      el("form", {
        class: "row-actions",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          const created = await adminCreateInvite({ role: "member", note: inviteNote.value, ttl_hours: 72 });
          inviteOut.textContent = `One-time invite code: ${created.code}`;
          inviteNote.value = "";
          await refresh();
        },
      }, [inviteNote, el("button", { type: "submit", class: "btn btn-primary" }, ["Create Invite"])]),
      inviteOut,
      invitesList,
    ]),
    el("div", { class: "panel" }, [
      el("h2", {}, ["Accounts"]),
      el("form", {
        class: "row-actions",
        onSubmit: async (ev: Event) => {
          ev.preventDefault();
          try {
            await adminCreateUser({ username: newUsername.value, password: newPass.value, role: "member" });
            newUsername.value = "";
            newPass.value = "";
            await refresh();
          } catch (err) {
            showBanner(bannerBox, err instanceof Error ? err.message : "Could not create user", "error");
          }
        },
      }, [newUsername, newPass, el("button", { type: "submit", class: "btn btn-secondary" }, ["Create Account"])]),
      usersList,
    ]),
  ]));

  await refresh();
}
