"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";

export default function Header() {
  const router = useRouter();

  async function handleLogout() {
    await fetch("/api/session", { method: "DELETE" });
    router.push("/login");
  }

  return (
    <header className="bg-white border-b border-gray-200">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16 items-center">
          <div className="flex items-center space-x-8">
            <Link href="/deals" className="text-xl font-bold text-gray-900">
              IDIS
            </Link>
            <nav className="flex space-x-4">
              <Link
                href="/deals"
                className="text-gray-600 hover:text-gray-900 px-3 py-2 text-sm font-medium"
              >
                Deals
              </Link>
              <Link
                href="/audit/events"
                className="text-gray-600 hover:text-gray-900 px-3 py-2 text-sm font-medium"
              >
                Audit Log
              </Link>
            </nav>
          </div>
          <button
            onClick={handleLogout}
            className="text-gray-600 hover:text-gray-900 px-3 py-2 text-sm font-medium"
          >
            Logout
          </button>
        </div>
      </div>
    </header>
  );
}
