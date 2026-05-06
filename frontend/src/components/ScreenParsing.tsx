function Spinner({ size = 28 }: { size?: number }) {
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        border: "2px solid var(--line-1)",
        borderTopColor: "var(--cardinal-bright)",
        animation: "spin 1s linear infinite",
      }}
    >
      <style>{`@keyframes spin { to { transform: rotate(360deg); } }`}</style>
    </div>
  );
}

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
