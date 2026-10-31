"use client";

import { useAuth } from "@clerk/nextjs";
import { useCallback, useState } from "react";
import { toast } from "sonner";
import { useLessons } from "@/hooks/use-lessons";
import {
  createLessonsApi,
  type Lesson,
  type LessonsApi,
} from "@/lib/lessons-api";

/**
 * Runs one lesson write from the dashboard: tracks it as pending, reports the
 * outcome in a toast and reloads the lessons after a success. The API enforces
 * every rule, so its error message is shown as is.
 */
export function useLessonWrite() {
  const { getToken } = useAuth();
  const { refresh } = useLessons();
  const [pending, setPending] = useState(false);

  const run = useCallback(
    async (
      write: (api: LessonsApi) => Promise<Lesson>,
      success: (lesson: Lesson) => string,
    ): Promise<boolean> => {
      setPending(true);
      try {
        const lesson = await write(createLessonsApi(getToken));
        toast.success(success(lesson));
        await refresh();
        return true;
      } catch (caught) {
        toast.error(caught instanceof Error ? caught.message : String(caught));
        return false;
      } finally {
        setPending(false);
      }
    },
    [getToken, refresh],
  );

  return { pending, run };
}
