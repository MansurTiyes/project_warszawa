type Props = { size?: number };

export function Spinner({ size = 28 }: Props) {
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
