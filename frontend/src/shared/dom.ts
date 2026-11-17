export type ChildItem = HTMLElement | Text | string | null | undefined;

export function clearEl(node: HTMLElement): void {
  while (node.firstChild) {
    node.removeChild(node.firstChild);
  }
}

export function el(tag: string, attrs: Record<string, string | boolean | (() => void) | ((ev: Event) => void)> = {}, children: Array<ChildItem> = []): HTMLElement {
  const node = document.createElement(tag);
  for (const [key, val] of Object.entries(attrs)) {
    if (key.startsWith("on") && typeof val === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), val as EventListener);
    } else if (typeof val === "boolean") {
      if (val) {
        node.setAttribute(key, "");
      }
    } else if (val !== undefined && val !== null) {
      node.setAttribute(key, String(val));
    }
  }
  for (const child of children) {
    if (child === null || child === undefined) continue;
    if (typeof child === "string") {
      node.appendChild(document.createTextNode(child));
    } else {
      node.appendChild(child);
    }
  }
  return node;
}

export function openModalDialog(titleText: string, buildBody: (dialog: HTMLDialogElement, body: HTMLElement) => void): HTMLDialogElement {
  const headingId = `dialog-title-${Date.now()}-${Math.floor(Math.random() * 10000)}`;
  const body = el("div", { class: "dialog-body" });
  const heading = el("h2", { id: headingId, class: "dialog-title" }, [titleText]);
  const dialog = el("dialog", { class: "vtx-dialog", "aria-labelledby": headingId }) as HTMLDialogElement;
  const closeBtn = el("button", { type: "button", class: "btn btn-ghost", onClick: () => dialog.close("cancel") }, ["Close"]);
  const header = el("div", { class: "dialog-header" }, [heading, closeBtn]);

  dialog.appendChild(header);
  dialog.appendChild(body);
  buildBody(dialog, body);

  dialog.addEventListener("close", () => {
    dialog.remove();
  });
  document.body.appendChild(dialog);
  dialog.showModal();
  return dialog;
}

export function showBanner(container: HTMLElement, message: string, kind: string = "info"): void {
  clearEl(container);
  if (!message) return;
  container.appendChild(el("div", { class: `banner banner-${kind}`, role: "status" }, [message]));
}
