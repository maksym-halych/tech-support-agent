# Frontend structure: lesson scheduling

| File | Content |
|---|---|
| `frontend/src/app/page.tsx` | Home page: `LessonsProvider`, `Dashboard` and the chat panel |
| `frontend/src/components/lessons/Dashboard.tsx` | Header, loading and error states, the views for the role |
| `frontend/src/components/lessons/LessonSchedule.tsx` | `LessonSchedule`, `AvailableLessons`, the **Book** and **Cancel** buttons, the lesson status |
| `frontend/src/components/lessons/NewLessonDialog.tsx` | The form to open a lesson (teachers only) |
| `frontend/src/hooks/use-lessons.tsx` | `LessonsProvider` and `useLessons`: `me`, `lessons`, `available`, `refresh`, `api` |
| `frontend/src/hooks/use-lesson-write.ts` | `useLessonWrite`: runs one write, shows a toast, loads the lessons again |
| `frontend/src/lib/lessons-api.ts` | `createLessonsApi`: the typed API client; zod schemas for the responses; `LessonsApiError` |
