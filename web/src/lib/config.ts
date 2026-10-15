/**
 * Runtime configuration read from `VITE_*` variables (SPEC §4.3).
 *
 * Defaults are the production values and every developer switch defaults to off, so a build
 * without an env file talks to prod with real Firebase sign-in only (mirrors the backend's
 * fail-closed settings, SPEC §4.1).
 */

const env = import.meta.env;

function flag(value: string | undefined): boolean {
  return value === "1" || value === "true";
}

function str(value: string | undefined, fallback: string): string {
  return value && value.trim() !== "" ? value.trim() : fallback;
}

export const PROD_API_BASE = "https://topskill-api-920441209758.asia-southeast1.run.app";

export const config = {
  apiBase: str(env.VITE_API_BASE, PROD_API_BASE).replace(/\/+$/, ""),
  firebase: {
    apiKey: str(env.VITE_FIREBASE_API_KEY, "AIzaSyBINjnUKFWfLiSPREGBXUFx0xX8Z9-BHA0"),
    authDomain: str(env.VITE_FIREBASE_AUTH_DOMAIN, "top-skill.firebaseapp.com"),
    projectId: str(env.VITE_FIREBASE_PROJECT_ID, "top-skill"),
    appId: str(env.VITE_FIREBASE_APP_ID, "1:920441209758:web:3e9d36df86419f88748c8f"),
  },
  useAuthEmulator: flag(env.VITE_USE_AUTH_EMULATOR),
  authEmulatorUrl: str(env.VITE_AUTH_EMULATOR_URL, "http://127.0.0.1:9099"),
  devAuth: flag(env.VITE_DEV_AUTH),
  googleSignIn: flag(env.VITE_GOOGLE_SIGNIN),
} as const;

export type AppConfig = typeof config;
