import type { Role } from "@/api/types";
import { config } from "./config";
import { safeLocal } from "./safeStorage";

/** Developer login identity (SPEC §4.2, §11.5). Only honoured when VITE_DEV_AUTH=1. */
export interface DevUser {
  uid: string;
  role: Role;
  email?: string;
}

export const DEV_USER_KEY = "topskill.devUser";
export const DEV_UID_RE = /^[A-Za-z0-9_-]{3,64}$/;

export function getDevUser(): DevUser | null {
  if (!config.devAuth) return null;
  const value = safeLocal.getJSON<DevUser>(DEV_USER_KEY);
  if (!value || typeof value.uid !== "string" || !DEV_UID_RE.test(value.uid)) return null;
  const role: Role = value.role === "creator" ? "creator" : "visitor";
  return { uid: value.uid, role, email: typeof value.email === "string" ? value.email : undefined };
}

export function setDevUser(user: DevUser | null): void {
  if (user) safeLocal.setJSON(DEV_USER_KEY, user);
  else safeLocal.remove(DEV_USER_KEY);
}
