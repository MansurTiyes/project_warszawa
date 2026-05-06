/**
 * Frontend PDF validation — three checks before we POST to /api/stars.
 *
 * Order matters:
 *   1. MIME or extension is PDF (older Safari sometimes drops the MIME).
 *   2. Size <= 5 MB (matches the upload-screen copy).
 *   3. First 4 bytes equal "%PDF" (catches a .txt renamed to .pdf).
 *
 * Async because the magic-byte sniff reads from the File via ArrayBuffer.
 */

export type ValidationResult =
  | { ok: true }
  | { ok: false; reason: string };

const MAX_BYTES = 5 * 1024 * 1024;

export async function validatePdf(file: File): Promise<ValidationResult> {
  const looksPdf =
    file.type === "application/pdf" ||
    file.name.toLowerCase().endsWith(".pdf");
  if (!looksPdf) {
    return { ok: false, reason: "That doesn't look like a PDF — please choose a .pdf file." };
  }

  if (file.size > MAX_BYTES) {
    const mb = (file.size / 1024 / 1024).toFixed(1);
    return { ok: false, reason: `File is ${mb} MB — max is 5 MB.` };
  }

  const head = new Uint8Array(await file.slice(0, 4).arrayBuffer());
  const magic = String.fromCharCode(...head);
  if (magic !== "%PDF") {
    return { ok: false, reason: "That file isn't a real PDF (header check failed)." };
  }

  return { ok: true };
}
