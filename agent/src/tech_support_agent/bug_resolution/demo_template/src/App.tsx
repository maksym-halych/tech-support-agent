import { useState } from "react";
import { canContinueGrade } from "./grade";
import { summarySubjects, type Subject } from "./subjects";

const steps = ["Subject", "Grade", "Goal", "Summary"];

export function App() {
  const [step, setStep] = useState(0);
  const [subject, setSubject] = useState<Subject | "">("");
  const [grade, setGrade] = useState("");
  const [goal, setGoal] = useState("");
  const nextEnabled =
    step === 0 ? !!subject : step === 1 ? canContinueGrade(grade) : !!goal;

  function restart() {
    setStep(0);
    setSubject("");
    setGrade("");
    setGoal("");
  }

  return (
    <>
      <header>
        <span className="brand">
          Learning <i>Lab</i>
          <span className="sun" aria-hidden="true">
            *
          </span>
        </span>
        <span className="demo-badge">INTERACTIVE DEMO</span>
      </header>
      <main>
        <aside>
          <p className="eyebrow">A LITTLE CURIOSITY GOES A LONG WAY</p>
          <h1>
            Small steps.
            <br />
            <em>Bright discoveries.</em>
          </h1>
          <p className="intro">
            Explore a learning journey in a few simple choices. A little math, a
            little reading, a world of possibility.
          </p>
          <div className="illustration" aria-hidden="true">
            <span>2 + 3</span>
            <span>Aa</span>
            <span>
              YOU
              <br />
              CAN.
            </span>
          </div>
          <p className="disclaimer">
            A sample quiz, not an enrollment form.
            <br />
            No account, payment, or personal details needed.
          </p>
        </aside>
        <section className="quiz" aria-label="Learning quiz">
          <ol className="progress">
            {steps.map((label, i) => (
              <li key={label} aria-current={step === i ? "step" : undefined}>
                <span>{i + 1}</span>
                {label}
              </li>
            ))}
          </ol>
          <p className="eyebrow">
            {step === 3 ? "YOUR DEMO CHOICES" : `STEP ${step + 1} OF 3`}
          </p>
          {step === 0 && (
            <>
              <h2>What sparks your curiosity?</h2>
              <p>Choose a subject to explore.</p>
              <div className="choices">
                {(["Math", "Reading", "Both"] as Subject[]).map((value) => (
                  <button
                    type="button"
                    className="choice"
                    aria-pressed={subject === value}
                    key={value}
                    onClick={() => setSubject(value)}
                  >
                    <strong>{value}</strong>
                    <span>
                      {value === "Math"
                        ? "Numbers, patterns & possibilities"
                        : value === "Reading"
                          ? "Words, stories & new worlds"
                          : "Math + Reading"}
                    </span>
                  </button>
                ))}
              </div>
            </>
          )}
          {step === 1 && (
            <>
              <h2>Find your starting point.</h2>
              <p>Pick a grade. Every learner starts somewhere.</p>
              <label htmlFor="grade">Grade</label>
              <select
                id="grade"
                value={grade}
                onChange={(e) => setGrade(e.target.value)}
              >
                <option value="">Choose a grade</option>
                {[1, 2, 3, 4, 5, 6].map((n) => (
                  <option key={n} value={String(n)}>
                    Grade {n}
                  </option>
                ))}
              </select>
              <p className="selection">Subject: {subject}</p>
            </>
          )}
          {step === 2 && (
            <>
              <h2>What would you like to work on?</h2>
              <p>A small goal is a great beginning.</p>
              <label htmlFor="goal">Learning goal</label>
              <select
                id="goal"
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
              >
                <option value="">Choose a goal</option>
                <option>Build confidence</option>
                <option>Practice the basics</option>
                <option>Explore new challenges</option>
              </select>
            </>
          )}
          {step === 3 && (
            <>
              <div className="complete-mark" aria-hidden="true">
                *
              </div>
              <h2>Demo complete!</h2>
              <p>Here is a summary of your choices.</p>
              <dl>
                <dt>Subjects</dt>
                <dd>{subject && summarySubjects(subject).join(" + ")}</dd>
                <dt>Grade</dt>
                <dd>Grade {grade}</dd>
                <dt>Goal</dt>
                <dd>{goal}</dd>
              </dl>
              <p className="notice">
                This is a demonstration only. No enrollment or learning plan has
                been created.
              </p>
            </>
          )}
          <div className="actions">
            {step > 0 && step < 3 && (
              <button
                type="button"
                className="back"
                onClick={() => setStep(step - 1)}
              >
                Back
              </button>
            )}
            {step < 3 ? (
              <button
                type="button"
                className="continue"
                disabled={!nextEnabled}
                onClick={() => setStep(step + 1)}
              >
                {step === 2 ? "See demo summary" : "Continue"}
              </button>
            ) : (
              <button type="button" className="continue" onClick={restart}>
                Start again
              </button>
            )}
          </div>
          <p className="privacy">Just a demo. Your choices stay in this tab.</p>
        </section>
      </main>
      <footer>
        LEARNING LAB <span>Built for curiosity. For demonstration only.</span>
      </footer>
    </>
  );
}
