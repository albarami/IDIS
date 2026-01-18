"use client";

import { useEffect, useState, useCallback, FormEvent } from "react";
import { useRouter } from "next/navigation";
import Header from "@/components/Header";
import ErrorCallout from "@/components/ErrorCallout";
import { idis, type AuditEvent, IDISApiError } from "@/lib/idis";

export default function AuditEventsPage() {
  const router = useRouter();
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [errorRequestId, setErrorRequestId] = useState<string | undefined>(undefined);
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  const [hasMore, setHasMore] = useState(false);

  // Filters
  const [filterEventType, setFilterEventType] = useState("");
  const [filterDealId, setFilterDealId] = useState("");

  const fetchEvents = useCallback(
    async (reset = false) => {
      try {
        setLoading(true);
        const response = await idis.audit.listEvents({
          limit: 50,
          cursor: reset ? undefined : cursor,
          eventType: filterEventType || undefined,
          dealId: filterDealId || undefined,
        });

        // Deterministic sorting by occurred_at descending, then by event_id
        const sortedEvents = [...response.items].sort((a, b) => {
          const dateCompare =
            new Date(b.occurred_at).getTime() - new Date(a.occurred_at).getTime();
          if (dateCompare !== 0) return dateCompare;
          return a.event_id.localeCompare(b.event_id);
        });

        if (reset) {
          setEvents(sortedEvents);
        } else {
          setEvents((prev) => [...prev, ...sortedEvents]);
        }
        setCursor(response.next_cursor || undefined);
        setHasMore(!!response.next_cursor);
      } catch (err) {
        if (err instanceof IDISApiError && err.status === 401) {
          router.push("/login");
          return;
        }
        if (err instanceof IDISApiError) {
          setError(err.message);
          setErrorRequestId(err.requestId);
        } else {
          setError(err instanceof Error ? err.message : "Failed to load audit events");
        }
      } finally {
        setLoading(false);
      }
    },
    [cursor, filterEventType, filterDealId, router]
  );

  useEffect(() => {
    fetchEvents(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function handleFilter(e: FormEvent) {
    e.preventDefault();
    setCursor(undefined);
    fetchEvents(true);
  }

  function handleLoadMore() {
    if (hasMore && !loading) {
      fetchEvents(false);
    }
  }

  if (error) {
    return (
      <div className="min-h-screen bg-gray-50">
        <Header />
        <main className="max-w-7xl mx-auto px-4 py-8">
          <ErrorCallout message={error} requestId={errorRequestId} />
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <main className="max-w-7xl mx-auto px-4 py-8">
        <h1 className="text-2xl font-bold text-gray-900 mb-6">Audit Events</h1>

        {/* Filters */}
        <div className="bg-white shadow rounded-lg p-4 mb-6">
          <form onSubmit={handleFilter} className="flex flex-wrap gap-4 items-end">
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Event Type
              </label>
              <input
                type="text"
                value={filterEventType}
                onChange={(e) => setFilterEventType(e.target.value)}
                placeholder="e.g., deal.created"
                className="px-3 py-2 border border-gray-300 rounded-md text-sm w-48"
              />
            </div>
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">
                Deal ID
              </label>
              <input
                type="text"
                value={filterDealId}
                onChange={(e) => setFilterDealId(e.target.value)}
                placeholder="UUID"
                className="px-3 py-2 border border-gray-300 rounded-md text-sm w-64"
              />
            </div>
            <button
              type="submit"
              className="px-4 py-2 bg-blue-600 text-white rounded-md text-sm hover:bg-blue-700"
            >
              Apply Filters
            </button>
            <button
              type="button"
              onClick={() => {
                setFilterEventType("");
                setFilterDealId("");
                setCursor(undefined);
                fetchEvents(true);
              }}
              className="px-4 py-2 bg-gray-100 text-gray-700 rounded-md text-sm hover:bg-gray-200"
            >
              Clear
            </button>
          </form>
        </div>

        {/* Events Table */}
        <div className="bg-white shadow rounded-lg overflow-hidden">
          {loading && events.length === 0 ? (
            <div className="p-6 text-center text-gray-500">Loading events...</div>
          ) : events.length === 0 ? (
            <div className="p-6 text-center text-gray-500">No audit events found</div>
          ) : (
            <>
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                      Occurred At
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                      Event Type
                    </th>
                    <th className="px-4 py-3 text-left text-xs font-medium text-gray-500 uppercase">
                      Event ID
                    </th>
                  </tr>
                </thead>
                <tbody className="bg-white divide-y divide-gray-200">
                  {events.map((event) => (
                    <tr key={event.event_id} className="hover:bg-gray-50">
                      <td className="px-4 py-3 whitespace-nowrap text-sm text-gray-500">
                        {new Date(event.occurred_at).toLocaleString()}
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap">
                        <span className="text-sm font-medium text-gray-900">
                          {event.event_type}
                        </span>
                      </td>
                      <td className="px-4 py-3 whitespace-nowrap text-sm">
                        <span className="font-mono text-xs text-gray-700">
                          {event.event_id}
                        </span>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>

              {/* Load More */}
              {hasMore && (
                <div className="p-4 border-t border-gray-200 text-center">
                  <button
                    onClick={handleLoadMore}
                    disabled={loading}
                    className="px-4 py-2 text-sm text-blue-600 hover:text-blue-800 disabled:opacity-50"
                  >
                    {loading ? "Loading..." : "Load More"}
                  </button>
                </div>
              )}
            </>
          )}
        </div>

        <p className="mt-4 text-sm text-gray-500">
          Showing {events.length} events
        </p>
      </main>
    </div>
  );
}
