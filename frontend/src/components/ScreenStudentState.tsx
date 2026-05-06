import type { StudentState } from "@/types";
import { requirementsSummary, unitsBreakdown } from "@/lib/audit";

type Props = {
  student_state: StudentState;
  onContinue?: () => void;
};

const STATUS_META: Record<
  "met" | "in_progress" | "unmet",
  { dot: string; label: string }
> = {
  met: { dot: "var(--cat-enrichment)", label: "Met" },
  in_progress: { dot: "var(--gold-soft)", label: "In progress" },
  unmet: { dot: "var(--cardinal-bright)", label: "Open" },
};

function Eyebrow({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: 10,
        color: "var(--ink-3)",
        textTransform: "uppercase",
        letterSpacing: "0.12em",
      }}
    >
      {children}
    </div>
  );
}

export function ScreenStudentState({ student_state, onContinue }: Props) {
  const s = student_state;
  const u = unitsBreakdown(s);
  const reqs = requirementsSummary(s);
  const metCount = reqs.filter((r) => r.status === "met").length;
  const openReqs = reqs.length - metCount;
  const firstName = s.name.split(" ")[0];

  return (
    <div style={{ minHeight: "100vh", display: "flex" }}>
      <main
        style={{
          flex: 1,
          padding: "64px 80px 56px",
          display: "flex",
          flexDirection: "column",
          gap: 32,
          overflow: "auto",
        }}
      >
        {/* Hero */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1fr auto",
            alignItems: "end",
            gap: 32,
          }}
        >
          <div>
            <h1
              className="h-display"
              style={{
                fontSize: 56,
                margin: "0 0 10px",
                letterSpacing: "-0.03em",
                lineHeight: 1,
              }}
            >
              Hi, <em>{firstName}.</em>
            </h1>
            <div style={{ display: "flex", gap: 28, flexWrap: "wrap" }}>
              {(
                [
                  [s.class_level, "Level"],
                  [`${s.degree}, ${s.major}`, "Program"],
                  [s.entrance_term, "Entered"],
                  [s.expected_graduation, "Graduating"],
                ] as const
              ).map(([v, k]) => (
                <div key={k}>
                  <div
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 9,
                      color: "var(--ink-3)",
                      textTransform: "uppercase",
                      letterSpacing: "0.12em",
                      marginBottom: 4,
                    }}
                  >
                    {k}
                  </div>
                  <div style={{ fontSize: 14, color: "var(--ink-1)" }}>{v}</div>
                </div>
              ))}
            </div>
          </div>

          <div
            style={{
              width: 160,
              height: 160,
              borderRadius: "50%",
              border: "1px solid var(--line-2)",
              display: "grid",
              placeItems: "center",
              background:
                "radial-gradient(circle at 35% 30%, rgba(212,160,23,0.08), transparent 70%)",
              flexShrink: 0,
            }}
          >
            <div style={{ textAlign: "center" }}>
              <div
                style={{
                  fontFamily: "var(--font-serif)",
                  fontSize: 64,
                  lineHeight: 1,
                  letterSpacing: "-0.04em",
                  color: "var(--ink-0)",
                }}
              >
                {u.remainingSemesters}
              </div>
              <div
                style={{
                  marginTop: 6,
                  fontFamily: "var(--font-mono)",
                  fontSize: 9,
                  color: "var(--ink-3)",
                  textTransform: "uppercase",
                  letterSpacing: "0.12em",
                }}
              >
                Semesters left
              </div>
            </div>
          </div>
        </div>

        {/* Units progress */}
        <div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "baseline",
              marginBottom: 16,
            }}
          >
            <div
              style={{
                fontFamily: "var(--font-serif)",
                fontSize: 52,
                letterSpacing: "-0.025em",
                lineHeight: 1,
                whiteSpace: "nowrap",
              }}
            >
              {u.cumulative}
              <span style={{ color: "var(--ink-3)", fontSize: 30 }}>
                {" "}
                / {u.total} units
              </span>
            </div>
            <div style={{ display: "flex", gap: 40, alignItems: "flex-end" }}>
              {(
                [
                  ["Earned", u.earned, "var(--cardinal-bright)"],
                  ["In progress", u.in_process, "var(--gold-soft)"],
                  ["Still needed", u.needed, null],
                  ["Transfer", u.transfer, null],
                ] as const
              ).map(([k, v, swatch]) => (
                <div key={k} style={{ textAlign: "right" }}>
                  <div
                    style={{
                      fontFamily: "var(--font-mono)",
                      fontSize: 10,
                      color: "var(--ink-3)",
                      textTransform: "uppercase",
                      letterSpacing: "0.1em",
                      marginBottom: 6,
                      display: "flex",
                      alignItems: "center",
                      gap: 6,
                      justifyContent: "flex-end",
                    }}
                  >
                    {swatch && (
                      <span
                        style={{
                          width: 8,
                          height: 8,
                          borderRadius: 2,
                          background: swatch,
                        }}
                      />
                    )}
                    {k}
                  </div>
                  <div
                    style={{
                      fontFamily: "var(--font-serif)",
                      fontSize: 26,
                      letterSpacing: "-0.02em",
                    }}
                  >
                    {v}
                  </div>
                </div>
              ))}
            </div>
          </div>
          <div
            style={{
              position: "relative",
              height: 8,
              background: "var(--bg-3)",
              borderRadius: 4,
              overflow: "hidden",
            }}
          >
            <div
              style={{
                position: "absolute",
                left: 0,
                top: 0,
                height: "100%",
                width: `${u.pctDone}%`,
                background: "var(--cardinal-bright)",
              }}
            />
            <div
              style={{
                position: "absolute",
                left: `${u.pctDone}%`,
                top: 0,
                height: "100%",
                width: `${u.pctInProgress}%`,
                background: "var(--gold-soft)",
                opacity: 0.85,
              }}
            />
          </div>
        </div>

        {/* Two-column: requirements grid + side stats */}
        <div
          style={{
            display: "grid",
            gridTemplateColumns: "1.6fr 1fr",
            gap: 48,
          }}
        >
          {/* Major requirements */}
          <div>
            <div
              style={{
                display: "flex",
                alignItems: "baseline",
                justifyContent: "space-between",
                marginBottom: 18,
              }}
            >
              <Eyebrow>Major requirements</Eyebrow>
              <div
                style={{
                  fontFamily: "var(--font-mono)",
                  fontSize: 10,
                  color: "var(--ink-2)",
                  letterSpacing: "0.08em",
                  textTransform: "uppercase",
                }}
              >
                {metCount} met · {openReqs} open
              </div>
            </div>

            <div
              style={{
                display: "grid",
                gridTemplateColumns: "1fr 1fr",
                gap: "0 32px",
              }}
            >
              {reqs.map((r) => {
                const meta = STATUS_META[r.status];
                return (
                  <div
                    key={r.key}
                    style={{
                      display: "grid",
                      gridTemplateColumns: "auto 1fr auto",
                      alignItems: "center",
                      gap: 12,
                      padding: "14px 0",
                      borderBottom: "1px solid var(--line-1)",
                    }}
                  >
                    <span
                      style={{
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        background: meta.dot,
                      }}
                    />
                    <div style={{ minWidth: 0 }}>
                      <div
                        style={{
                          fontFamily: "var(--font-serif)",
                          fontSize: 17,
                          letterSpacing: "-0.015em",
                          color: "var(--ink-0)",
                        }}
                      >
                        {r.label}
                      </div>
                      <div
                        style={{
                          marginTop: 4,
                          fontFamily: "var(--font-mono)",
                          fontSize: 11,
                          color: "var(--ink-3)",
                          whiteSpace: "nowrap",
                          overflow: "hidden",
                          textOverflow: "ellipsis",
                        }}
                      >
                        {r.courses.length > 0 ? r.courses.join(" · ") : "—"}
                      </div>
                    </div>
                    <div
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: 11,
                        color: r.needed > 0 ? "var(--gold-soft)" : "var(--ink-3)",
                        textAlign: "right",
                        whiteSpace: "nowrap",
                      }}
                    >
                      {r.needed > 0 ? `${r.needed} to go` : meta.label.toLowerCase()}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Side: residency, upper-div, writing/GE */}
          <div style={{ display: "flex", flexDirection: "column", gap: 28 }}>
            <div>
              <Eyebrow>Residency</Eyebrow>
              <div
                style={{
                  fontFamily: "var(--font-serif)",
                  fontSize: 26,
                  letterSpacing: "-0.02em",
                  marginTop: 8,
                  color: s.residency_satisfied
                    ? "var(--ink-0)"
                    : "var(--gold-soft)",
                }}
              >
                {s.residency_satisfied ? "Satisfied" : "Not yet"}
              </div>
              <div
                style={{
                  marginTop: 6,
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--ink-3)",
                }}
              >
                {s.residency_units_earned} earned ·{" "}
                {s.residency_units_in_process} in progress
              </div>
            </div>

            <div>
              <Eyebrow>Upper division</Eyebrow>
              <div
                style={{
                  fontFamily: "var(--font-serif)",
                  fontSize: 26,
                  letterSpacing: "-0.02em",
                  marginTop: 8,
                }}
              >
                {s.upper_division_units_earned + s.upper_division_units_in_process}
                <span style={{ color: "var(--ink-3)", fontSize: 18 }}>
                  {" "}
                  earned + in flight
                </span>
              </div>
              <div
                style={{
                  marginTop: 10,
                  height: 4,
                  borderRadius: 2,
                  background: "var(--bg-3)",
                  overflow: "hidden",
                  position: "relative",
                }}
              >
                {(() => {
                  const total =
                    s.upper_division_units_earned +
                    s.upper_division_units_in_process +
                    s.upper_division_units_needed;
                  const e = total > 0 ? (s.upper_division_units_earned / total) * 100 : 0;
                  const ip = total > 0 ? (s.upper_division_units_in_process / total) * 100 : 0;
                  return (
                    <>
                      <div
                        style={{
                          position: "absolute",
                          left: 0,
                          top: 0,
                          height: "100%",
                          width: `${e}%`,
                          background: "var(--cardinal-bright)",
                        }}
                      />
                      <div
                        style={{
                          position: "absolute",
                          left: `${e}%`,
                          top: 0,
                          height: "100%",
                          width: `${ip}%`,
                          background: "var(--gold-soft)",
                          opacity: 0.85,
                        }}
                      />
                    </>
                  );
                })()}
              </div>
              <div
                style={{
                  marginTop: 8,
                  fontFamily: "var(--font-mono)",
                  fontSize: 11,
                  color: "var(--ink-3)",
                }}
              >
                {s.upper_division_units_needed} units still needed
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16 }}>
              {(
                [
                  ["Writing", s.writing_requirement_satisfied],
                  ["GE seminar", s.ge_seminar_satisfied],
                ] as const
              ).map(([label, ok]) => (
                <div
                  key={label}
                  style={{
                    padding: "14px 16px",
                    border: "1px solid var(--line-1)",
                    borderRadius: 10,
                  }}
                >
                  <Eyebrow>{label}</Eyebrow>
                  <div
                    style={{
                      marginTop: 6,
                      fontFamily: "var(--font-serif)",
                      fontSize: 18,
                      letterSpacing: "-0.015em",
                      color: ok ? "var(--ink-0)" : "var(--gold-soft)",
                    }}
                  >
                    {ok ? "Satisfied" : "Not yet"}
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Continue */}
        <div
          style={{
            display: "flex",
            justifyContent: "space-between",
            alignItems: "center",
            borderTop: "1px solid var(--line-1)",
            marginTop: 4,
            paddingTop: 24,
          }}
        >
          <div style={{ fontSize: 14, color: "var(--ink-2)" }}>
            Looks right? Let's plan the rest.
          </div>
          <button
            type="button"
            className="btn btn--primary btn--lg"
            onClick={onContinue}
            disabled={!onContinue}
          >
            Continue <span className="btn__arrow">→</span>
          </button>
        </div>
      </main>
    </div>
  );
}
