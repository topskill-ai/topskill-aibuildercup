import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Avatar } from "./Avatar";

describe("Avatar", () => {
  it("solid without an accent draws initials on the theme (cta) gradient", () => {
    render(<Avatar solid name="Jane Austen" size={34} />);
    const monogram = screen.getByRole("img", { name: "Jane Austen" });
    expect(monogram).toHaveTextContent("JA");
    const disc = monogram.parentElement!;
    expect(disc.className).toContain("from-cta");
    expect(disc.className).toContain("text-primary-fg");
  });

  it("solid with an accent uses the accent fill and on-accent text", () => {
    render(<Avatar solid name="Tax Helper" accent="violet" />);
    const disc = screen.getByRole("img", { name: "Tax Helper" }).parentElement!;
    expect(disc.className).toContain("bg-accent-violet");
    expect(disc.className).toContain("text-on-accent");
    expect(disc.className).not.toContain("from-cta");
  });

  it("falls back to initials when the photo fails to load", () => {
    render(<Avatar solid name="Jane Austen" src="https://example.test/jane.png" />);
    const photo = screen.getByRole("img", { name: "Jane Austen" });
    expect(photo.tagName).toBe("IMG");
    fireEvent.error(photo);
    const monogram = screen.getByRole("img", { name: "Jane Austen" });
    expect(monogram.tagName).toBe("SPAN");
    expect(monogram).toHaveTextContent("JA");
  });
});
