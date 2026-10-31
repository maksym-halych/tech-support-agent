# Frequently asked questions

The lesson assistant answers how-to questions from this file through the `search_faq` tool. Each entry is a `## ` heading with the question, an `Audience:` line (`everyone`, `student` or `teacher`), a `Keywords:` line with other words users say, and the answer. Use the labels that the dashboard shows, in bold. Keep each answer true to the code: if the dashboard or a lesson rule changes, change the answer.

## How do I sign in?

Audience: everyone
Keywords: login, log in, sign in, account, register, sign up, access

Open the app and use the sign-in page. A new account is a student account. After you sign in, the dashboard opens with your lessons.

## How do I sign out or manage my account?

Audience: everyone
Keywords: logout, log out, sign out, profile, password, email, account settings, avatar

Click your avatar in the top-right corner of the dashboard. The menu has **Manage account**, where you change your name, email or password, and **Sign out**.

## How do I know if I am a student or a teacher?

Audience: everyone
Keywords: role, badge, account type, permissions

Your role badge, **Student** or **Teacher**, is next to your name in the top-right corner of the dashboard. On a narrow screen the name and badge are hidden; the page title then tells you: students see **My lessons**, teachers see **My schedule**.

## How do I become a teacher?

Audience: student
Keywords: teacher account, change role, upgrade, permissions, open lessons as teacher, teach

Every new account is a student account. You cannot change your role yourself: an administrator of the platform makes an account a teacher. After the administrator changes your role, sign out and sign in again, because the old session keeps the old role.

## My role was changed but the app still shows the old one

Audience: everyone
Keywords: role not updated, still student, still teacher, badge wrong, new role

The role comes from your sign-in session. Click your avatar in the top-right corner, choose **Sign out**, then sign in again. The new role shows after the new sign-in.

## How do I book a lesson?

Audience: student
Keywords: book, booking, reserve, sign up for a lesson, schedule, enroll, find a lesson

1. On the dashboard, scroll down to **Open lessons**. It lists the lessons that teachers have opened, grouped by day, with the teacher and the duration.
2. Click **Book** next to the lesson you want.
3. A message confirms the booking, and the lesson moves up to **My lessons**.

## Why can I not see any open lessons?

Audience: student
Keywords: no lessons, empty, nothing to book, no open lessons, missing lesson, list empty

**Open lessons** shows only lessons that start in the future and that nobody has booked yet. If it says **No open lessons**, no teacher has an open lesson right now. Click the refresh button (the circular arrow next to the page title) to load the latest list, or check back later.

## Can more than one student book the same lesson?

Audience: everyone
Keywords: group lesson, several students, shared lesson, capacity, seats

No. One lesson has one student. When a student books it, it leaves **Open lessons** for everybody else.

## It says "Someone else has already booked this lesson"

Audience: student
Keywords: already booked, taken, booking failed, conflict, someone else

Another student booked the lesson a moment before you. Your list was older than the latest change. Click the refresh button next to the page title and pick another lesson from **Open lessons**.

## It says "You already have a lesson" at that time

Audience: student
Keywords: overlap, overlapping, double booking, same time, clash, conflict, you already have

You cannot book two lessons at the same time. The message names the lesson you already have. Choose a lesson at another time, or first cancel the booking you already have.

## It says "This lesson has already started"

Audience: everyone
Keywords: started, too late, in progress, cannot book, cannot cancel, past lesson

You can book or cancel a lesson only before it starts. A lesson that is in progress stays on the dashboard until it ends, but it has no **Book** or **Cancel** button.

## How do I cancel my booking?

Audience: student
Keywords: cancel, unbook, remove booking, cannot attend, drop lesson

1. In **My lessons**, find the lesson.
2. Click **Cancel** next to it.
3. In the confirmation, click **Cancel booking**. Click **Keep it** if you change your mind.

The lesson becomes open again, so another student can book it. You can cancel only before the lesson starts.

## How do I move my booking to another time?

Audience: student
Keywords: reschedule, change time, move booking, change lesson, swap

You cannot change a booking. Cancel it in **My lessons**, then book the new time from **Open lessons**. Book the new lesson first if you are not sure it is still open: if the two lessons do not overlap, you can hold both for a moment.

## My lesson shows "Cancelled by teacher"

Audience: student
Keywords: cancelled by teacher, teacher cancelled, lesson cancelled, red badge, why cancelled

The teacher cancelled the lesson. This is final: you cannot book it again or cancel it yourself. Find another lesson in **Open lessons**. The app does not show why the teacher cancelled.

## How do I open a lesson for students?

Audience: teacher
Keywords: create, new lesson, add lesson, open slot, availability, schedule, make a slot, publish

