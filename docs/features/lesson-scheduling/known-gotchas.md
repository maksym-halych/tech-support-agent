# Known gotchas: lesson scheduling

1. **The duration limits are in two places.** `MIN_DURATION_MINUTES` and `MAX_DURATION_MINUTES` in `models.py`, and the `CHECK` in the SQL schema. Keep them equal.
2. **The overlap queries use the maximum duration.** The service reads lessons that start up to 240 minutes before the new lesson. If you change the maximum duration, this window changes with it.
3. **"My lessons" includes lessons in progress.** The filter keeps each lesson that has not ended, not only future lessons.
4. **Writes use `BEGIN IMMEDIATE`.** This makes concurrent bookings safe on one SQLite file. Do not do a check and a write in two transactions.
5. **404 hides lessons.** The API returns 404, not 403, for a lesson that the caller cannot see. Tests expect this.
6. **CORS allows only GET and POST.** A new method, for example DELETE, needs a change in `create_app`.
7. **The lesson shape has three copies.** See `data-model.md`. A change in one copy breaks the frontend or the agent.
8. **`GET /lessons/available` returns 200 lessons or fewer.** The dashboard asks for the maximum. Use a time window if the number of lessons increases.
9. **Times without an offset are refused.** Send `2026-11-01T18:00:00+02:00`, not `2026-11-01T18:00:00`.
