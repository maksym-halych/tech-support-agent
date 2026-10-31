"use client";

import { addDays, format } from "date-fns";
import { Loader2, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useLessonWrite } from "@/hooks/use-lesson-write";

const DURATIONS = [15, 30, 45, 60, 90, 120, 180, 240];
const DEFAULT_DURATION = 60;
const DEFAULT_TIME = "10:00";

// Native date and time pickers follow the page's dark theme.
const pickerClass = "[color-scheme:dark]";

function tomorrow(): string {
  return format(addDays(new Date(), 1), "yyyy-MM-dd");
}

/**
 * The start time in the browser's timezone, or an error message. A
 * `yyyy-MM-ddTHH:mm` string without an offset parses as local time.
 */
function parseStart(date: string, time: string): Date | string {
  const start = new Date(`${date}T${time}`);
  if (Number.isNaN(start.getTime())) return "Pick a date and a start time.";
  if (start <= new Date()) return "Pick a start time in the future.";
  return start;
}

/** Teachers open a lesson slot that students can then book. */
export function NewLessonDialog() {
  const { pending, run } = useLessonWrite();
  const [open, setOpen] = useState(false);
  const [subject, setSubject] = useState("");
  const [date, setDate] = useState(tomorrow);
  const [time, setTime] = useState(DEFAULT_TIME);
  const [duration, setDuration] = useState(DEFAULT_DURATION);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setSubject("");
    setDate(tomorrow());
    setTime(DEFAULT_TIME);
    setDuration(DEFAULT_DURATION);
    setError(null);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const start = parseStart(date, time);
    if (typeof start === "string") {
      setError(start);
      return;
    }
    setError(null);
    const created = await run(
      (api) =>
        api.createLesson({
          subject: subject.trim(),
          starts_at: start.toISOString(),
          duration_minutes: duration,
        }),
      (lesson) =>
        `Opened ${lesson.subject} on ${format(new Date(lesson.starts_at), "EEE d MMM, HH:mm")}`,
    );
    if (created) {
      setOpen(false);
      reset();
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (pending) return;
        setOpen(next);
        if (!next) setError(null);
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm">
          <Plus />
          New lesson
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>New lesson</DialogTitle>
          <DialogDescription>
            Open a slot that students can book. Times are in your timezone.
          </DialogDescription>
        </DialogHeader>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => void submit(event)}
        >
          <div className="flex flex-col gap-2">
            <Label htmlFor="lesson-subject">Subject</Label>
            <Input
              id="lesson-subject"
              value={subject}
              onChange={(event) => setSubject(event.target.value)}
              placeholder="e.g. Python basics"
              maxLength={100}
              required
              autoFocus
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-2">
              <Label htmlFor="lesson-date">Date</Label>
              <Input
                id="lesson-date"
                type="date"
                className={pickerClass}
                value={date}
                min={format(new Date(), "yyyy-MM-dd")}
                onChange={(event) => setDate(event.target.value)}
                required
              />
            </div>
            <div className="flex flex-col gap-2">
              <Label htmlFor="lesson-time">Start time</Label>
              <Input
                id="lesson-time"
                type="time"
                className={pickerClass}
                value={time}
                step={300}
                onChange={(event) => setTime(event.target.value)}
                required
              />
            </div>
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="lesson-duration">Duration</Label>
            <select
              id="lesson-duration"
              className="h-9 w-full rounded-md border border-input bg-background px-3 text-base shadow-sm outline-none md:text-sm"
              value={duration}
              onChange={(event) => setDuration(Number(event.target.value))}
            >
              {DURATIONS.map((minutes) => (
                <option
                  key={minutes}
                  value={minutes}
                >
                  {minutes} min
                </option>
              ))}
            </select>
          </div>
          {error && (
            <p
              role="alert"
              className="text-sm text-red-400"
            >
              {error}
            </p>
          )}
          <DialogFooter>
            <DialogClose asChild>
              <Button
                type="button"
                variant="ghost"
                disabled={pending}
              >
                Cancel
              </Button>
            </DialogClose>
            <Button
              type="submit"
              disabled={pending || subject.trim() === ""}
            >
              {pending && <Loader2 className="animate-spin" />}
              Open lesson
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
