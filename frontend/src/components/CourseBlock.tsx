import type { PlannedCourse } from "@/types";
import { categoryColor } from "@/lib/schedule-display";

type Props = {
  course: PlannedCourse;
  dim: boolean;
};

export function CourseBlock({ course, dim }: Props) {
  const isPlaceholder = course.is_placeholder;
  const bg = isPlaceholder ? "var(--gold-soft)" : categoryColor(course.category);
  const fg = isPlaceholder ? "#1a1714" : "white";

  return (
    <div
      style={{
        position: "relative",
        background: bg,
        color: fg,
        padding: "10px 12px 14px",
        minHeight: 72,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        opacity: dim ? 0.42 : 1,
        filter: dim ? "saturate(0.5)" : "none",
      }}
    >
      <div
        style={{
          fontFamily: "var(--font-sans)",
          fontSize: 12.5,
          fontWeight: 600,
          letterSpacing: "0.02em",
          textDecoration: dim ? "line-through" : "none",
          textDecorationColor: "rgba(255,255,255,0.5)",
        }}
      >
        {course.course_code}
      </div>

      {!isPlaceholder && course.name && (
        <div
          style={{
            fontFamily: "var(--font-serif)",
            fontStyle: "italic",
            fontSize: 10.5,
            opacity: 0.85,
            marginTop: 3,
            lineHeight: 1.25,
          }}
        >
          {course.name}
        </div>
      )}

      {isPlaceholder && (
        <div
          style={{
            fontFamily: "var(--font-sans)",
            fontSize: 9.5,
            fontWeight: 600,
            opacity: 0.7,
            marginTop: 3,
            letterSpacing: "0.05em",
          }}
        >
          OPTIONAL
        </div>
      )}

      <div
        style={{
          position: "absolute",
          bottom: 4,
          right: 6,
          fontFamily: "var(--font-mono)",
          fontSize: 9,
          opacity: 0.6,
        }}
      >
        {course.units}
      </div>
    </div>
  );
}
