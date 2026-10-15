import { clsx, type ClassValue } from "clsx";

/**
 * Join class names conditionally (thin wrapper over clsx so every lane imports one helper).
 *
 * There is no tailwind-merge: a `className` that sets the same utility as a component's variant
 * (e.g. a background or text colour on `<Button variant="primary">`) does not reliably win. Pick a
 * variant that already has the look you need instead of overriding its colours.
 */
export function cn(...inputs: ClassValue[]): string {
  return clsx(inputs);
}
