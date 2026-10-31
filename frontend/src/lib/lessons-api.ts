import { z } from "zod";
import type { GetToken } from "@/providers/client";

export const LESSONS_API_URL =
  process.env.NEXT_PUBLIC_LESSONS_API_URL ?? "http://localhost:8001";

const personSchema = z.object({ id: z.string(), name: z.string() });

export const roleSchema = z.enum(["student", "teacher"]);

export const meSchema = z.object({
  id: z.string(),
  name: z.string(),
  email: z.string(),
  role: roleSchema,
});

export const lessonSchema = z.object({
  id: z.string(),
  subject: z.string(),
  starts_at: z.iso.datetime({ offset: true }),
  ends_at: z.iso.datetime({ offset: true }),
  duration_minutes: z.number().int(),
  status: z.enum(["open", "booked", "cancelled"]),
  teacher: personSchema,
  student: personSchema.nullable(),
  created_at: z.iso.datetime({ offset: true }),
  cancelled_at: z.iso.datetime({ offset: true }).nullable(),
});

export type Role = z.infer<typeof roleSchema>;
export type Me = z.infer<typeof meSchema>;
export type Lesson = z.infer<typeof lessonSchema>;

/** A lesson slot a teacher opens; `starts_at` is ISO 8601 with an offset. */
export interface NewLesson {
  subject: string;
  starts_at: string;
  duration_minutes: number;
}

/** A failed lessons API call; `status` is 0 when the API was unreachable. */
export class LessonsApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = "LessonsApiError";
  }
}

async function errorMessage(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object" && "detail" in body) {
      if (typeof body.detail === "string") return body.detail;
      // FastAPI validation errors: a list of `{ msg }` objects.
      if (Array.isArray(body.detail) && body.detail.length > 0) {
        const first: unknown = body.detail[0];
        if (
          first &&
          typeof first === "object" &&
          "msg" in first &&
          typeof first.msg === "string"
        ) {
          return first.msg;
        }
      }
    }
  } catch {
    // Not JSON; fall through to the status text.
  }
  return response.statusText || `Request failed with ${response.status}`;
}

/**
 * Typed client for the lessons API. A fresh Clerk token is fetched for every
 * request, because session tokens only live about a minute.
 */
export function createLessonsApi(
  getToken: GetToken,
  baseUrl = LESSONS_API_URL,
) {
  async function request<T>(
    method: "GET" | "POST",
    path: string,
    schema: z.ZodType<T>,
    body?: unknown,
  ): Promise<T> {
    const token = await getToken();
    if (!token) throw new LessonsApiError(401, "You are signed out");
    const headers: Record<string, string> = {
      Authorization: `Bearer ${token}`,
    };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let response: Response;
    try {
      response = await fetch(`${baseUrl}${path}`, {
        method,
        headers,
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch {
      throw new LessonsApiError(
        0,
        `Cannot reach the lessons API at ${baseUrl}`,
      );
    }
    if (!response.ok) {
      throw new LessonsApiError(response.status, await errorMessage(response));
    }
    return schema.parse(await response.json());
  }

  const lessonPath = (id: string) => `/lessons/${encodeURIComponent(id)}`;

  return {
    me: () => request("GET", "/me", meSchema),
    myLessons: (includePast = false) =>
      request(
        "GET",
        `/lessons/mine?include_past=${includePast}`,
        z.array(lessonSchema),
      ),
    /** Students only: open future lessons, soonest first. */
    availableLessons: () =>
      request("GET", "/lessons/available?limit=200", z.array(lessonSchema)),
    /** Teachers only: open a lesson slot. */
    createLesson: (lesson: NewLesson) =>
      request("POST", "/lessons", lessonSchema, lesson),
    /** Students only: book an open lesson. */
    bookLesson: (id: string) =>
      request("POST", `${lessonPath(id)}/book`, lessonSchema),
    /** Students cancel their booking; teachers cancel the lesson. */
    cancelLesson: (id: string) =>
      request("POST", `${lessonPath(id)}/cancel`, lessonSchema),
  };
}

export type LessonsApi = ReturnType<typeof createLessonsApi>;
