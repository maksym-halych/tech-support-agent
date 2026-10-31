"use client";

import { Dashboard } from "@/components/lessons/Dashboard";
import { LessonsProvider } from "@/hooks/use-lessons";

export default function HomePage() {
  return (
    <LessonsProvider>
      <Dashboard />
    </LessonsProvider>
  );
}
