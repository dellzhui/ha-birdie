"use strict";

(() => {
  const app = document.getElementById("app");
  const requestId = app.dataset.requestId;
  const message = document.getElementById("message");

  const show = (text) => {
    message.textContent = text;
    message.hidden = !text;
  };

  const post = async (path, body) => {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify(body),
    });
    let data = {};
    try {
      data = await response.json();
    } catch (err) {
      // Non-JSON error page from a proxy; fall through to the generic message.
    }
    if (!response.ok) {
      throw new Error(data.message || "Something went wrong. Please try again.");
    }
    return data;
  };

  if (app.dataset.method === "dev_password") {
    document.getElementById("dev-banner").hidden = false;
  }

  document.getElementById("password-form").addEventListener("submit", async (event) => {
    event.preventDefault();
    show("");
    const form = new FormData(event.target);
    try {
      const result = await post("/oauth/session", {
        request_id: requestId,
        credential: { email: form.get("email"), password: form.get("password") },
      });
      document.getElementById("account").textContent = result.account || "";
      document.getElementById("sign-in").hidden = true;
      document.getElementById("consent").hidden = false;
    } catch (err) {
      show(err.message);
    }
  });

  for (const [id, allow] of [["allow", true], ["deny", false]]) {
    document.getElementById(id).addEventListener("click", async () => {
      show("");
      try {
        const result = await post("/oauth/consent", { request_id: requestId, allow });
        window.location.assign(result.redirect);
      } catch (err) {
        show(err.message);
      }
    });
  }
})();
