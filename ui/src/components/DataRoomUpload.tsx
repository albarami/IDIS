"use client";

import { useState, type FormEvent } from "react";
import ErrorCallout from "@/components/ErrorCallout";
import { idis, type DocumentArtifact, IDISApiError } from "@/lib/idis";

const DOC_TYPES = [
  "DATA_ROOM_FILE",
  "PITCH_DECK",
  "FINANCIAL_MODEL",
  "TRANSCRIPT",
  "TERM_SHEET",
  "OTHER",
];

/**
 * Data-room document upload form (deal-scoped) over POST /v1/deals/{dealId}/documents/upload.
 *
 * Displays a SAFE result summary only — document id, name, type, and parse status. The raw
 * document bytes are sent to the API but never rendered back.
 */
export default function DataRoomUpload({ dealId }: { dealId: string }) {
  const [file, setFile] = useState<File | null>(null);
  const [docType, setDocType] = useState("DATA_ROOM_FILE");
  const [uploading, setUploading] = useState(false);
  const [result, setResult] = useState<DocumentArtifact | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorRequestId, setErrorRequestId] = useState<string | undefined>(undefined);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!file) {
      setError("Select a file to upload.");
      return;
    }
    setUploading(true);
    setError(null);
    setErrorRequestId(undefined);
    setResult(null);
    try {
      const artifact = await idis.documents.upload(dealId, file, {
        filename: file.name,
        docType,
      });
      setResult(artifact);
    } catch (err) {
      if (err instanceof IDISApiError) {
        setError(err.message);
        setErrorRequestId(err.requestId);
      } else {
        setError(err instanceof Error ? err.message : "Upload failed");
      }
    } finally {
      setUploading(false);
    }
  }

  return (
    <div className="space-y-6">
      <form
        onSubmit={handleSubmit}
        className="space-y-4 rounded-lg border border-gray-200 bg-white p-4"
      >
        <div>
          <label htmlFor="upload-file" className="block text-sm font-medium text-gray-700">
            File
          </label>
          <input
            id="upload-file"
            type="file"
            onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            className="mt-1 block w-full text-sm"
          />
        </div>
        <div>
          <label htmlFor="upload-doc-type" className="block text-sm font-medium text-gray-700">
            Document type
          </label>
          <select
            id="upload-doc-type"
            value={docType}
            onChange={(event) => setDocType(event.target.value)}
            className="mt-1 block w-full rounded border-gray-300 text-sm"
          >
            {DOC_TYPES.map((type) => (
              <option key={type} value={type}>
                {type.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
        <button
          type="submit"
          disabled={uploading}
          className="rounded bg-blue-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        >
          {uploading ? "Uploading..." : "Upload"}
        </button>
      </form>

      {error && <ErrorCallout message={error} requestId={errorRequestId} />}

      {result && (
        <div className="rounded-lg border border-green-200 bg-green-50 p-4">
          <h3 className="text-sm font-semibold text-green-800">Uploaded</h3>
          <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
            <dt className="text-gray-500">Document ID</dt>
            <dd className="font-mono text-gray-900">{result.doc_id}</dd>
            <dt className="text-gray-500">Name</dt>
            <dd className="text-gray-900">{result.title ?? file?.name}</dd>
            <dt className="text-gray-500">Type</dt>
            <dd className="text-gray-900">{result.doc_type}</dd>
            {result.parse_status && (
              <>
                <dt className="text-gray-500">Parse status</dt>
                <dd className="text-gray-900">{result.parse_status}</dd>
              </>
            )}
          </dl>
        </div>
      )}
    </div>
  );
}
