import type { UserAccountResponse } from "../../shared/api-types.ts";
import { acceptInvite, bootstrapAdmin, getBootstrapStatus, login } from "../../shared/api.ts";
import { clearEl, el, showBanner } from "../../shared/dom.ts";

export async function renderAuthView(container: HTMLElement, onAuthenticated: (account: UserAccountResponse) => void): Promise<void> {
  clearEl(container);
  const bannerBox = el("div");
  const card = el("section", { class: "auth-card" }, [
    el("h1", {}, ["Vertex"]),
    el("p", { class: "muted" }, ["Private self-hosted game library"]),
    bannerBox,
  ]);
  container.appendChild(card);

  let bootstrapOpen = false;
  try {
    const status = await getBootstrapStatus();
    bootstrapOpen = status.bootstrap_open;
  } catch (err) {
    const msg = err instanceof Error ? err.message : "Could not connect to server";
    showBanner(bannerBox, msg, "error");
    return;
  }

  if (bootstrapOpen) {
    const userIn = el("input", { type: "text", required: true, minlength: "2", maxlength: "32", autocomplete: "username" }) as HTMLInputElement;
    const passIn = el("input", { type: "password", required: true, minlength: "8", maxlength: "128", autocomplete: "new-password" }) as HTMLInputElement;
    const form = el("form", {
      class: "stack",
      onSubmit: async (ev: Event) => {
        ev.preventDefault();
        try {
          const res = await bootstrapAdmin({ username: userIn.value, password: passIn.value });
          onAuthenticated(res.account);
        } catch (err) {
          showBanner(bannerBox, err instanceof Error ? err.message : "Bootstrap failed", "error");
        }
      },
    }, [
      el("h2", {}, ["Initial Administrator Setup"]),
      el("label", {}, ["Username", userIn]),
      el("label", {}, ["Password (min 8 chars)", passIn]),
      el("button", { type: "submit", class: "btn btn-primary" }, ["Create Administrator & Enter"]),
    ]);
    card.appendChild(form);
    return;
  }

  let mode: string = "login";
  const formHost = el("div");

  function drawForm(): void {
    clearEl(formHost);
    const userIn = el("input", { type: "text", required: true, autocomplete: "username" }) as HTMLInputElement;
    const passIn = el("input", { type: "password", required: true, autocomplete: mode === "login" ? "current-password" : "new-password" }) as HTMLInputElement;
    const codeIn = el("input", { type: "text", required: true, placeholder: "vtxi_..." }) as HTMLInputElement;

    const fields: Array<HTMLElement> = mode === "invite"
      ? [
          el("label", {}, ["Invite Code", codeIn]),
          el("label", {}, ["Username", userIn]),
          el("label", {}, ["Password", passIn]),
        ]
      : [
          el("label", {}, ["Username", userIn]),
          el("label", {}, ["Password", passIn]),
        ];

    const submitLabel = mode === "invite" ? "Redeem Invite" : "Sign In";
    const toggleLabel = mode === "invite" ? "Back to Sign In" : "Have an invite code?";

    const form = el("form", {
      class: "stack",
      onSubmit: async (ev: Event) => {
        ev.preventDefault();
        try {
          const res = mode === "invite"
            ? await acceptInvite({ invite_code: codeIn.value, username: userIn.value, password: passIn.value })
            : await login({ username: userIn.value, password: passIn.value });
          onAuthenticated(res.account);
        } catch (err) {
          showBanner(bannerBox, err instanceof Error ? err.message : "Authentication failed", "error");
        }
      },
    }, [
      ...fields,
      el("div", { class: "row-actions" }, [
        el("button", { type: "submit", class: "btn btn-primary" }, [submitLabel]),
        el("button", {
          type: "button",
          class: "btn btn-ghost",
          onClick: () => {
            mode = mode === "login" ? "invite" : "login";
            showBanner(bannerBox, "");
            drawForm();
          },
        }, [toggleLabel]),
      ]),
    ]);
    formHost.appendChild(form);
  }

  card.appendChild(formHost);
  drawForm();
}
