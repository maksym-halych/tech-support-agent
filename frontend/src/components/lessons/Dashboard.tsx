"use client";

import { UserButton } from "@clerk/nextjs";
import { AlertCircle, RefreshCw } from "lucide-react";
import { AppLogo, APP_NAME } from "@/components/icons/logo";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useLessons } from "@/hooks/use-lessons";
import { LESSONS_API_URL } from "@/lib/lessons-api";
import { cn } from "@/lib/utils";
import { useChatOpen } from "@/components/chat-panel/use-chat-open";
import { LessonSchedule } from "./LessonSchedule";

const userButtonAppearance = {
  elements: { avatarBox: { height: "2rem", width: "2rem" } },
};

function Header() {
  const { me } = useLessons();
  return (
    <header className="flex items-center justify-between gap-3 border-b px-4 py-3 sm:px-6">
      <div className="flex items-center gap-2">
        <AppLogo className="size-7 text-primary" />
        <span className="text-lg font-semibold tracking-tight">{APP_NAME}</span>
      </div>
      <div className="flex items-center gap-3">
        {me && (
          <div className="hidden items-center gap-2 sm:flex">
            <span className="text-sm">{me.name}</span>
            <Badge variant={me.role === "teacher" ? "default" : "secondary"}>
              {me.role === "teacher" ? "Teacher" : "Student"}
            </Badge>
          </div>
        )}
        <UserButton appearance={userButtonAppearance} />
      </div>
    </header>
  );
}

function LoadingState() {
  return (
    <div
      className="flex flex-col gap-3"
      aria-label="Loading lessons"
    >
      <Skeleton className="h-4 w-40" />
      <Skeleton className="h-20 w-full rounded-xl" />
      <Skeleton className="h-4 w-32" />
      <Skeleton className="h-32 w-full rounded-xl" />
    </div>
  );
}

function ErrorState({ message }: { message: string }) {
  const { refresh, loading } = useLessons();
  return (
    <Card className="items-center gap-3 py-10 text-center">
      <AlertCircle className="size-8 text-red-400" />
      <p className="font-medium">Could not load your lessons</p>
      <p className="max-w-md text-sm text-muted-foreground">{message}</p>
      <p className="max-w-md text-xs text-muted-foreground">
        Make sure the lessons API is running at <code>{LESSONS_API_URL}</code>.
      </p>
      <Button
        variant="secondary"
        size="sm"
        onClick={() => void refresh()}
        disabled={loading}
      >
        <RefreshCw className={loading ? "animate-spin" : undefined} />
        Try again
      </Button>
    </Card>
  );
}

export function Dashboard() {
  const { me, lessons, loading, error, refresh } = useLessons();
  const [chatOpen] = useChatOpen();
  const title = me?.role === "teacher" ? "My schedule" : "My lessons";
  const subtitle =
    me?.role === "teacher"
      ? "Upcoming lessons you teach, open or booked."
      : "Upcoming lessons you have booked.";

  return (
    <div
      className={cn(
        "flex h-dvh flex-col transition-[padding] duration-300",
        // Leave room for the chat panel on wide screens.
        chatOpen && "md:pr-[420px]",
      )}
    >
      <Header />
      <main className="scrollbar flex-1 overflow-y-auto">
        <div className="mx-auto flex w-full max-w-3xl flex-col gap-6 px-4 pb-24 pt-8 sm:px-6">
          <div className="flex items-end justify-between gap-4">
            <div>
              <h1 className="text-2xl font-semibold tracking-tight">
                {me ? title : "Lessons"}
              </h1>
              {me && (
                <p className="text-sm text-muted-foreground">{subtitle}</p>
              )}
            </div>
            {me && (
              <Button
                variant="ghost"
                size="icon"
                aria-label="Refresh lessons"
                onClick={() => void refresh()}
                disabled={loading}
              >
                <RefreshCw className={loading ? "animate-spin" : undefined} />
              </Button>
            )}
          </div>
          {error && !loading ? (
            <ErrorState message={error} />
          ) : !me ? (
            <LoadingState />
          ) : (
            <LessonSchedule
              lessons={lessons}
              role={me.role}
            />
          )}
        </div>
      </main>
    </div>
  );
}
