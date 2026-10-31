"use client";

import { Suspense } from "react";
import { ChatPanel } from "@/components/chat-panel/ChatPanel";
import { Dashboard } from "@/components/lessons/Dashboard";
import { Toaster } from "@/components/ui/sonner";
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
          <Toaster
            position="top-center"
            theme="dark"
          />
        </StreamProvider>
      </LessonsProvider>
    </Suspense>
  );
}
