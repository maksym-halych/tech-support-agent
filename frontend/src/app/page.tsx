"use client";

import { Suspense } from "react";
import { ChatPanel } from "@/components/chat-panel/ChatPanel";
import { Dashboard } from "@/components/lessons/Dashboard";
import { LessonsProvider } from "@/hooks/use-lessons";
import { StreamProvider } from "@/providers/Stream";

export default function HomePage() {
  return (
    // nuqs reads the URL (`chat`, `threadId`) through useSearchParams.
    <Suspense>
      <LessonsProvider>
        <StreamProvider>
          <Dashboard />
          <ChatPanel />
        </StreamProvider>
      </LessonsProvider>
    </Suspense>
  );
}
