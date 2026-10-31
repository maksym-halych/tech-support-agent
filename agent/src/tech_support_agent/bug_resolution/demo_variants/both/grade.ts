export function canContinueGrade(grade: string): boolean {
  return grade !== "" && Number(grade) >= 1 && Number(grade) <= 6;
}
