import { useState } from "react";
import { normalizeDebateRounds } from "@/lib/debateNormalizer";

interface DebateTranscriptProps {
  rounds: unknown[];
}

export default function DebateTranscript({ rounds }: DebateTranscriptProps) {
  const [showRaw, setShowRaw] = useState(false);
  const normalizedRounds = normalizeDebateRounds(rounds);

  if (rounds.length === 0) {
    return (
      <div className="text-sm text-gray-500 text-center py-4">
        No debate rounds available
      </div>
    );
  }

  return (
    <div>
      <div className="flex justify-between items-center mb-3">
        <h3 className="text-sm font-medium text-gray-900">
          Debate Transcript ({normalizedRounds.length} rounds)
        </h3>
        <button
          onClick={() => setShowRaw(!showRaw)}
          className="text-xs text-blue-600 hover:text-blue-800"
        >
          {showRaw ? "Show Formatted" : "Show Raw JSON"}
        </button>
      </div>

      {showRaw ? (
        <div className="bg-gray-50 p-3 rounded max-h-96 overflow-y-auto">
          <pre className="text-xs whitespace-pre-wrap">
            {JSON.stringify(rounds, null, 2)}
          </pre>
        </div>
      ) : (
        <div className="space-y-3 max-h-96 overflow-y-auto">
          {normalizedRounds.map((round, index) => (
            <div
              key={index}
              className="bg-white border border-gray-200 rounded-lg p-4"
            >
              <div className="flex items-start justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className="inline-flex items-center justify-center w-6 h-6 rounded-full bg-blue-100 text-blue-800 text-xs font-medium">
                    {round.roundNumber}
                  </span>
                  <span className="text-sm font-medium text-gray-900">
                    {round.speaker}
                  </span>
                </div>
                {round.timestamp && (
                  <span className="text-xs text-gray-500">
                    {new Date(round.timestamp).toLocaleString()}
                  </span>
                )}
              </div>
              
              {round.message ? (
                <p className="text-sm text-gray-700 whitespace-pre-wrap">
                  {round.message}
                </p>
              ) : (
                <details className="text-xs text-gray-500">
                  <summary className="cursor-pointer hover:text-gray-700">
                    No message field found - show raw data
                  </summary>
                  <pre className="mt-2 p-2 bg-gray-50 rounded overflow-x-auto">
                    {JSON.stringify(round.rawData, null, 2)}
                  </pre>
                </details>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
