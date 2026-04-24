/*
 * contribute-stub.js
 *
 * Stub handler for /position/contribute/.
 * Collects form → JSON → shows confirmation → writes to console.
 * Does NOT transmit anywhere.
 *
 * When the Worker at contribute.abalonecove.org is live, replace the
 * body of submit() with a real fetch() to data-action and an age
 * encryption step (either via a WASM age build in the browser or by
 * shipping the ciphertext produced server-side).
 *
 * No third-party scripts. No cookies. No analytics. No IP logs.
 */
(function () {
  "use strict";

  var form = document.getElementById("contribute-form");
  if (!form) return;

  var status = document.getElementById("form-status");

  form.addEventListener("submit", function (ev) {
    ev.preventDefault();

    var kind = (form.querySelector('input[name="kind"]:checked') || {}).value || "";
    var body = (form.querySelector('textarea[name="body"]') || {}).value || "";
    var url  = (form.querySelector('input[name="url"]') || {}).value || "";
    var ct   = (form.querySelector('input[name="contact_optional"]') || {}).value || "";

    if (!kind || !body.trim()) {
      show("Need a category and a body. Try again.");
      return;
    }

    var payload = {
      kind: kind,
      body: body.trim(),
      url: url.trim() || undefined,
      contact_optional: ct.trim() || undefined,
      client_ts: new Date().toISOString(),
    };

    /* eslint-disable no-console */
    console.log("[abalonecove:contribute stub]", JSON.parse(JSON.stringify(payload)));
    /* eslint-enable no-console */

    show(
      "Submission received — form backend not yet deployed.\n" +
      "Nothing was transmitted. When the backend is live, your submission " +
      "will be encrypted in your browser before transmission. Thank you for " +
      "contributing."
    );

    form.reset();
  });

  function show(msg) {
    if (!status) return;
    status.textContent = msg;
    status.classList.add("visible");
  }
})();
