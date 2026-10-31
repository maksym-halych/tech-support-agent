"use client";

import { format, isToday, isTomorrow } from "date-fns";
import { CalendarSearch, CalendarX2, Clock, Loader2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { useLessonWrite } from "@/hooks/use-lesson-write";
import type { Lesson, Role } from "@/lib/lessons-api";

function dayLabel(date: Date): string {
  if (isToday(date)) return `Today, ${format(date, "d MMMM")}`;
  if (isTomorrow(date)) return `Tomorrow, ${format(date, "d MMMM")}`;
  return format(date, "EEEE, d MMMM");
}

function whenLabel(lesson: Lesson): string {
  return format(new Date(lesson.starts_at), "EEEE, d MMMM 'at' HH:mm");
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

function cancelDescription(lesson: Lesson, role: Role): string {
  if (role === "student") {
    return "Your booking is cancelled and the lesson opens again for other students.";
  }
  if (lesson.status === "booked") {
    return `${lesson.student?.name ?? "A student"} has booked this lesson and will see it cancelled. This cannot be undone.`;
  }
  return "Students will no longer be able to book it. This cannot be undone.";
}

/** Cancels a booking (student) or a lesson (teacher) after a confirmation. */
function CancelLessonButton({ lesson, role }: { lesson: Lesson; role: Role }) {
  const { pending, run } = useLessonWrite();
  const [open, setOpen] = useState(false);

  async function cancel() {
    const cancelled = await run(
      (api) => api.cancelLesson(lesson.id),
      (result) =>
        role === "student"
          ? `Cancelled your booking for ${result.subject}`
          : `Cancelled ${result.subject}`,
    );
    if (cancelled) setOpen(false);
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => !pending && setOpen(next)}
    >
      <DialogTrigger asChild>
        <Button
          variant="ghost"
          size="sm"
          className="text-muted-foreground hover:text-red-400"
        >
          Cancel
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {role === "student"
              ? "Cancel your booking?"
              : "Cancel this lesson?"}
          </DialogTitle>
          <DialogDescription>
            {lesson.subject}, {whenLabel(lesson)}.{" "}
            {cancelDescription(lesson, role)}
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button
              variant="ghost"
              disabled={pending}
            >
              Keep it
            </Button>
          </DialogClose>
          <Button
            variant="destructive"
            onClick={() => void cancel()}
            disabled={pending}
          >
            {pending && <Loader2 className="animate-spin" />}
            {role === "student" ? "Cancel booking" : "Cancel lesson"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function BookLessonButton({ lesson }: { lesson: Lesson }) {
  const { pending, run } = useLessonWrite();
  return (
    <Button
      size="sm"
      onClick={() =>
        void run(
          (api) => api.bookLesson(lesson.id),
          (result) =>
            `Booked ${result.subject} with ${result.teacher.name}, ${whenLabel(result)}`,
        )
      }
      disabled={pending}
    >
      {pending && <Loader2 className="animate-spin" />}
      Book
    </Button>
  );
}

function LessonRow({
  lesson,
  showTeacher,
  children,
}: {
  lesson: Lesson;
  showTeacher: boolean;
  children: ReactNode;
}) {
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
            {showTeacher ? `with ${lesson.teacher.name} · ` : ""}
            {lesson.duration_minutes} min
          </p>
        </div>
      </div>
      <div className="flex items-center gap-2 pl-[7.75rem] sm:pl-0">
        {children}
      </div>
    </li>
  );
}

function LessonDays({
  lessons,
  children,
}: {
  lessons: Lesson[];
  children: (lesson: Lesson) => ReactNode;
}) {
  return (
    <div className="flex flex-col gap-6">
      {groupByDay(lessons).map(([day, dayLessons]) => (
        <section
          key={day}
          className="flex flex-col gap-2"
        >
          <h3 className="text-sm font-medium text-muted-foreground">
            {dayLabel(new Date(dayLessons[0].starts_at))}
          </h3>
          <Card className="py-4">
            <CardContent>
              <ul className="divide-y">{dayLessons.map(children)}</ul>
            </CardContent>
          </Card>
        </section>
      ))}
    </div>
  );
}

function EmptyCard({
  icon,
  title,
  children,
}: {
  icon: ReactNode;
  title: string;
  children: ReactNode;
}) {
  return (
    <Card className="items-center gap-3 py-12 text-center">
      {icon}
      <p className="font-medium">{title}</p>
      <p className="max-w-sm text-sm text-muted-foreground">{children}</p>
    </Card>
  );
}

/** The caller's upcoming lessons: booked ones for students, taught ones for teachers. */
export function LessonSchedule({
  lessons,
  role,
}: {
  lessons: Lesson[];
  role: Role;
}) {
  if (lessons.length === 0) {
    return (
      <EmptyCard
        icon={<CalendarX2 className="size-8 text-muted-foreground" />}
        title="No upcoming lessons"
      >
        {role === "student"
          ? "Book one of the open lessons below."
          : "Click New lesson to open a slot for your students."}
      </EmptyCard>
    );
  }
  return (
    <LessonDays lessons={lessons}>
      {(lesson) => (
        <LessonRow
          key={lesson.id}
          lesson={lesson}
          showTeacher={role === "student"}
        >
          <LessonStatus
            lesson={lesson}
            role={role}
          />
          {lesson.status !== "cancelled" &&
            new Date(lesson.starts_at) > new Date() && (
              <CancelLessonButton
                lesson={lesson}
                role={role}
              />
            )}
        </LessonRow>
      )}
    </LessonDays>
  );
}

/** Open future lessons a student can book. */
export function AvailableLessons({ lessons }: { lessons: Lesson[] }) {
  if (lessons.length === 0) {
    return (
      <EmptyCard
        icon={<CalendarSearch className="size-8 text-muted-foreground" />}
        title="No open lessons"
      >
        Teachers have not opened any lessons yet. Check back later.
      </EmptyCard>
    );
  }
  return (
    <LessonDays lessons={lessons}>
      {(lesson) => (
        <LessonRow
          key={lesson.id}
          lesson={lesson}
          showTeacher
        >
          <BookLessonButton lesson={lesson} />
        </LessonRow>
      )}
    </LessonDays>
  );
}
