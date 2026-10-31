export function canContinueGrade(grade: string): boolean {
  return Number.isInteger(grade) && Number(grade) >= 1 && Number(grade) <= 6;
}
