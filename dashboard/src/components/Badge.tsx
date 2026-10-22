export function Badge({ passed, failure }: { passed: boolean; failure?: string | null }) {
  return passed
    ? <span className="badge pass">pass</span>
    : <span className="badge fail" title={failure ?? undefined}>{failure ?? "fail"}</span>;
}
