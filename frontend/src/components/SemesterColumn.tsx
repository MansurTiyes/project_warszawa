import type { SemesterPlan } from "@/types";
import { CourseBlock } from "@/components/CourseBlock";

type Props = {
  semester: SemesterPlan;
  inProgress: boolean;
};

export function SemesterColumn({ semester, inProgress }: Props) {
  const dim = semester.is_completed;
  const ip = inProgress;

  const headerBg = ip
    ? "var(--gold-soft)"
    : dim
      ? "var(--bg-3)"
      : "var(--cardinal-bright)";
  const headerFg = ip ? "#1a1714" : "white";

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        flex: 1,
        minWidth: 0,
        position: "relative",
        outline: ip ? "2px solid var(--gold-soft)" : "none",
        outlineOffset: ip ? -1 : 0,
        boxShadow: ip ? "0 0 0 4px rgba(212,160,23,0.12)" : "none",
        borderRadius: ip ? 4 : 0,
      }}
    >
      {ip && (
        <div
          style={{
            position: "absolute",
            top: -10,
            left: "50%",
            transform: "translateX(-50%)",
            background: "var(--gold-soft)",
            color: "#1a1714",
            fontFamily: "var(--font-sans)",
            fontSize: 9,
            fontWeight: 700,
            letterSpacing: "0.12em",
            textTransform: "uppercase",
            padding: "2px 8px",
            borderRadius: 999,
            zIndex: 2,
            whiteSpace: "nowrap",
          }}
        >
          ● In progress
        </div>
      )}

      <div
        style={{
          background: headerBg,
          color: headerFg,
          fontFamily: "var(--font-sans)",
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: "0.1em",
          textTransform: "uppercase",
          textAlign: "center",
          padding: "8px 6px",
        }}
      >
        {semester.label}
      </div>

      <div
        style={{
          display: "flex",
          flexDirection: "column",
          gap: 2,
          background: "var(--bg-0)",
          padding: 2,
        }}
      >
        {semester.courses.map((c, i) => (
          <CourseBlock key={`${c.course_code}-${i}`} course={c} dim={dim} />
        ))}
      </div>
    </div>
  );
}
