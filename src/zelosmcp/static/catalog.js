  const filterInput = document.getElementById("filter");
  const root = document.getElementById("root");
  let catalog = {};

  function el(tag, props, ...children) {
    const node = document.createElement(tag);
    if (props) for (const [k, v] of Object.entries(props)) {
      if (k === "class") node.className = v;
      else if (k === "html") node.innerHTML = v;
      else node.setAttribute(k, v);
    }
    for (const c of children) {
      if (c == null) continue;
      node.appendChild(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return node;
  }

  function renderItem(kind, item) {
    const li = el("li", { class: "item", "data-name": (item.name || item.uri || "") });
    const head = el("div", null, el("code", null, item.name || item.uri || "(unnamed)"));
    li.appendChild(head);
    if (item.description) li.appendChild(el("div", { class: "desc" }, item.description));
    if (kind === "tools" && item.inputSchema) {
      const det = el("details");
      det.appendChild(el("summary", null, "schema"));
      det.appendChild(el("pre", null, JSON.stringify(item.inputSchema, null, 2)));
      li.appendChild(det);
    }
    if (kind === "prompts" && Array.isArray(item.arguments) && item.arguments.length) {
      const args = item.arguments.map(a =>
        a.name + (a.required ? "" : "?") + (a.description ? `: ${a.description}` : "")
      ).join(" • ");
      li.appendChild(el("div", { class: "args" }, args));
    }
    if (kind === "resources" || kind === "resourceTemplates") {
      const meta = [];
      if (item.mimeType) meta.push(`mime: ${item.mimeType}`);
      if (item.uriTemplate) meta.push(`template: ${item.uriTemplate}`);
      if (meta.length) li.appendChild(el("div", { class: "args" }, meta.join(" • ")));
    }
    return li;
  }

  const KINDS = [
    ["tools", "Tools"],
    ["prompts", "Prompts"],
    ["resources", "Resources"],
    ["resourceTemplates", "Resource templates"],
  ];

  function renderCatalog(filterText) {
    root.innerHTML = "";
    const needle = (filterText || "").trim().toLowerCase();
    const names = Object.keys(catalog);
    if (names.length === 0) {
      root.appendChild(el("p", { class: "empty" }, "No backends are currently running."));
      return;
    }
    let totalShown = 0;
    for (const name of names) {
      const block = el("div", { class: "server-block" });
      const transport = catalog[name].transport || "unknown";
      block.appendChild(el("div", { class: "server-head" },
        el("h2", null, name),
        el("span", { class: "meta" }, `(${transport})`),
      ));
      let blockShown = 0;
      for (const [kind, label] of KINDS) {
        const raw = catalog[name][kind];
        if (Array.isArray(raw)) {
          const items = needle
            ? raw.filter(it => ((it.name || it.uri || "") + " " + (it.description || ""))
                .toLowerCase().includes(needle))
            : raw;
          if (raw.length === 0) {
            const group = el("div", { class: "group" },
              el("h3", null, `${label} (0)`),
              el("p", { class: "empty" }, "(none)"),
            );
            if (!needle) block.appendChild(group);
          } else if (items.length > 0) {
            const ul = el("ul");
            for (const item of items) ul.appendChild(renderItem(kind, item));
            block.appendChild(el("div", { class: "group" },
              el("h3", null, `${label} (${items.length}${items.length !== raw.length ? ` of ${raw.length}` : ""})`),
              ul,
            ));
            blockShown += items.length;
          }
        } else if (raw && raw.error) {
          block.appendChild(el("div", { class: "group" },
            el("h3", null, label),
            el("p", { class: "empty" }, `Error: ${raw.error}`),
          ));
        }
      }
      // Hide whole block if filter matched nothing in it.
      if (!needle || blockShown > 0) {
        root.appendChild(block);
        totalShown += blockShown;
      }
    }
    if (needle && totalShown === 0) {
      root.appendChild(el("p", { class: "empty" }, `No matches for "${filterText}".`));
    }
  }

  filterInput.addEventListener("input", () => renderCatalog(filterInput.value));

  fetch("/api/catalog")
    .then(r => r.json())
    .then(data => { catalog = data; renderCatalog(""); })
    .catch(err => {
      root.innerHTML = "";
      root.appendChild(el("div", { class: "error-banner" }, `Failed to load /api/catalog: ${err.message}`));
    });