1. Click **New lesson** next to the page title, at the top of **My schedule**.
2. Enter the **Subject**, the **Date**, the **Start time** and the **Duration**. Times are in your timezone.
3. Click **Open lesson**.

The lesson shows in **My schedule** with the **Open** badge, and students can book it at once.

## What are the rules for a new lesson?

Audience: teacher
Keywords: duration, length, minutes, limits, subject length, how long, rules, validation

- The lesson must start in the future.
- The duration is 15 to 240 minutes. The form offers 15, 30, 45, 60, 90, 120, 180 and 240 minutes; the default is 60.
- The subject has 1 to 100 characters.
- The lesson must not overlap another lesson of yours that is not cancelled.

## It says my lesson overlaps another lesson

Audience: teacher
Keywords: overlap, overlaps your lesson, conflict, same time, clash, cannot create

You already have a lesson at that time. The message names it and gives its start time. Choose another start time or a shorter duration. A cancelled lesson does not block the time.

## It says "A lesson must start in the future"

Audience: teacher
Keywords: past, past date, start time, in the future, cannot create

The date and start time you entered have already passed in your timezone. Pick a later date or time. Check that the time is in your own timezone, not in your student's.

## How do I change or edit a lesson?

Audience: teacher
Keywords: edit, change, modify, update, rename, reschedule, move lesson, change subject

You cannot edit a lesson. Open a new lesson with the correct details, then cancel the old one. If a student booked the old lesson, tell them about the new one, because the cancellation does not move their booking.

## How do I cancel a lesson?

Audience: teacher
Keywords: cancel, delete, remove lesson, cannot teach, call off

1. In **My schedule**, find the lesson.
2. Click **Cancel** next to it.
3. In the confirmation, click **Cancel lesson**. Click **Keep it** if you change your mind.

This is final and cannot be undone. If a student booked the lesson, they see it as **Cancelled by teacher**. You can cancel only before the lesson starts.

## How do I see who booked my lesson?

Audience: teacher
Keywords: who booked, student name, booked by, attendees, my students

In **My schedule**, a booked lesson has the badge **Booked by** and the student's name. A lesson that nobody booked has the **Open** badge.

## How do I see past lessons?

Audience: everyone
Keywords: history, past lessons, previous, old lessons, finished, completed

The dashboard shows only lessons that have not ended yet. Ask the assistant, for example "Show my past lessons", to see lessons that have ended.

## What timezone are lesson times in?

Audience: everyone
Keywords: timezone, time zone, wrong time, hour off, different time, local time, daylight saving

All times on the dashboard and in the assistant are in the timezone of your browser. Each person sees the same lesson in their own local time, so a teacher and a student in different countries see different clock times for the same lesson. If the times look wrong, check the timezone setting of your device, then reload the page.

## Will I get a reminder or an email about my lesson?

Audience: everyone
Keywords: email, notification, reminder, alert, calendar invite, notify

No. The app does not send emails, reminders or calendar invites. Check the dashboard for your lessons and any changes.

## The dashboard does not show the latest changes

Audience: everyone
Keywords: not updating, refresh, stale, old data, reload, sync, missing change

The dashboard does not update by itself when another person makes a change. Click the refresh button (the circular arrow next to the page title) to load the latest lessons. Your own changes, on the dashboard or through the assistant, show at once.

## It says "Could not load your lessons"

Audience: everyone
Keywords: error, cannot load, not loading, broken, down, failed, unavailable

The dashboard cannot reach the lessons service. Click **Try again**. If the error stays, sign out and sign in again. If it still stays, the service is down: try again later or tell the administrator.

## How do I use the assistant?

Audience: everyone
Keywords: chat, assistant, bot, help, ask assistant, chatbot

Click **Ask assistant** in the bottom-right corner. Ask a question about the app, or ask it to do something, for example "Find me a lesson tomorrow" or "Open a lesson on Friday at 10:00". Click **New chat** at the top of the panel to start a new conversation.

## Why does the assistant ask me to approve an action?

Audience: everyone
Keywords: approve, reject, approval, confirmation, card, permission, nothing happened

The assistant never changes a lesson without your approval. Before it books, opens or cancels a lesson, it shows a card with the action. Click **Approve** to do it, or **Reject** to stop it; after **Reject** nothing changes. You cannot send a new message until you approve or reject. To change the action, reject it and say what should change.

## The assistant says my session expired, or asks me to send the message again

Audience: everyone
Keywords: session expired, not signed in, unauthorized, send again, 401, token

Your sign-in session renews every minute. A long request can outlast it. Send the same message again. If it happens every time, reload the page or sign in again.
