/**
 * ErrorCallout component for displaying API errors with request_id visibility.
 * Used across all pages to ensure request correlation for audit trails.
 */

interface ErrorCalloutProps {
  title?: string;
  message: string;
  requestId?: string;
}

export default function ErrorCallout({
  title = "Error",
  message,
  requestId,
}: ErrorCalloutProps) {
  return (
    <div className="rounded-md bg-red-50 p-4 border border-red-200">
      <div className="flex">
        <div className="flex-shrink-0">
          <svg
            className="h-5 w-5 text-red-400"
            viewBox="0 0 20 20"
            fill="currentColor"
          >
            <path
              fillRule="evenodd"
              d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z"
              clipRule="evenodd"
            />
          </svg>
        </div>
        <div className="ml-3 flex-1">
          <h3 className="text-sm font-medium text-red-800">{title}</h3>
          <div className="mt-2 text-sm text-red-700">
            <p>{message}</p>
          </div>
          {requestId && (
            <div className="mt-3 text-xs text-red-600 font-mono bg-red-100 px-2 py-1 rounded inline-block">
              Request ID: {requestId}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
