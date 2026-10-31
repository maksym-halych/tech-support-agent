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
  /** Open lessons a student can book; always empty for teachers. */
  available: Lesson[];
  loading: boolean;
  error: string | null;
  /** Reload the current user and their lessons, e.g. after a write. */
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
  const [available, setAvailable] = useState<Lesson[]>([]);
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
      // Only students may list open lessons; the API refuses teachers.
      const nextAvailable =
        nextMe.role === "student" ? await api.availableLessons() : [];
      if (request !== latest.current) return;
      setMe(nextMe);
      setLessons(nextLessons);
      setAvailable(nextAvailable);
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
    () => ({ me, lessons, available, loading, error, refresh }),
    [me, lessons, available, loading, error, refresh],
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
