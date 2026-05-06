import { Spinner } from "@/components/Spinner";

export function ScreenParsing() {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "grid",
        placeItems: "center",
      }}
    >
      <div style={{ textAlign: "center" }}>
        <div style={{ display: "flex", justifyContent: "center", marginBottom: 28 }}>
          <Spinner size={28} />
        </div>
        <div
          style={{
            fontFamily: "var(--font-serif)",
            fontSize: 28,
            fontStyle: "italic",
            color: "var(--ink-0)",
            letterSpacing: "-0.02em",
          }}
        >
          Reading…
        </div>
      </div>
    </div>
  );
}
