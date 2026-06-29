export default function StatusDot({ online }) {
  return (
    <span
      className={`status-dot ${online ? "status-dot--online" : "status-dot--offline"}`}
      aria-label={online ? "Online" : "Offline"}
      title={online ? "Online" : "Offline"}
    />
  );
}
