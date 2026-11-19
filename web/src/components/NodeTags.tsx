import { useEffect, useState } from "react";
import { duration, ignoreNode, knownTime, unignoreNode } from "../api";

/** "stale · 3 d" with a clock icon, so staleness never rests on colour alone. */
export function StaleTag({ stale, lastHeard }: { stale?: boolean; lastHeard?: number | null }) {
  const heard = knownTime(lastHeard);
  if (!stale || heard == null) return null;
  const age = duration(Date.now() / 1000 - heard);
  return (
    <span className="pill stale" title={`Not heard for ${age}`}>
      <svg viewBox="0 0 16 16" width="11" height="11" aria-hidden="true" focusable="false">
        <circle cx="8" cy="8" r="6.5" fill="none" stroke="currentColor" strokeWidth="1.6" />
        <path d="M8 4.2V8l2.6 1.6" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
      stale · {age}
    </span>
  );
}

/** Ignore / Stop ignoring. Calls onChange after the station has saved it. */
export function IgnoreButton({
  publicKey,
  name,
  ignored = false,
  onChange,
  small = false,
}: {
  publicKey: string;
  name?: string | null;
  ignored?: boolean;
  onChange?: (ignored: boolean) => void;
  small?: boolean;
}) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [on, setOn] = useState(ignored);
  useEffect(() => setOn(ignored), [ignored]);
  const label = name || publicKey.slice(0, 8);
  return (
    <>
      <button
        type="button"
        className={small ? "small-btn" : undefined}
        disabled={busy}
        title={on ? `Show ${label} again` : `Hide ${label} from the map, lists, analytics and alerts. Its packets are still stored.`}
        aria-label={on ? `Stop ignoring ${label}` : `Ignore ${label}`}
        onClick={(e) => {
          e.stopPropagation();
          setBusy(true);
          setErr(null);
          (on ? unignoreNode(publicKey) : ignoreNode(publicKey, name))
            .then(() => {
              setOn(!on);
              onChange?.(!on);
            })
            .catch((x) => setErr(x.message))
            .finally(() => setBusy(false));
        }}
      >
        {on ? "Stop ignoring" : "Ignore"}
      </button>
      {err && <span className="err small" role="alert"> {err}</span>}
    </>
  );
}
