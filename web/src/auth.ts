import { useEffect, useState } from "react";
import { api } from "./api";

export type Me = {
  accounts_on: boolean;
  user: { id: number; username: string; role: string } | null;
  local: boolean;
  public_map: boolean;
  can_change: boolean;
  is_admin: boolean;
};

let current: Me | null = null;
let checking = false;
window.addEventListener("notoryx-signin", () => {
  // A request was refused: find out whether the sign-in ended.
  if (checking) return;
  checking = true;
  refreshMe().finally(() => (checking = false));
});
const subs = new Set<(m: Me | null) => void>();

export function refreshMe(): Promise<Me | null> {
  return api<Me>("/api/auth/me")
    .then((m) => {
      current = m;
      subs.forEach((f) => f(m));
      return m;
    })
    .catch(() => current);
}

/** Who is signed in. Refreshed on load, after sign-in/out, and when the API says to sign in. */
export function useMe(): Me | null {
  const [me, setMe] = useState<Me | null>(current);
  useEffect(() => {
    subs.add(setMe);
    if (!current) refreshMe();
    return () => {
      subs.delete(setMe);
    };
  }, []);
  return me;
}

export function signOut() {
  return api("/api/auth/logout", { method: "POST" }).finally(() => {
    current = null;
    refreshMe();
  });
}
