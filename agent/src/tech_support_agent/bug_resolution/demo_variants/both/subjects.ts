export type Subject = "Math" | "Reading" | "Both";

export function summarySubjects(subject: Subject): string[] {
  return subject === "Both" ? ["Math"] : [subject];
}
