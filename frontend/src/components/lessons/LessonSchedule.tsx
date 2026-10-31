import { format, isToday, isTomorrow } from "date-fns";
import { CalendarX2, Clock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import type { Lesson, Role } from "@/lib/lessons-api";

function dayLabel(date: Date): string {
  if (isToday(date)) return `Today, ${format(date, "d MMMM")}`;
  if (isTomorrow(date)) return `Tomorrow, ${format(date, "d MMMM")}`;
  return format(date, "EEEE, d MMMM");
}

/** Lessons grouped by local calendar day, keeping the API's start-time order. */
function groupByDay(lessons: Lesson[]): [string, Lesson[]][] {
  const groups = new Map<string, Lesson[]>();
  for (const lesson of lessons) {
    const key = format(new Date(lesson.starts_at), "yyyy-MM-dd");
    groups.set(key, [...(groups.get(key) ?? []), lesson]);
  }
  return [...groups.entries()];
}

function LessonStatus({ lesson, role }: { lesson: Lesson; role: Role }) {
  if (lesson.status === "cancelled") {
    return (
      <Badge variant="destructive">
        {role === "student" ? "Cancelled by teacher" : "Cancelled"}
      </Badge>
    );
  }
  if (role === "student") return <Badge variant="success">Booked</Badge>;
  if (lesson.status === "booked") {
    return (
      <Badge variant="success">
        Booked by {lesson.student?.name ?? "a student"}
      </Badge>
    );
  }
  return <Badge variant="muted">Open</Badge>;
}

function LessonRow({ lesson, role }: { lesson: Lesson; role: Role }) {
  const start = new Date(lesson.starts_at);
  const end = new Date(lesson.ends_at);
  return (
    <li
      className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between"
      data-lesson-id={lesson.id}
    >
      <div className="flex min-w-0 items-start gap-3">
        <div className="flex w-28 shrink-0 items-center gap-1.5 text-sm tabular-nums text-muted-foreground">
          <Clock className="size-3.5" />
          {format(start, "HH:mm")}–{format(end, "HH:mm")}
        </div>
        <div className="min-w-0">
          <p
            className={
              lesson.status === "cancelled"
                ? "truncate font-medium text-muted-foreground line-through"
                : "truncate font-medium"
            }
          >
            {lesson.subject}
          </p>
          <p className="text-sm text-muted-foreground">
            {role === "student" ? `with ${lesson.teacher.name} · ` : ""}
            {lesson.duration_minutes} min
          </p>
        </div>
      </div>
      <div className="pl-[7.75rem] sm:pl-0">
        <LessonStatus
          lesson={lesson}
          role={role}
        />
      </div>
    </li>
  );
}

export function LessonSchedule({
  lessons,
  role,
}: {
  lessons: Lesson[];
  role: Role;
}) {
  if (lessons.length === 0) {
    return (
      <Card className="items-center gap-3 py-12 text-center">
        <CalendarX2 className="size-8 text-muted-foreground" />
        <p className="font-medium">No upcoming lessons</p>
        <p className="max-w-sm text-sm text-muted-foreground">
          {role === "student"
            ? "Ask the assistant to find an open lesson and book it for you."
            : "Ask the assistant to open a lesson slot for your students."}
        </p>
      </Card>
    );
  }
  return (
    <div className="flex flex-col gap-6">
      {groupByDay(lessons).map(([day, dayLessons]) => (
        <section
          key={day}
          className="flex flex-col gap-2"
        >
          <h2 className="text-sm font-medium text-muted-foreground">
            {dayLabel(new Date(dayLessons[0].starts_at))}
          </h2>
          <Card className="py-4">
            <CardContent>
              <ul className="divide-y">
                {dayLessons.map((lesson) => (
                  <LessonRow
                    key={lesson.id}
                    lesson={lesson}
                    role={role}
                  />
                ))}
              </ul>
            </CardContent>
          </Card>
        </section>
      ))}
    </div>
  );
}
