"use client";

import { useAuth } from "@clerk/nextjs";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { createLessonsApi, type Lesson, type Me } from "@/lib/lessons-api";

interface LessonsState {
  me: Me | null;
  lessons: Lesson[];
  loading: boolean;
  error: string | null;
  /** Reload the current user and their lessons, e.g. after the agent wrote. */
  refresh: () => Promise<void>;
}

const LessonsContext = createContext<LessonsState | undefined>(undefined);

export function LessonsProvider({ children }: { children: ReactNode }) {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  // Clerk returns a new `getToken` on most renders; read it through a ref so
  // `refresh` keeps a stable identity.
  const getTokenRef = useRef(getToken);
  useEffect(() => {
    getTokenRef.current = getToken;
  }, [getToken]);

  const [me, setMe] = useState<Me | null>(null);
  const [lessons, setLessons] = useState<Lesson[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  // Only the latest request may update state, so a slow earlier response
  // cannot overwrite a newer one.
  const latest = useRef(0);

  const load = useCallback(async () => {
    const request = ++latest.current;
    const api = createLessonsApi(() => getTokenRef.current());
    try {
      const [nextMe, nextLessons] = await Promise.all([
        api.me(),
        api.myLessons(),
      ]);
      if (request !== latest.current) return;
      setMe(nextMe);
      setLessons(nextLessons);
      setError(null);
    } catch (caught) {
      if (request !== latest.current) return;
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      if (request === latest.current) setLoading(false);
    }
  }, []);

  const refresh = useCallback(async () => {
    setLoading(true);
    await load();
  }, [load]);

  useEffect(() => {
    // `loading` starts true, so the first load does not need to set it.
    if (isLoaded && isSignedIn) void load();
  }, [isLoaded, isSignedIn, load]);

  const value = useMemo(
    () => ({ me, lessons, loading, error, refresh }),
    [me, lessons, loading, error, refresh],
  );
  return (
    <LessonsContext.Provider value={value}>{children}</LessonsContext.Provider>
  );
}

export function useLessons(): LessonsState {
  const context = useContext(LessonsContext);
  if (context === undefined) {
    throw new Error("useLessons must be used within a LessonsProvider");
  }
  return context;
}
