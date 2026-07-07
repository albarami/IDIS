"use client";

import { useParams } from "next/navigation";
import Header from "@/components/Header";
import DataRoomUpload from "@/components/DataRoomUpload";

export default function DataRoomUploadPage() {
  const params = useParams();
  const dealId = params.dealId as string;

  return (
    <div className="min-h-screen bg-gray-50">
      <Header />
      <main className="mx-auto max-w-3xl px-4 py-8">
        <h1 className="mb-6 text-2xl font-bold text-gray-900">Upload data-room document</h1>
        <DataRoomUpload dealId={dealId} />
      </main>
    </div>
  );
}
