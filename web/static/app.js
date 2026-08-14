const form = document.querySelector("#redaction-form");
const fileInput = document.querySelector("#document");
const dropZone = document.querySelector("#drop-zone");
const fileTitle = document.querySelector("#file-title");
const fileDetail = document.querySelector("#file-detail");
const submitButton = document.querySelector("#submit-button");
const statusPanel = document.querySelector("#status-panel");
const statusTitle = document.querySelector("#status-title");
const statusMessage = document.querySelector("#status-message");
const downloadLink = document.querySelector("#download-link");

const maxFileBytes = 25 * 1024 * 1024;
let selectedFile = null;
let downloadUrl = null;

function formatBytes(bytes) {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function showError(message) {
  statusPanel.hidden = false;
  statusPanel.className = "status-panel error";
  statusTitle.textContent = "Could not redact this document";
  statusMessage.textContent = message;
  downloadLink.hidden = true;
}

function selectFile(file) {
  if (!file) return;
  if (!file.name.toLowerCase().endsWith(".docx")) {
    selectedFile = null;
    submitButton.disabled = true;
    showError("Choose a Microsoft Word .docx file.");
    return;
  }
  if (file.size > maxFileBytes) {
    selectedFile = null;
    submitButton.disabled = true;
    showError("That file is larger than the 25 MB upload limit.");
    return;
  }
  selectedFile = file;
  fileTitle.textContent = file.name;
  fileDetail.textContent = `${formatBytes(file.size)} · ready to redact`;
  submitButton.disabled = false;
  statusPanel.hidden = true;
}

dropZone.addEventListener("click", () => fileInput.click());
dropZone.addEventListener("keydown", (event) => {
  if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    fileInput.click();
  }
});
fileInput.addEventListener("change", () => selectFile(fileInput.files[0]));

for (const eventName of ["dragenter", "dragover"]) {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.add("dragging");
  });
}
for (const eventName of ["dragleave", "drop"]) {
  dropZone.addEventListener(eventName, (event) => {
    event.preventDefault();
    dropZone.classList.remove("dragging");
  });
}
dropZone.addEventListener("drop", (event) => selectFile(event.dataTransfer.files[0]));

function responseFilename(header) {
  if (!header) return "redacted.docx";
  const encoded = header.match(/filename\*=utf-8''([^;]+)/i);
  if (encoded) return decodeURIComponent(encoded[1]);
  const quoted = header.match(/filename="([^"]+)"/i);
  return quoted ? quoted[1] : "redacted.docx";
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!selectedFile) return showError("Choose a DOCX file first.");

  submitButton.disabled = true;
  statusPanel.hidden = false;
  statusPanel.className = "status-panel";
  statusTitle.textContent = "Processing document…";
  statusMessage.textContent = "Checking text, tables, fields, and embedded media. Large files can take a few minutes.";
  downloadLink.hidden = true;

  const body = new FormData();
  body.append("document", selectedFile, selectedFile.name);
  body.append("company_scope", document.querySelector("#company-scope").value);
  body.append("image_policy", document.querySelector("#image-policy").value);
  body.append("use_ner", String(document.querySelector("#use-ner").checked));
  body.append("seed", document.querySelector("#seed").value || "42");

  try {
    const response = await fetch("/api/redact", { method: "POST", body });
    if (!response.ok) {
      let message = `The server returned ${response.status}.`;
      try {
        const error = await response.json();
        if (error.detail) message = error.detail;
      } catch (_) {
        // The fallback status message is sufficient for a non-JSON error.
      }
      throw new Error(message);
    }

    const blob = await response.blob();
    if (downloadUrl) URL.revokeObjectURL(downloadUrl);
    downloadUrl = URL.createObjectURL(blob);
    const filename = responseFilename(response.headers.get("content-disposition"));
    downloadLink.href = downloadUrl;
    downloadLink.download = filename;
    downloadLink.hidden = false;

    const textCount = Number(response.headers.get("x-redaction-count") || 0);
    const imageCount = Number(response.headers.get("x-image-redaction-count") || 0);
    const alreadyRedacted = response.headers.get("x-already-redacted") === "true";
    const model = response.headers.get("x-ner-model") || "disabled";
    statusPanel.className = "status-panel done";
    statusTitle.textContent = alreadyRedacted ? "Document was already redacted" : "Redaction complete";
    statusMessage.textContent = alreadyRedacted
      ? "The existing redacted document was returned unchanged."
      : `${textCount} text span${textCount === 1 ? "" : "s"} and ${imageCount} image${imageCount === 1 ? "" : "s"} redacted · NER: ${model}.`;
    downloadLink.click();
  } catch (error) {
    showError(error.message || "The request failed. Please try again.");
  } finally {
    submitButton.disabled = false;
  }
});
